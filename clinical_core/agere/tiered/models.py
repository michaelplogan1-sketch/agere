"""Model tiers. Each returns its proposal plus what it cost; none of them returns a number for the answer.

Tier 2  System One (Perplexity, then Jev): which recipe shape is this question? label + confidence.
Tier 3  small model (Haiku): fill one recipe's typed parameters from the question.
Tier 4  frontier model (Sonnet): write a new parameterised recipe from schema and definitions. It never sees rows.
"""
import contextvars
import json
import os
import re
import sqlite3
import time

from . import ledger, recipes, store

LLM = os.environ.get('AGERE_TIERED_LLM', 'claude-sonnet-5-5')
SLM = os.environ.get('AGERE_TIERED_SLM', 'claude-haiku-5-5')
S1_THRESHOLD = 0.8


def _json(text):
    text = text.strip()
    m = re.search(r'```(?:json)?\s*(\{.*\})\s*```', text, re.S)
    if m: text = m.group(1)
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end < 0: raise ValueError('The model returned no JSON object')
    return json.loads(text[start:end + 1])


_client = None


def _api(model, system, user):
    """Paid Anthropic API. Hosted server only (serve_hosted sets AGERE_HOSTED=1); never on a workstation."""
    if os.environ.get('AGERE_HOSTED') != '1':
        raise RuntimeError('The paid Anthropic API transport is for the hosted server only')
    import anthropic
    global _client
    if _client is None: _client = anthropic.Anthropic(max_retries=3, timeout=180.0)
    try:
        message = _client.messages.create(model=model, max_tokens=16000, system=system, messages=[{'role': 'user', 'content': user}])
    except anthropic.RateLimitError as exc:
        raise RuntimeError('Claude API rate limit reached') from exc
    except anthropic.APIStatusError as exc:
        raise RuntimeError(f'Claude API error {exc.status_code}') from exc
    except anthropic.APIConnectionError as exc:
        raise RuntimeError('Claude API could not be reached') from exc
    if message.stop_reason == 'refusal':
        raise RuntimeError('The model declined this request')
    u = message.usage
    usage = {k: getattr(u, k, 0) or 0 for k in ('input_tokens', 'output_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens')}
    return ''.join(b.text for b in message.content if b.type == 'text'), usage


class ProviderLimit(RuntimeError):
    """A provider's usage limit was reached: all model work stops (the owner's standing rule), never retried or worked around."""


class ProviderUnavailable(RuntimeError):
    """A provider failed for any other reason (outage, timeout, refusal, bad response): the rung is skipped and recorded, and the
    question climbs to the next rung."""


# Set while one reading of an ambiguous question is answered, so that reading is answered, not split again.
NO_READINGS = contextvars.ContextVar('agere_no_readings', default=False)


def is_limit(exc):
    return isinstance(exc, ProviderLimit) or 'limit' in str(exc).lower()


def _claude(model, system, user, timeout=180):
    started = time.monotonic()
    try:
        if os.environ.get('AGERE_CLAUDE_TRANSPORT', '').strip().lower() == 'api':
            text, usage = _api(model, system, user)
        else:
            from .. import subscription_transport as transport
            out = transport.run_cli(model, system, user, timeout=timeout)
            text, usage = out.get('result') or '', out.get('usage') or {}
    except (ProviderLimit, ProviderUnavailable):
        raise
    except Exception as exc:   # one of two kinds, so callers decide by type, not by reading the message
        raise (ProviderLimit if is_limit(exc) else ProviderUnavailable)(f'{type(exc).__name__}: {str(exc)[:300]}') from exc
    return text, {'model': model, 'tokens': ledger.tokens(usage), 'usd': ledger.claude_usd(model, usage), 'seconds': round(time.monotonic() - started, 2)}


DEFINITIONS = os.environ.get('AGERE_TIERED_DEFINITIONS', 'nearest')   # 'nearest' (default from 2026-11-16, proof_v6): the closest few recipes' SQL; 'all': every recipe's
NEAREST = int(os.environ.get('AGERE_TIERED_DEFINITIONS_K', '6'))
_STOP = set('a an and are as at be by for from had has have how in is it its many much of on or per that the their there this to was were what when which who with each count number one all any did does do'.split())


def _words(text):
    """Content words, singular, years and numbers left out (every recipe takes a year)."""
    out = set()
    for w in re.findall(r'[a-z][a-z0-9]*', str(text).lower().replace('_', ' ')):
        if w in _STOP or len(w) < 2: continue
        out.add(w[:-1] if len(w) > 3 and w.endswith('s') and not w.endswith('ss') else w)
    return out


