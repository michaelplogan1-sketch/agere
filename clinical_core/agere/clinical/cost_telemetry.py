"""Per-question cost telemetry: question id, attempt ids, tokens, pricing version and stage timings for every answer.

conversation.handle_compose opens one Probe per question (start), marks its stages, and finishes it after the learning
hooks. While a question runs its id is the current context (CURRENT); BudgetLedger.reserve tags every paid request it
reserves (and every validated cache reuse) with that id in the ledger's call_questions table (tag), so a provider call
is attributed to the question that made it. Explicit ledger ownership survives workers that do not inherit context.
Untagged rows in an overlapping window remain candidate attempts with ambiguous ownership, never known question spend.
  attempts      every provider attempt (Claude, Jev, Perplexity): attempt id (the ledger request hash), provider, model,
                status, cache hit, input/output/cache tokens, priced USD (None when unpriced: unknown, never $0), seconds,
                and for a Claude judge reply whether its review failed. Per-question receipt fields: calls_by_model (every provider) and
                claude_calls_by_model (Claude only); sonnet_before_haiku lists Sonnet calls made before any Haiku call (the ladder forbids them).
  stages        seconds per stage (compose, capture, log, learning) plus every cascade tier's seconds.
  pricing       config/agere/pricing.json checked_at + content digest: the price table every USD figure used.
The result carries 'cost_telemetry'; answers.sqlite3 keeps one answer_costs row per question (joined to the answers
row by question_id). Telemetry is bookkeeping: it never raises into the answer path.
"""
import contextvars
import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from collections import Counter
from pathlib import Path
from .evaluation import active as _evaluating
from .. import model_policy

VERSION = 'cost-telemetry/1'
ROOT = Path(__file__).resolve().parents[3]
CURRENT = contextvars.ContextVar('agere_question_id', default=None)
OWNER = contextvars.ContextVar('agere_cost_owner', default=None)
SYSTEM_ONE = ('jev', 'perplexity', 'perplexity_agent')
LOCAL_LLM_TIERS = frozenset({'local_normalizer', 'qwen_rewrite_compiler', 'bottled_sql', 'qwen_typed_interpreter', 'local_free_sql'})
SYSTEM_ONE_TIERS = frozenset({'atomic_system_one', 'typed_decider', 'evidence_review', 'hcc_suspecting', 'terminology_mapping'})
DDL = """CREATE TABLE IF NOT EXISTS answer_costs(question_id TEXT PRIMARY KEY, at REAL, question_sha TEXT, pricing_version TEXT,
  attempt_ids TEXT, api_calls INTEGER, input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER,
  cache_write_tokens INTEGER, api_usd REAL, usd_known INTEGER, answered_by TEXT, status TEXT, stages TEXT, payload TEXT)"""
TAG_DDL = 'CREATE TABLE IF NOT EXISTS call_questions(id TEXT NOT NULL, question_id TEXT NOT NULL, at REAL, reused INTEGER, PRIMARY KEY(id, question_id))'


class _Owner:
    def __init__(self, question_id):
        self.question_id = question_id
        self.lock = threading.Lock()
        self.issues = []

    def issue(self, key, reason):
        with self.lock:
            self.issues.append({'attempt_id': key, 'reason': reason})

    def snapshot(self):
        with self.lock:
            return list(self.issues)


class QuestionLedgerPath(os.PathLike):
    """A path plus the originating question, captured before worker dispatch.

    Provider requests/cache identities still receive only their original inputs.
    This pathlike is consumed by BudgetLedger; it does not share a connection.
    """
    def __init__(self, path, question_id, owner=None):
        self.path = Path(path)
        self.question_id = question_id
        self.owner = owner

    def __fspath__(self):
        return str(self.path)

    def __str__(self):
        return str(self.path)


def bind_ledger_path(path):
    if isinstance(path, QuestionLedgerPath):
        return path  # a conflicting current scope must not silently steal ownership
    current = CURRENT.get()
    if not current:
        return path  # retain any explicit ownership already attached by the parent
    owner = OWNER.get()
    return QuestionLedgerPath(path, current, owner if owner and owner.question_id == current else None)


def inherit_context(function):
    """Carry only cost ownership; preserve the dispatcher's prior model/eval policy.

    Unwrapped executor workers start with default ContextVars. Copying the whole
    parent would also copy model selection, escalation and evaluation state,
    changing request bodies and cache keys as a side effect of bookkeeping.
    """
    parent = contextvars.Context()
    parent.run(CURRENT.set, CURRENT.get())
    parent.run(OWNER.set, OWNER.get())
    def run(*args, **kwargs):
        return parent.copy().run(function, *args, **kwargs)
    return run


