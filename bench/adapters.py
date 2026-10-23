"""Independent HTTP adapters; product internals are deliberately not imported."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from bench.common import ROOT, Warehouse
# The semantic analyst's prompts, answer contract, documentation context and bounded tool loop are shared with the
# product's "together" path (clinical_core/agere/clinical/semantic_analyst.py; stdlib + semantic_docs only).
from clinical_core.agere.clinical import semantic_analyst as _analyst
from clinical_core.agere.clinical.semantic_analyst import (  # noqa: F401 - re-exports
    CONTRACT, SEMANTIC_RULES, SEMANTIC_SQL_TOOL, _SEMANTIC_CONTEXT, semantic_context)

MODEL = 'claude-opus-5-5'
KEY_FILE = Path('/Users/mikel/insight-engine/.env')


EVALUATION_HEADERS = {'X-Agere-Evaluation': '1'}  # every benchmark request: the service learns nothing from it
FROZEN_LEARNING_HEADER = 'X-Agere-Frozen-Learning'
# The parts of a refused Agere response that say why: kept in a row's diagnostics, never printed.
KEEP_KEYS = ('status', 'error', 'reason', 'tiers_tried', 'deferred_tiers', 'stage_diagnostics', 'answer_timing',
             'background_continuation', 'new_paid_api_calls', 'planning_economics', 'cost_receipt', 'cost_telemetry',
             'evaluation', 'data_absence_established')
_SECRET_KEY = re.compile(r'(?:api[_-]?key|(?:^|_)token$|secret|password|passwd|authorization|credential|cookie)', re.I)  # not 'input_tokens'
_SECRET_TEXT = [(re.compile(r'\bsk-[A-Za-z0-9_\-]{8,}'), '[redacted]'),
                (re.compile(r'(?i)\bBearer\s+[A-Za-z0-9._~+/=\-]{8,}'), 'Bearer [redacted]'),
                (re.compile(r'(?i)\b(api[_-]?key|token|secret|password|authorization)\b(["\']?\s*[:=]\s*["\']?)[^\s,"\'}]+'), r'\1\2[redacted]')]


def scrub(value, depth=0):
    """A JSON-safe copy of a response fragment with credential-looking keys dropped and credential-looking text redacted."""
    if depth > 12:
        return None
    if isinstance(value, dict):
        return {str(k): scrub(v, depth + 1) for k, v in value.items() if not _SECRET_KEY.search(str(k))}
    if isinstance(value, (list, tuple)):
        return [scrub(v, depth + 1) for v in value[:200]]
    if isinstance(value, str):
        for pattern, repl in _SECRET_TEXT:
            value = pattern.sub(repl, value)
        return value[:2000]
    return value if value is None or isinstance(value, (bool, int, float)) else str(value)[:200]


class AgereHTTPError(RuntimeError):
    """A 4xx/5xx reply. str() stays 'HTTP <code> from <host>: ...' (what rows and logs show); .status and .body carry the
    structured JSON body (tiers_tried, stage_diagnostics, cost fields) so a refusal keeps its diagnostics."""

    def __init__(self, message, status=None, body=None):
        super().__init__(message)
        self.status, self.body = status, body or {}

    @property
    def diagnostics(self):
        return {'http_status': self.status, **self.body}


def post_json(url, body, headers=None, timeout=180):
    request = urllib.request.Request(url, data=json.dumps(body, allow_nan=False).encode(),
                                     headers={'Content-Type': 'application/json', **(headers or {})}, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        # Never print request headers, credentials or an arbitrary response body; the provider's structured error
        # type and message (no credentials) are kept so a failed run says why. An Agere refusal also keeps its
        # structured body (allowlisted keys, scrubbed) on the exception for the row's diagnostics.
        detail, kept = '', {}
        try:
            payload = json.loads(exc.read().decode('utf-8', 'replace'))
            err = payload.get('error') if isinstance(payload, dict) else None
            if isinstance(err, dict):
                detail = f": {str(err.get('type', ''))[:60]}: {str(err.get('message', ''))[:200]}"
            elif err:
                detail = f": {str(scrub(str(err)))[:200]}"
            if isinstance(payload, dict):
                kept = scrub({k: payload[k] for k in KEEP_KEYS if k in payload})
        except (ValueError, AttributeError, OSError):
            pass
        raise AgereHTTPError(f'HTTP {exc.code} from {urllib.parse.urlparse(url).hostname}{detail}', exc.code, kept) from None


def api_key(path=KEY_FILE):
    key = os.environ.get('ANTHROPIC_API_KEY')
    if key:
        return key
    for line in Path(path).read_text().splitlines():
        match = re.match(r'^\s*(?:export\s+)?ANTHROPIC_API_KEY\s*=\s*(.*?)\s*$', line)
        if match:
            value = match.group(1)
            if value[:1] in {'"', "'"} and value[-1:] == value[:1]:
                value = value[1:-1]
            else:
                value = value.split(' #', 1)[0].strip()
            if value:
                return value
    raise ValueError('ANTHROPIC_API_KEY is unavailable')


def usage_cost(usage, model=MODEL):
    prices = json.loads((ROOT.parent / 'config/agere/pricing.json').read_text())
    price = prices.get(model)
    if not price or any(type(usage.get(k)) is not int or usage[k] < 0 for k in ('input_tokens', 'output_tokens')):
        return None
    cache = usage.get('cache_creation') or {}
    five = cache.get('ephemeral_5m_input_tokens', usage.get('cache_creation_input_tokens', 0))
    hour = cache.get('ephemeral_1h_input_tokens', 0)
    counters = [five, hour, usage.get('cache_read_input_tokens', 0)]
    if any(type(n) is not int or n < 0 for n in counters):
        return None
    return (usage['input_tokens'] * price['input'] + usage['output_tokens'] * price['output'] +
            five * price['cache_write_5m'] + hour * price['cache_write_1h'] +
            counters[-1] * price['cache_read']) / 1_000_000


def diagnostics_of(response):
    """Why-and-what of one Agere reply, for a row (never printed): the tiers tried and the winner with each tier's verdict,
    the stage diagnostics, paid calls and USD, the isolation receipt. No credentials: only allowlisted keys, scrubbed."""
    response = response if isinstance(response, dict) else {}
    receipt = response.get('planner_receipt') or {}
    interp = receipt.get('interpretation') or {}
    log = interp.get('proposers') or response.get('tiers_tried') or []
    tiers = [{'tier': e.get('proposer') or e.get('tier'), 'verdict': e.get('outcome'), 'seconds': e.get('seconds'),
              **({'detail': str(e.get('detail'))[:300]} if e.get('detail') else {})} for e in log if isinstance(e, dict)][:60]
    winner = (interp.get('proposer') or {}).get('name') or interp.get('interpreter')
    out = {'tiers_tried': tiers, 'winning_tier': winner, 'route': receipt.get('route'),
           'stage_diagnostics': receipt.get('stage_diagnostics') or response.get('stage_diagnostics'),
           'deferred_tiers': receipt.get('deferred_tiers') or response.get('deferred_tiers'),
           'answer_timing': receipt.get('answer_timing') or response.get('answer_timing'),
           'cost_receipt': response.get('cost_receipt'), 'evaluation': response.get('evaluation'),
           'status': (response.get('answer') or {}).get('status') or response.get('status')}
    return scrub({k: v for k, v in out.items() if v not in (None, [], {})})


def agere(question, url='http://127.0.0.1:8885', transport=post_json, timeout=900, frozen_learning=None):
    # A slow answer is reported as latency, never scored as a failure: allow up to 15 minutes per question by default.
    # The contest harness passes its shared wall-clock limit here.
    # Always an evaluation request: the service runs it with every learning write off (X-Agere-Evaluation), so a measured
    # run never teaches the stack the test. A transport that takes no headers (a test fake) is called without them.
    headers = dict(EVALUATION_HEADERS)
    frozen = frozen_learning or os.environ.get('AGERE_FROZEN_LEARNING_ID')
    if frozen:
        headers[FROZEN_LEARNING_HEADER] = str(frozen)[:120]
    kwargs = {'timeout': timeout} if transport is post_json else {}
    if transport is post_json or getattr(transport, 'accepts_headers', False):
        kwargs['headers'] = headers
    response = transport(url.rstrip('/') + '/api/stack/compose', {'question': question['question']}, **kwargs)
    economics = response.get('planning_economics') or {}
    # Planning usage alone is insufficient if graph execution also invokes models.
    planner = response.get('planner_receipt') or {}
    cost = response.get('cost_receipt')
    if isinstance(cost, dict) and cost.get('paid_calls') is not None and cost.get('ledger_consistent'):
        # The service's per-request receipt covers every provider the ledger saw (Claude, Jev, Perplexity) and the
        # background work the answer started; it is the authority when present.
        total_calls = cost['paid_calls']
        known = cost.get('usd') is not None or total_calls == 0
        usd = 0.0 if total_calls == 0 else cost.get('usd')
        return {'answer': response.get('answer'), 'response': response, 'input_tokens': None, 'output_tokens': None,
                'estimated_cost_usd': usd if known else None,
                'cost_scope': 'request_ledger' if known else 'incomplete_product_usage',
                'known_planning_cost_usd': economics.get('known_api_estimate_usd'),
                'new_paid_api_calls': total_calls, 'model': planner.get('model'), 'error': None,
                'diagnostics': diagnostics_of(response)}
    total_calls = response.get('new_paid_api_calls')
    planner_calls = planner.get('new_paid_api_calls')
    complete = (type(total_calls) is int and type(planner_calls) is int and total_calls == planner_calls)
    if total_calls == 0:
        cost, inputs, outputs, complete = 0.0, 0, 0, True
    else:
        cost = economics.get('new_api_estimate_usd') if complete else None
        inputs = economics.get('new_input_tokens') if complete else None
        outputs = economics.get('new_output_tokens') if complete else None
    return {'answer': response.get('answer'), 'response': response, 'input_tokens': inputs,
            'output_tokens': outputs, 'estimated_cost_usd': cost,
            'cost_scope': 'all_reported_calls' if complete else 'incomplete_product_usage',
            'known_planning_cost_usd': economics.get('known_api_estimate_usd'),
            'new_paid_api_calls': total_calls, 'model': planner.get('model'), 'error': None,
            'diagnostics': diagnostics_of(response)}


SQL_TOOL = {'name': 'query_sql', 'description': 'Read-only SQLite SELECT over received warehouse records. '
            'Use JSON functions to inspect raw payloads. All rows are scoped to the original roster, '
            'release and receipt cutoff. Truncation is explicit; aggregate in SQL for complete results.',
            'input_schema': {'type': 'object', 'properties': {'sql': {'type': 'string'}},
                             'required': ['sql'], 'additionalProperties': False}}

SYSTEM = '''You are the independent analytical baseline for synthetic healthcare data.
Answer the user's actual question using only received warehouse evidence available through query_sql.
First inspect relevant payload vocabulary, coding systems, values, units and clocks. Never guess a field
or confuse no observed row with absence of disease/care. Deduplicate patients. Distinguish any observed
result from latest dated result, and event time from arrival time. Do arithmetic in SQL.
You have at most 20 SQL calls. Tools may return explicit errors/truncation; do not treat either as zero.
You may query these SQLite views (already scoped to the selected release and receipt cutoff):
patients(release,id,payload JSON), records(release,id,patient,stream,event,arrival,identity,supersedes,hash,payload JSON),
source_entities(release,kind,id,payload JSON). Records preserve literal native fields and nested JSON.
Examples of structural inspection: SELECT stream,COUNT(*) AS n FROM records GROUP BY stream;
SELECT payload FROM records WHERE stream='labs' LIMIT 3. Use :as_of for the cutoff if needed.
Only read these views; cached product outputs and hidden generator truth are unavailable.
''' + CONTRACT  # the shared answer contract (semantic_analyst.CONTRACT), reused by frontier_semantic


def budget_prompt(text, max_tools, stated):
    """The prompt states the SQL budget the loop enforces (identical wording when max_tools is the historical default)."""
    if f'at most {stated} ' not in text:
        raise ValueError('Prompt does not state its SQL budget')
    return text.replace(f'at most {stated} ', f'at most {max_tools} ', 1)


def frontier_sql(question, warehouse: Warehouse, *, key=None, model=MODEL, max_tools=20, transport=post_json,
                 max_tokens=8192, deadline=None, system=None):
    if not 1 <= max_tools <= 20:
        raise ValueError('Tool limit must be 1..20')
    user = json.dumps({'question': question['question'], 'as_of': question['as_of']})

    def execute(use):
        if use.get('name') != 'query_sql' or set(use.get('input', {})) != {'sql'}:
            raise ValueError('Only query_sql with one sql string is supported')
        return warehouse.query(use['input']['sql'], max_rows=2000, max_chars=60000)

    return _tool_loop(user, budget_prompt(system or SYSTEM, max_tools, 20), [SQL_TOOL], execute, key=key, model=model,
                      max_tools=max_tools, transport=transport, max_tokens=max_tokens, deadline=deadline)


def _sender(key, model, transport, deadline=None):
    """send(body) for the shared tool loop: one direct POST with the caller's key; cost from returned usage. With a
    deadline (time.monotonic() value), a real HTTP call's timeout is the remaining time."""
    credential = key if key is not None else api_key()

    def send(body):
        timing = {}
        if deadline is not None and getattr(transport, 'accepts_timeout', transport is post_json):
            timing = {'timeout': max(1.0, deadline - time.monotonic())}
        response = transport('https://api.anthropic.com/v1/messages', body,
                             {'x-api-key': credential, 'anthropic-version': '2023-06-01'}, **timing)
        return response, usage_cost(response.get('usage', {}), model), False
    return send