def nearest_definitions(question, k=None):
    """The built-in recipes closest to the question, by words shared with each recipe's name, title, parameters and the
    tables it reads, each word weighted by how few recipes use it. Code only, no model. -> [recipe id, ...]"""
    import math
    k = NEAREST if k is None else k
    docs = {rid: _words(rid + ' ' + r['title'] + ' ' + ' '.join(r['params']) + ' ' + ' '.join(sorted(recipes.reads(r['sql']))))
            for rid, r in recipes.RECIPES.items() if recipes.runnable(rid)}
    df = {}
    for words in docs.values():
        for w in words: df[w] = df.get(w, 0) + 1
    asked = _words(question)
    # Rare shared words count most, and a recipe with a long description does not win on length alone.
    score = {rid: sum(math.log((1 + len(docs)) / df[w]) for w in asked & words) / math.sqrt(len(words) or 1) for rid, words in docs.items()}
    return [rid for rid, s in sorted(score.items(), key=lambda kv: (-kv[1], kv[0])) if s > 0][:k]


def definitions(question=None):
    """The reference SQL the frontier writer is shown. 'all' sends every applicable recipe's SQL (about 98,000 characters
    with 51 recipes); 'nearest' sends every applicable recipe's name and title on one line each and the SQL of only the
    few closest to the question, as case-based text-to-SQL work retrieves the nearest solved cases."""
    usable = [rid for rid in recipes.RECIPES if recipes.runnable(rid)] if store.mapping() else list(recipes.RECIPES)
    if DEFINITIONS != 'nearest' or question is None:
        return '\n'.join(f"- {rid}: {recipes.RECIPES[rid]['title']}\n  {recipes.RECIPES[rid]['sql']}" for rid in usable)
    close = nearest_definitions(question)
    lines = [f"- {rid}: {recipes.RECIPES[rid]['title']}\n  {recipes.RECIPES[rid]['sql']}" for rid in close]
    rest = [rid for rid in usable if rid not in close]
    if rest:
        lines.append('Other existing definitions (name and what each computes; reuse their tables and rules where they apply):')
        lines += [f"- {rid}: {recipes.RECIPES[rid]['title'][:140]}" for rid in rest]
    return '\n'.join(lines)


LLM_SYSTEM = '''You write one SQL recipe that answers a data question over a read-only SQLite semantic layer.
You never see data rows and you never state a number yourself: code will execute your recipe.

Return exactly one JSON object and nothing else:
{"sql": "<one SELECT, using :name placeholders for every value taken from the question>",
 "params": {"<name>": {"type": "year|month|int|text", "value": "<value from the question>"}},
 "grain": ["<columns that uniquely identify a result row; [] for a single-row result>"],
 "question_template": "<the question with each parameter value replaced by {name}>"}

If the question asks what documents, notes, reports or letters say, and no listed column already holds exactly that,
the documents must be read. Return a reading plan instead; code will read every selected document:
{"sql": "<one SELECT from the document table whose first column is record_id, selecting exactly the documents the question is about>",
 "params": {"<name>": {"type": "year|month|int|text", "value": "<value from the question>"}},
 "read": {"statement": "<one self-contained sentence that is true of a document exactly when the question wants it counted or listed>",
          "mode": "count" or "list" or "extract",
          "value": "<extract only: a short name of the one value to copy out of each document, such as the discharge disposition>"},
 "question_template": "<the question with each parameter value replaced by {name}>"}
Use count for how many documents say something, list for which documents say it, and extract when the question asks
what each document says (one row per document that gives the value); for extract the statement is that the document
gives that value. A count or list plan may select at most 3000 documents and an extract plan at most 150, so filter by patient, document type and period as the question does.
Never match document content with LIKE on type or title columns, and never select document text.

If neither one SELECT nor one reading plan can fully answer every part of the question (it needs several separate
result tables, a statistical method, a judgment across documents, or data that is not in these tables), do not
approximate. Return instead: {"cannot_answer": "<what is needed that these tables or one query cannot give>"}

If the question can plainly be read two or three ways that would give different answers, and neither the question nor the
governed definitions above settle which one is meant (for example: which facilities count, which unit of time, which
grouping level, distinct patients or events, which date puts a record in the period, initial or final status), do not
choose. Return instead the readings, each restated as a complete explicit question that settles the point:
{"readings": [{"label": "<two to five words naming this reading>", "question": "<the question restated so it has one reading>"}, ...]}
Code will answer each reading and ask the person only if the answers differ. Do not return readings for differences the
governed definitions already decide, for wording that is merely informal, or for a question that has one ordinary reading.
NO_READINGS_NOTE

Rules:
- Exactly one SELECT (WITH is allowed). No semicolons. Only the listed tables and columns.
- Dates are ISO text; filter a calendar year with substr(date_col, 1, 4) = :year and a month with substr(date_col, 1, 7) = :month.
- Every period or number the question states must be a parameter, never a literal in the SQL.
- Compare a text parameter without regard to case: lower(col) = lower(:name).
- Give each result column a clear name. Order the rows deterministically.
- Reuse the existing definitions below where they fit, so totals agree with them.

Tables and columns:
%s

Existing definitions (reference SQL):
%s

Vocabulary (read from this warehouse): condition concepts are exactly: %s.
Compare concept and drug_class with = or IN, never LIKE, and use the value that is the question's own word: "diabetes" is the
concept diabetes, not type_2_diabetes or prediabetes. Money columns are integer cents. Boolean columns are 0/1.
measurement_latest holds only each patient's most recent value of a concept; to count or average results within a period use measurement.
stay_episode is one row per inpatient hospital stay with repeated and overlapping stay rows merged; count stays, admissions,
discharges, length of stay and readmissions from it, never from stay rows.
hospital_visit is one row per hospital visit with governed ED times (ed_minutes, boarding_minutes; implausible ones excluded),
ed_outcome, the ED timeliness thresholds the data supports, sepsis and heart failure flags by principal diagnosis and death in
hospital; read ED times and these cohorts from it.'''