def pricing_version():
    """'<checked_at>/<sha12>' of the price table every USD estimate uses."""
    path = ROOT / 'config' / 'agere' / 'pricing.json'
    try:
        raw = path.read_bytes()
        return f"{json.loads(raw).get('checked_at')}/{hashlib.sha256(raw).hexdigest()[:12]}"
    except (OSError, ValueError):
        return None


def tag(ledger, key, reused=False):
    """BudgetLedger.reserve hook: attribute a reserved (or reused) request to the current question. Never raises."""
    path = getattr(ledger, 'path', None)
    bound = getattr(path, 'question_id', None)
    qid = CURRENT.get() or bound
    if not qid: return
    owner = OWNER.get()
    if owner is None or owner.question_id != qid:
        owner = getattr(path, 'owner', None)
    if bound and bound != qid:
        for scope in {owner, getattr(path, 'owner', None)} - {None}:
            scope.issue(key, 'ledger_owner_context_conflict')
        return
    try:
        db = ledger.db
        db.execute(TAG_DDL)
        db.execute('INSERT OR IGNORE INTO call_questions VALUES (?,?,?,?)', (key, qid, time.time(), int(bool(reused))))
        db.commit()
    except Exception as exc:  # noqa: BLE001 - telemetry cannot abort an otherwise valid answer
        if owner: owner.issue(key, 'tag_write_failed:' + type(exc).__name__)
        try:
            from ..request_lineage import execution_observation
            execution_observation('cost_attribution_unavailable', request_hash=key, question_id=qid,
                                  reason='tag_write_failed', error_type=type(exc).__name__)
        except Exception:
            pass  # the scoped issue above still makes this question's accounting unknown


def _ledger_path(path=None):
    if path: return Path(path)
    from ..gateway import default_ledger_path
    return default_ledger_path()


def _ro(path):
    return sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=30)


def high_water(path):
    try:
        if not Path(path).exists(): return 0
        with _ro(path) as db:
            return db.execute('SELECT COALESCE(MAX(rowid),0) FROM calls').fetchone()[0]
    except sqlite3.Error:
        return 0


def review_verdict(text):
    """'failed' / 'passed' for a judge reply ({'equivalent', 'issues'}), None for any other reply."""
    import re
    try:
        audit = json.loads(re.sub(r'^```(?:json)?\s*|\s*```$', '', (text or '').strip()))
    except (ValueError, TypeError):
        return None
    if not isinstance(audit, dict) or 'equivalent' not in audit: return None
    from .nl2sql import review_failed
    return 'failed' if review_failed(json.dumps(audit)) else 'passed'