def _tool_loop(user, system, tools, execute, *, key, model, max_tools, transport, extra=None, max_tokens=8192,
               deadline=None):
    """Bounded tool-use loop shared by the frontier baselines (semantic_analyst.tool_loop): every executed call (failed
    ones included) counts toward max_tools; all returned usage is recorded, including cache writes/reads; there are no
    automatic paid retries. deadline (time.monotonic() value) bounds wall clock: no provider call or SQL call starts
    after it, and a real HTTP call's timeout is the remaining time."""
    try:
        send = _sender(key, model, transport, deadline)
    except Exception as exc:
        return {**_analyst.new_result(model), 'error': type(exc).__name__ + ': ' + str(exc)}
    return _analyst.tool_loop(user, system, tools, execute, send=send, model=model, max_tools=max_tools, extra=extra,
                              max_tokens=max_tokens, deadline=deadline)


# --- frontier_semantic: LLM + semantic layer (the Genie / Cortex Analyst shape) ---------------------------------------
# The model gets the documented semantic model (tables, column docs, concept code sets, governed metric and measure
# definitions, verified example queries) as cached system documentation and one read-only SQL tool over the semantic
# database. It never calls Agere's planner, compiler, routers, engines, verification or caches.

def frontier_semantic(question, semantic, *, key=None, model=MODEL, max_tools=8, transport=post_json):
    if not 1 <= max_tools <= 20:
        raise ValueError('Tool limit must be 1..20')
    try:
        send = _sender(key, model, transport)
    except Exception as exc:
        docs, example_ids = semantic_context(semantic)
        result = {**_analyst.new_result(model), 'error': type(exc).__name__ + ': ' + str(exc),
                  'semantic_snapshot_as_of': semantic.as_of, 'context_chars': len(docs), 'tool_call_count': 0,
                  'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0}
    else:
        result = _analyst.analyze(question['question'], question['as_of'], semantic, send=send, model=model,
                                  max_tools=max_tools)
        example_ids = result.pop('example_ids')
    result['prompt_example'] = question['id'] in example_ids
    return result


# --- frontier_canonical: LLM + canonical model (clinical_core/agere/canonical) ----------------------------------------
# Same tool loop and answer contract as frontier_semantic; the documentation is generated from the canonical database by
# clinical_core.agere.canonical.docs_gen (tables, columns, version rules, value sets, stream lineage, verified examples
# that are never drawn from heldout sets or bench/capability) and the one tool is read-only SQL over that database.

CANONICAL_SQL_TOOL = {'name': 'query_sql', 'description': 'Read-only SQLite SELECT/WITH over the canonical model tables '
                      'documented in the system prompt. :as_of is bound to the question\'s as-of date. Results are capped '
                      '(2000 rows / 60K characters) and truncation is explicit: aggregate in SQL for complete results.',
                      'input_schema': {'type': 'object', 'properties': {'sql': {'type': 'string'}},
                                       'required': ['sql'], 'additionalProperties': False}}

CANONICAL_RULES = '''You are a data analyst answering questions over a canonical analytic model of synthetic healthcare data
(SQLite), like a text-to-SQL assistant working from a published data model. The documentation below is the model:
tables with column descriptions and row counts, the version-resolution rules already applied (current versions,
claim replacements and voids, final adjudication, picked-up fills), value sets with citations, the raw-stream lineage
of each table, and verified example queries. Plan from the documentation and the examples; do not explore tables
blindly. Query only to compute the answer or to check a value you cannot get from the documentation.
Answer the question as asked: follow its literal scope (records vs patients vs visits vs claims, named fields, units,
date clocks, inclusions/exclusions). Deduplicate patients. Distinguish service/event dates from arrival
(available_date). Read money from one perspective only. Do arithmetic in SQL. You have at most 8 query_sql calls;
tools may return explicit errors or truncation, never treat either as zero.
Snapshot semantics: the canonical model is ONE snapshot (see the Snapshot line). If the question's as_of is earlier,
restrict to rows with available_date <= :as_of where a table has that column, and say in grounding that settled
per-entity rows reflect the snapshot.
'''
_CANONICAL_CONTEXT = {}


def canonical_context(canonical):
    """(documentation text, verified example IDs) for one canonical database; deterministic, cached per database."""
    from clinical_core.agere.canonical import docs_gen
    key = (str(canonical.path), canonical.meta.get('release'), canonical.meta.get('built_at'))
    cached = _CANONICAL_CONTEXT.get(key)
    if cached is None:
        text, ids, _rejected = docs_gen.build_docs(
            canonical.columns, lambda sql: canonical.query(sql, max_rows=5000, max_chars=10**6)['rows'], canonical.meta)
        cached = _CANONICAL_CONTEXT[key] = (text, ids)
    return cached


def frontier_canonical(question, canonical, *, key=None, model=MODEL, max_tools=8, transport=post_json):
    if not 1 <= max_tools <= 20:
        raise ValueError('Tool limit must be 1..20')
    docs, example_ids = canonical_context(canonical)
    system = [{'type': 'text', 'text': CANONICAL_RULES + CONTRACT},
              {'type': 'text', 'text': docs, 'cache_control': {'type': 'ephemeral'}}]
    user = json.dumps({'question': question['question'], 'as_of': question['as_of'],
                       'canonical_snapshot_as_of': canonical.as_of})

    def execute(use):
        if use.get('name') != 'query_sql' or set(use.get('input', {})) != {'sql'}:
            raise ValueError('Only query_sql with one sql string is supported')
        return canonical.query(use['input']['sql'], as_of=question['as_of'], max_rows=2000, max_chars=60000)

    result = _tool_loop(user, system, [CANONICAL_SQL_TOOL], execute, key=key, model=model, max_tools=max_tools,
                        transport=transport, extra={'cache_control': {'type': 'ephemeral'}})
    usage = result['usage']
    result.update(canonical_snapshot_as_of=canonical.as_of, prompt_example=question['id'] in example_ids,
                  context_chars=len(docs), tool_call_count=len(result['tool_calls']),
                  cache_read_input_tokens=sum(u.get('cache_read_input_tokens') or 0 for u in usage),
                  cache_creation_input_tokens=sum(u.get('cache_creation_input_tokens') or 0 for u in usage))
    return result