FOREIGN_SYSTEM = '''You write one SQL query that answers a data question over a read-only SQLite healthcare warehouse.
You never see data rows and you never state a number yourself: code will execute your query.

Return exactly one JSON object and nothing else:
{"sql": "<one SELECT, using :name placeholders for every value taken from the question>",
 "params": {"<name>": {"type": "year|month|int|text", "value": "<value from the question>"}},
 "grain": ["<columns that uniquely identify a result row; [] for a single-row result>"],
 "question_template": "<the question with each parameter value replaced by {name}>"}

If one SELECT cannot fully answer every part of the question (it needs several separate result tables, a statistical
method, a judgment across documents, or data that is not in these tables), do not approximate. Return instead:
{"cannot_answer": "<what is needed that these tables or one query cannot give>"}

Rules:
- Exactly one SELECT (WITH is allowed). No semicolons. Only the listed tables and columns.
- Filter a calendar year with substr(date_col, 1, 4) = :year and a month with substr(date_col, 1, 7) = :month, adapting to how
  each column stores its dates.
- Every period or number the question states must be a parameter, never a literal in the SQL.
- Compare a text parameter without regard to case: lower(col) = lower(:name).
- Give each result column a clear name. Order the rows deterministically.

Tables and columns:
%s

Text columns with few distinct values, and their values:
%s'''


EXAMPLES = os.environ.get('AGERE_TIERED_LLM_EXAMPLES', 'off') == 'on'   # show the writer the nearest verified saved queries


def nearest_examples(question, k=3, exclude=()):
    """The k promoted saved recipes most like the question, as (question template, SQL). Code only."""
    from . import bottles as bottle_store, near
    try:
        found = near.shortlist(question, bottle_store.Bottles(), k=k + len(exclude) + 2)
    except Exception:
        return []
    out = [(b['template'], b['sql']) for b in found if b['id'] not in exclude and not b.get('read')]
    return out[:k]


# The coded columns whose values the SQL writer needs: read from the warehouse in use, so the prompt never carries one
# warehouse's vocabulary to another (design review: these were the synthetic warehouse's values, typed in).
VOCABULARY_COLUMNS = (('utilization_event', 'kind'), ('stay', 'type'), ('appointment', 'final_status'), ('appointment', 'outcome'),
                      ('concept_patient', 'kind'), ('fill', 'drug_class'), ('document', 'source'), ('document', 'document_type'),
                      ('note_fact', 'fact_type'))