def attempt_of(key, provider, model, status, response, error, seconds, *, reused=False, attribution='tagged'):
    from ..economics import usage_cost
    raw = json.loads(response) if response else {}
    usage = raw.get('usage') if isinstance(raw, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    cache = usage.get('cache_creation') if isinstance(usage.get('cache_creation'), dict) else {}
    write = usage.get('cache_creation_input_tokens') or sum(v for v in cache.values() if isinstance(v, int))
    price = usage_cost(model, usage) if usage and not reused else None
    text = ''.join(b.get('text', '') for b in raw.get('content', []) if isinstance(b, dict)) if isinstance(raw, dict) else ''
    return {'attempt_id': key, 'provider': provider, 'model': model, 'status': status, 'cache_hit': bool(reused),
            'input_tokens': usage.get('input_tokens') if not reused else 0, 'output_tokens': usage.get('output_tokens') if not reused else 0,
            'cache_read_tokens': usage.get('cache_read_input_tokens', 0) if not reused else 0, 'cache_write_tokens': write if not reused else 0,
            'usd': 0.0 if reused else (price or {}).get('estimated_usd'), 'seconds': seconds, 'error': error,
            'review': review_verdict(text) if provider == 'claude' else None, 'attribution': attribution}


def attempts_since(path, hw, question_id):
    """Provider attempts of one question: rows reserved after hw tagged with it (or untagged), then its tagged cache reuses."""
    path = Path(path)
    if not path.exists(): return []
    out = []
    with _ro(path) as db:
        tagged = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE name='call_questions'")}
        rows = db.execute('SELECT rowid,id,provider,model,status,response,error,seconds FROM calls WHERE rowid>? ORDER BY rowid', (hw,)).fetchall()
        owners, reuses = {}, []
        if tagged:
            for key, qid, reused in db.execute('SELECT q.id,q.question_id,q.reused FROM call_questions q JOIN calls c ON c.id=q.id WHERE c.rowid>?', (hw,)):
                owners.setdefault(key, {})[qid]=bool(reused)
            reuses = db.execute('SELECT c.id,c.provider,c.model,c.status,c.response,c.error,c.seconds FROM call_questions q '
                                'JOIN calls c ON c.id=q.id WHERE q.question_id=? AND q.reused=1 AND c.rowid<=? ORDER BY q.at',
                                (question_id, hw)).fetchall()
    for rowid, key, provider, model, status, response, error, seconds in rows:
        who = owners.get(key)
        charge_owners = [qid for qid, reused in (who or {}).items() if not reused]
        if len(charge_owners) == 1 and question_id not in who:
            continue  # a unique explicit owner, rather than a reuse tag, excludes this call
        ambiguity = len(charge_owners) != 1
        out.append(attempt_of(key, provider, model, status, response, error, seconds,
                              reused=bool(who and who.get(question_id)),
                              attribution='window' if not who else 'shared_unresolved' if ambiguity else 'tagged'))
    out += [attempt_of(*r, reused=True) for r in reuses]
    return out


def opus_without_failed_sonnet(attempts):
    """Opus calls not preceded (in this question) by a Sonnet review that failed: Mike's escalation rule says these are failures."""
    bad, failed_review = [], False
    for a in attempts:
        if a['cache_hit'] or a['provider'] != 'claude': continue
        model = a['model'] or ''
        if 'sonnet' in model and a['review'] == 'failed': failed_review = True
        if 'opus' in model and not failed_review: bad.append(a['attempt_id'])
    return bad


def sonnet_before_haiku(attempts):
    """Sonnet calls made before any Haiku call of this question: the ladder (model_policy) says Sonnet is only ever the escalation after a Haiku
    attempt, so each of these is a policy violation. Cache hits are free and do not count."""
    bad, haiku_seen = [], False
    for a in attempts:
        if a['cache_hit'] or a['provider'] != 'claude': continue
        model = a['model'] or ''
        if 'haiku' in model: haiku_seen = True
        elif 'sonnet' in model and not haiku_seen: bad.append(a['attempt_id'])
    return bad


def summarize(attempts, result=None, error=None):
    interp = (((result or {}).get('planner_receipt') or {}).get('interpretation') or {})
    answered_by = interp.get('interpreter') or ((result or {}).get('planner_receipt') or {}).get('route')
    ambiguous = [a for a in attempts if a.get('attribution') in ('window', 'shared_unresolved')]
    owned = [a for a in attempts if a.get('attribution') not in ('window', 'shared_unresolved')]
    new = [a for a in owned if not a['cache_hit']]
    priced = [a['usd'] for a in new]
    calls_by_model = Counter(a['model'] for a in new)
    s1 = [a for a in new if a['provider'] in SYSTEM_ONE]
    claude = [a for a in new if a['provider'] == 'claude']
    claude_calls_by_model = Counter(a['model'] for a in claude)
    tokens = {k: sum(a.get(k) or 0 for a in new) for k in ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens')}
    routing = interp.get('system_one_routing') or {}
    s1_decided = []
    if answered_by in SYSTEM_ONE_TIERS: s1_decided.append(f'answer:{answered_by}')
    if interp.get('partial_reason') == 'system_one_fallback_provider_unavailable': s1_decided.append('fallback_pick')
    if routing.get('applied'): s1_decided.append(f"route:{routing.get('label')}")
    checks = ((result or {}).get('answer_checks') or {}).get('system_one')
    if isinstance(checks, dict) and checks.get('label'): s1_decided.append(f"check:{checks['label']}")
    status = ((result or {}).get('answer') or {}).get('status') or ('error' if error else None)
    known = round(sum(p or 0.0 for p in priced), 6)
    return {'answered_by': answered_by, 'status': status, 'api_calls': None if ambiguous else len(new),
            'api_usd': None if ambiguous else known, 'usd_known': not ambiguous and all(p is not None for p in priced),
            'ownership_complete': not ambiguous, 'attributed_paid_calls': len(new),
            'known_owned_api_estimate_usd': known, 'ambiguous_attempt_ids': [a['attempt_id'] for a in ambiguous],
            'calls_by_model': dict(calls_by_model), 'claude_calls_by_model': dict(claude_calls_by_model), 'claude_calls': len(claude), 'system_one_calls': len(s1),
            'tokens': tokens, 'no_paid_call': None if ambiguous else not new, 'local_llm': answered_by in LOCAL_LLM_TIERS,
            'llm_free': None if ambiguous else not new and answered_by not in LOCAL_LLM_TIERS,
            'system_one_decided': s1_decided, 'opus_without_failed_sonnet': opus_without_failed_sonnet(owned), 'sonnet_before_haiku': sonnet_before_haiku(owned),
            'cascade_usage': interp.get('cascade_usage')}


class Probe:
    """One question's telemetry. start() -> mark(stage)... -> finish(result|error) -> dict (also on result['cost_telemetry'])."""

    def __init__(self, question, *, ledger_path=None, question_id=None):
        self.question = str(question or '')
        self.question_id = question_id or 'q-' + uuid.uuid4().hex[:24]
        self.ledger_path = _ledger_path(ledger_path)
        self.marks, self._token, self.hw, self.started = [], None, 0, None

    def start(self):
        self.started = time.perf_counter()
        self.marks = [('start', self.started)]
        self.hw = high_water(self.ledger_path)
        self.accounting_complete=True
        if Path(self.ledger_path).exists():
            try:
                with _ro(self.ledger_path) as db:db.execute('SELECT rowid FROM calls LIMIT 1').fetchall()
            except (sqlite3.Error,ValueError,OSError):self.accounting_complete=False
        self._token = CURRENT.set(self.question_id)
        self.owner = _Owner(self.question_id)
        self._owner_token = OWNER.set(self.owner)
        self._scope = model_policy.mark_question()  # Claude requests of this question resolve to the Haiku-first ladder (model_policy)
        return self

    def mark(self, stage):
        self.marks.append((stage, time.perf_counter()))

    def stages(self, result=None):
        out = {name: round(t - prev, 4) for (_, prev), (name, t) in zip(self.marks, self.marks[1:])}
        out['total'] = round(time.perf_counter() - (self.started or time.perf_counter()), 4)
        log = ((((result or {}).get('planner_receipt') or {}).get('interpretation') or {}).get('proposers') or [])
        out['cascade'] = [{'tier': e.get('proposer'), 'outcome': e.get('outcome'), 'seconds': e.get('seconds')} for e in log]
        return out

    def finish(self, result=None, error=None):
        if getattr(self, '_owner_token', None) is not None:
            try: OWNER.reset(self._owner_token)
            except ValueError: pass
            self._owner_token = None
        try:
            if self._token is not None:
                CURRENT.reset(self._token); self._token = None
        except ValueError:  # finished from another context: the tag simply stops
            self._token = None
        if getattr(self, '_scope', None) is not None:
            model_policy.unmark_question(self._scope); self._scope = None
        try:
            attempts = attempts_since(self.ledger_path, self.hw, self.question_id)
        except (sqlite3.Error, ValueError, OSError) as exc:
            attempts = []
            self.accounting_complete=False
            error = error or f'attempts unreadable: {type(exc).__name__}'
        seen = {a['attempt_id'] for a in attempts}
        interp = (((result or {}).get('planner_receipt') or {}).get('interpretation') or {})
        for a in interp.get('attempts') or []:  # free cache hits served before any reservation (no ledger row of this question)
            if isinstance(a, dict) and a.get('cache_hit') and a.get('request_hash') and a['request_hash'] not in seen:
                seen.add(a['request_hash'])
                attempts.append({'attempt_id': a['request_hash'], 'provider': 'claude', 'model': a.get('model'), 'status': 'validated',
                                 'cache_hit': True, 'input_tokens': 0, 'output_tokens': 0, 'cache_read_tokens': 0,
                                 'cache_write_tokens': 0, 'usd': 0.0, 'seconds': a.get('seconds'), 'error': None,
                                 'review': None, 'attribution': 'receipt'})
        summary=summarize(attempts,result,error)
        issues = self.owner.snapshot() if getattr(self, 'owner', None) else []
        complete=getattr(self,'accounting_complete',True) and summary['ownership_complete'] and not issues
        if not complete:
            summary.update(api_calls=None,api_usd=None,usd_known=False,no_paid_call=None,llm_free=None)
        return {'version': VERSION, 'question_id': self.question_id,'accounting_complete':complete,
                'question_sha': hashlib.sha256(self.question.encode()).hexdigest(), 'pricing_version': pricing_version(),
                'ledger': str(self.ledger_path), 'attempts': attempts, 'attempt_ids': [a['attempt_id'] for a in attempts],
                'attribution_issues': issues,
                'stages': self.stages(result), **summary, **({'error': str(error)[:300]} if error else {})}


def start(question, **kw):
    return Probe(question, **kw).start()


def receipt(telemetry, result=None, *, failed=False):
    """What one request cost, in one place: paid calls and USD across every provider the ledger saw (Claude, Jev,
    Perplexity), the split, and the background work the answer started. usd is None, never 0, when it cannot be known.

    Explicit ledger ownership is the authority; the planner's own count (result['new_paid_api_calls'], or the planner
    receipt's on a failure) is a cross-check: when the planner reports more calls than the ledger attributed to this
    question, the ledger missed some and the USD is unknown. Attempts attributed by time window only (another question
    ran at the same time) remain visible in 'windowed_attempts' and make per-question calls and USD unknown."""
    t = telemetry or {}
    planner = result if isinstance(result, dict) else {}
    reported = planner.get('new_paid_api_calls')
    calls = t.get('api_calls')
    ambiguous = [a for a in t.get('attempts') or [] if a.get('attribution') in ('window', 'shared_unresolved')]
    complete=t.get('accounting_complete',True) and t.get('ownership_complete',True) and not ambiguous
    consistent = complete and calls is not None and (reported is None or not isinstance(reported, int) or reported <= calls)
    usd = t.get('api_usd') if consistent and t.get('usd_known') else None
    interp = ((planner.get('planner_receipt') or {}).get('interpretation') or {}) if not failed else {}
    timing = ((planner.get('planner_receipt') or {}).get('answer_timing') or planner.get('answer_timing') or {})
    background = timing.get('background_continuation') or ((interp.get('answer_timing') or {}).get('background_continuation'))
    return {'version': 'cost-receipt/1','accounting_complete':complete,
            'paid_calls': calls if consistent else (reported if complete and isinstance(reported, int) else None),
            'usd': usd, 'usd_known': usd is not None or (consistent and calls == 0), 'claude_calls': t.get('claude_calls'),
            'system_one_calls': t.get('system_one_calls'), 'calls_by_model': t.get('calls_by_model'),
            'claude_calls_by_model': t.get('claude_calls_by_model'),
            'windowed_attempts': sum(1 for a in t.get('attempts') or [] if a.get('attribution') == 'window'),
            'ownership_complete': complete, 'attributed_paid_calls': t.get('attributed_paid_calls'),
            'known_owned_api_estimate_usd': t.get('known_owned_api_estimate_usd'),
            'ambiguous_attempt_ids': [a['attempt_id'] for a in ambiguous],
            'attribution_issues': t.get('attribution_issues', []),
            'ledger_consistent': consistent, 'planner_reported_calls': reported,
            'background': background if background is not None else None,
            'question_id': t.get('question_id')}


def record(path, telemetry):
    """answers.sqlite3 answer_costs row (one per question). Never raises."""
    if _evaluating(): return False
    try:
        db = sqlite3.connect(Path(path) / 'answers.sqlite3', timeout=30)
        try:
            db.execute(DDL)
            t, tok = telemetry, telemetry.get('tokens') or {}
            db.execute('INSERT OR REPLACE INTO answer_costs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (t['question_id'], time.time(), t['question_sha'], t['pricing_version'], json.dumps(t['attempt_ids']),
                        t['api_calls'], tok.get('input_tokens'), tok.get('output_tokens'), tok.get('cache_read_tokens'),
                        tok.get('cache_write_tokens'), t['api_usd'] if t['usd_known'] else None, int(t['usd_known']),
                        t.get('answered_by'), t.get('status'), json.dumps(t['stages'], default=str),
                        json.dumps(t, default=str)))
            db.commit()
        finally:
            db.close()
        return True
    except (sqlite3.Error, OSError, KeyError, TypeError):
        return False


def rows(path, limit=None):
    """Recorded telemetry payloads, oldest first."""
    target = Path(path) / 'answers.sqlite3'
    if not target.exists(): return []
    with _ro(target) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='answer_costs'").fetchone(): return []
        got = [json.loads(r[0]) for r in db.execute('SELECT payload FROM answer_costs ORDER BY at')]
    return got[-limit:] if limit else got