def _shape(identifier):
    """An identifier's format, never its value: WHP-66F4B476DECF -> WHP-XXXXXXXXXXXX (letters after the prefix X, digits 9)."""
    head, sep, tail = str(identifier).partition('-')
    if not sep: return re.sub(r'[0-9]', '9', re.sub(r'[A-Za-z]', 'X', head))
    return head + '-' + re.sub(r'[0-9]', '9', re.sub(r'[A-Za-z]', 'X', tail))


def warehouse_vocabulary(limit=40):
    """-> lines naming each coded column's values (at most `limit` distinct, else left out) and the patient identifier formats."""
    cache = store.scoped('prompt_vocabulary')
    if 'text' not in cache:
        db = store.connect()
        lines = []
        try:
            for table, col in VOCABULARY_COLUMNS:
                try:
                    values = [r[0] for r in db.execute(f'SELECT DISTINCT "{col}" FROM "{table}" WHERE "{col}" IS NOT NULL LIMIT {limit + 1}')]
                except sqlite3.Error:
                    continue
                if 0 < len(values) <= limit: lines.append(f'{table}.{col} is one of: ' + ', '.join(sorted(str(v) for v in values)) + '.')
                elif values:   # too many to list: the most common, so the model sees their form
                    common = [r[0] for r in db.execute(f'SELECT "{col}" FROM "{table}" WHERE "{col}" IS NOT NULL GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 12')]
                    lines.append(f'{table}.{col} has more than {limit} values; the most common: ' + ', '.join(str(v) for v in common) + '.')
            try:
                shapes = sorted({_shape(r[0]) for r in db.execute('SELECT patient_id FROM patient LIMIT 2000')})[:4]
                if shapes: lines.append('Patient identifiers look like ' + ' or '.join(shapes) + ' (X a letter, 9 a digit).')
            except sqlite3.Error:
                pass
        finally:
            db.close()
        cache['text'] = ('\n' + '\n'.join(lines)) if lines else ''
    return cache['text']


def llm_recipe(question, error=None, model=None, examples=None):
    """Tier 4. -> (recipe dict or None, step). error: one SQL execution error to correct, if any.
    model: a different model for an independent second query (curriculum.confirm); the frontier model by default."""
    from . import catalogue
    if store.foreign():
        # A warehouse that is not this semantic layer and has no mapping: the same rules, its own tables and vocabulary, and
        # none of this layer's recipes, vocabulary or governed definitions, which name tables it does not have.
        system, governed = FOREIGN_SYSTEM % (store.schema_text(), store.vocabulary_text()), ''
    else:
        try:
            conditions = ', '.join(r[0] for r in store.run("SELECT DISTINCT concept FROM concept_patient WHERE kind = 'condition' ORDER BY 1")[1])
        except store.RecipeError:
            conditions = '(not listed: this mapped warehouse has no concept_patient table)'
        system = (LLM_SYSTEM % (store.schema_text(), definitions(question), conditions)).replace('NO_READINGS_NOTE',
                  'This question is already one reading of an earlier question: answer it; do not return readings.' if NO_READINGS.get() else '') + warehouse_vocabulary()
        governed = catalogue.block(question) if catalogue.enabled() else ''
    if governed: system += '\n\n' + governed
    if examples is None and EXAMPLES and model in (None, LLM): examples = nearest_examples(question)
    shown = ''.join(f'\nSaved question: {t}\nIts verified SQL: {q}\n' for t, q in (examples or []))
    user = ('Verified saved queries for similar questions follow. They answer other questions: reuse their tables, joins and '
            'definitions only where this question asks the same thing, and write your own SQL for what differs.\n' + shown + '\n' if shown else '') + \
           'Question: ' + question + ('\n\nYour previous SQL failed to execute: ' + error + '\nReturn a corrected JSON object.' if error else '')
    text, step = _claude(model or LLM, system, user)
    try:
        out = _json(text)
        if out.get('cannot_answer'):
            return None, {**step, 'tier': 'llm', 'outcome': 'cannot_answer', 'reason': str(out['cannot_answer'])[:300]}
        if out.get('readings') and not NO_READINGS.get():
            readings = [{'label': str(r.get('label'))[:60], 'question': str(r.get('question'))[:600]} for r in out['readings'][:3]
                        if isinstance(r, dict) and r.get('question')]
            if len(readings) >= 2:
                return None, {**step, 'tier': 'llm', 'outcome': 'readings', 'readings': readings}
            raise ValueError('readings need at least two restated questions')
        if not isinstance(out.get('sql'), str) or not isinstance(out.get('params', {}), dict): raise ValueError('Recipe needs sql and params')
        recipe = {'sql': out['sql'], 'params': {k: v for k, v in (out.get('params') or {}).items()},
                  'grain': [g for g in out.get('grain') or [] if isinstance(g, str)], 'question_template': out.get('question_template')}
        if out.get('read') is not None:
            read = out['read'] if isinstance(out['read'], dict) else {}
            if not isinstance(read.get('statement'), str) or read.get('mode') not in ('count', 'list', 'extract'):
                raise ValueError('A reading plan needs a statement and mode count, list or extract')
            if read['mode'] == 'extract' and not isinstance(read.get('value'), str): raise ValueError('An extract plan names its value')
            recipe['read'], recipe['grain'] = {'statement': read['statement'], 'mode': read['mode'],
                                               **({'value': read['value']} if read['mode'] == 'extract' else {})}, []
        return recipe, {**step, 'tier': 'llm', 'outcome': 'proposed'}
    except (ValueError, json.JSONDecodeError) as exc:
        return None, {**step, 'tier': 'llm', 'outcome': 'invalid_output', 'reason': str(exc)[:200]}


SLM_SYSTEM = '''Fill the parameters of one analytics recipe from a question. Return exactly one JSON object mapping each
parameter name to its value taken from the question, or {"fits": false} if the question asks for something this recipe
does not compute. Use the allowed values exactly as written when a parameter lists them. Do not answer the question.'''


def slm_slots(question, recipe_id, db=None):
    """Tier 3. -> (typed params or None, step)."""
    recipe = recipes.RECIPES[recipe_id]
    spec = {}
    for name, p in recipe['params'].items():
        spec[name] = {'type': p['type'], **({'allowed': p['values']} if 'values' in p else {}),
                      **({'allowed': recipes.vocabulary(p, db)} if p['type'] == 'vocabulary' else {}),
                      **({'format': 'YYYY'} if p['type'] == 'year' else {'format': 'YYYY-MM'} if p['type'] == 'month' else
                         {'format': 'the thing each document must state or give, in the words of the question'} if p['type'] == 'text' else
                         {'format': 'the patient identifier exactly as written'} if p['type'] == 'patient' else {})}
    user = json.dumps({'recipe': recipe['title'], 'parameters': spec, 'question': question})
    text, step = _claude(SLM, SLM_SYSTEM, user)
    try:
        raw = _json(text)
    except (ValueError, json.JSONDecodeError) as exc:
        return None, {**step, 'tier': 'slm', 'outcome': 'invalid_output', 'reason': str(exc)[:200]}
    if raw.get('fits') is False:
        return None, {**step, 'tier': 'slm', 'outcome': 'declined', 'reason': 'recipe does not fit the question'}
    params = recipes.normalize(recipe_id, raw, db)
    return params, {**step, 'tier': 'slm', 'outcome': 'filled' if params is not None else 'invalid_parameters',
                    **({} if params is not None else {'reason': 'values failed type checks: ' + json.dumps(raw)[:160]})}


SLM_CHOOSE_SYSTEM = '''Choose the one analytics recipe that computes exactly what a question asks, and fill its parameters
from the question. Return exactly one JSON object: {"recipe": "<recipe id>", "params": {"<name>": "<value>"}}, or
{"recipe": null} if no listed recipe computes exactly what is asked (a different grouping, filter, measure or
combination means no recipe fits). Use allowed values exactly as written. Do not answer the question.'''


def slm_choose_and_fill(question, db=None):
    """Tier 3 without Tier 2: the small model picks a recipe and fills it. -> (recipe_id, params) or (None, None), step."""
    catalog = {}
    for rid, r in recipes.RECIPES.items():
        if not recipes.runnable(rid): continue
        catalog[rid] = {'computes': r['title'], 'parameters': {
            n: ({'allowed': p['values']} if 'values' in p else {'vocabulary': 'lower-case name'} if p['type'] == 'vocabulary'
                else {'format': {'year': 'YYYY', 'month': 'YYYY-MM', 'int': 'integer', 'text': 'words from the question', 'patient': 'identifier as written'}[p['type']]})
            for n, p in r['params'].items()}}
    text, step = _claude(SLM, SLM_CHOOSE_SYSTEM, json.dumps({'recipes': catalog, 'question': question}))
    try:
        raw = _json(text)
    except (ValueError, json.JSONDecodeError) as exc:
        return None, None, {**step, 'tier': 'slm', 'outcome': 'invalid_output', 'reason': str(exc)[:200]}
    rid = raw.get('recipe')
    if rid not in recipes.RECIPES:
        return None, None, {**step, 'tier': 'slm', 'outcome': 'declined', 'reason': 'no recipe fits'}
    params = recipes.normalize(rid, raw.get('params') or {}, db)
    if params is None:
        return None, None, {**step, 'tier': 'slm', 'outcome': 'invalid_parameters', 'recipe': rid,
                            'reason': 'values failed type checks: ' + json.dumps(raw.get('params'))[:160]}
    return rid, params, {**step, 'tier': 'slm', 'outcome': 'filled', 'recipe': rid}


def s1_classify(question, ledger_path=None):
    """Tier 2. -> (recipe_id or None, confidence, step). System One picks one recipe shape or 'none'."""
    from ..clinical import system_one
    options = {rid: r['title'] for rid, r in recipes.RECIPES.items() if recipes.runnable(rid)}
    options['none'] = 'The question asks for something none of the listed computations produce'
    started = time.monotonic()
    steps_cost, label, p, provider, error = 0.0, None, 0.0, None, None
    for name in ('perplexity', 'jev'):
        try:
            out = system_one.choice('Which single computation answers this analytics question exactly? Question: ' + question,
                                    options, [], as_of=time.strftime('%Y-%m-%d'), provider=name, ledger=ledger_path)
        except Exception as exc:  # a provider outage is a decline, never a guess
            error = f'{type(exc).__name__}: {exc}'[:160]
            continue
        steps_cost += out.get('cost_usd') or 0.0
        probabilities = out.get('probabilities') or {}
        label = out.get('label'); p = float(probabilities.get(label, 0.0)) if label else 0.0; provider = name
        if label and p >= S1_THRESHOLD: break
    step = {'tier': 'system1', 'model': provider, 'tokens': 0, 'usd': steps_cost, 'seconds': round(time.monotonic() - started, 2),
            'confidence': round(p, 4), 'label': label, **({'error': error} if error and not provider else {})}
    if not label or label == 'none' or p < S1_THRESHOLD or label not in recipes.RECIPES:
        return None, p, {**step, 'outcome': 'low_confidence' if label and label != 'none' and p < S1_THRESHOLD else 'no_matching_shape'}
    return label, p, {**step, 'outcome': 'classified'}


FIT_OPTIONS = {
    'exact': 'The computation returns exactly what the question asks for: the same measure, the same filters and the same rows. Nothing asked for is missing and nothing has to be picked out of, ranked within or added to its output.',
    'not_exact': 'The question asks for something else or something more specific: a different measure, column, filter or grouping, only the top or a subset of the rows, a comparison, a combination, or any further step applied to the output.',
}


def s1_fit(question, recipe_id, params, ledger_path=None):
    """Tier 2 check before a chosen recipe is executed: is it exactly what was asked? -> (bool, step).
    A near-miss shape (the monthly table when the question asks which month was highest) must go up a tier."""
    from ..clinical import system_one
    recipe = recipes.RECIPES[recipe_id]
    started = time.monotonic()
    usd, p, provider, label, error = 0.0, 0.0, None, None, None
    prompt = ('Question: ' + question + '\nProposed computation: ' + recipe['title'] + '\nWith parameters: ' + json.dumps(params) +
              '\nOutput columns: ' + ', '.join(recipe.get('columns') or re.findall(r'AS (\w+)', recipe['sql'])) + '\nIs this computation exactly what the question asks for?')
    for name in ('perplexity', 'jev'):
        try:
            out = system_one.choice(prompt, FIT_OPTIONS, [], as_of=time.strftime('%Y-%m-%d'), provider=name, ledger=ledger_path)
        except Exception as exc:
            error = f'{type(exc).__name__}: {exc}'[:160]
            continue
        usd += out.get('cost_usd') or 0.0
        label = out.get('label'); p = float((out.get('probabilities') or {}).get(label, 0.0)) if label else 0.0; provider = name
        if label and p >= S1_THRESHOLD: break
    ok = label == 'exact' and p >= S1_THRESHOLD
    return ok, {'tier': 'system1', 'check': 'exact_fit', 'model': provider, 'tokens': 0, 'usd': usd, 'seconds': round(time.monotonic() - started, 2),
                'label': label, 'confidence': round(p, 4), 'outcome': 'fits' if ok else 'not_exact_fit', **({'error': error} if error and not provider else {})}
