"""Model tiers. Each returns its proposal plus what it cost; none of them returns a number for the answer.

Tier 2  System One (Perplexity, then Jev): which recipe shape is this question? label + confidence.
Tier 3  small model (Haiku): fill one recipe's typed parameters from the question.
Tier 4  frontier model (Sonnet): write a new parameterised recipe from schema and definitions. It never sees rows.
"""
import json
import os
import re
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


def _claude(model, system, user):
    from .. import subscription_transport as transport
    started = time.monotonic()
    out = transport.run_cli(model, system, user, timeout=180)
    usage = out.get('usage') or {}
    return out.get('result') or '', {'model': model, 'tokens': ledger.tokens(usage), 'usd': ledger.claude_usd(model, usage),
                                    'seconds': round(time.monotonic() - started, 2)}


def definitions():
    lines = []
    for rid, r in recipes.RECIPES.items():
        lines.append(f"- {rid}: {r['title']}\n  {r['sql']}")
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
gives that value. A reading plan may select at most 150 documents, so filter by patient, document type and period as the question does.
Never match document content with LIKE on type or title columns, and never select document text.

If neither one SELECT nor one reading plan can fully answer every part of the question (it needs several separate
result tables, a statistical method, a judgment across documents, or data that is not in these tables), do not
approximate. Return instead: {"cannot_answer": "<what is needed that these tables or one query cannot give>"}

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

Vocabulary: utilization_event.kind is one of ed_visit, inpatient, observation. stay.type 'inpatient' or 'observation'.
appointment.final_status is one of arrived, attended, booked, cancelled, fulfilled, no_show, rescheduled, unassigned_requires_review;
appointment.outcome is one of kept, cancelled, no_show, scheduled, unassigned. concept_patient.kind is condition, encounter or measurement;
concepts are lower snake case such as hypertension, type_2_diabetes, heart_failure. fill.drug_class is lower snake case such as statin,
ace_inhibitor, metformin. Money columns are integer cents. Boolean columns are 0/1.
document.source is one of clinical_note, scanned_document, hie_document. For scanned_document, document_type is one of
Radiology Report, Consult Note, Outside Records, Miscellaneous, Imaging - Outside, Correspondence, Specialist Letter,
Procedure Report, Surgery/Procedure, Home Health Orders, Pathology, Discharge Summary; for hie_document it is ED summary or
Discharge summary; for clinical_note it is a lower snake case note type such as office_followup, discharge_summary, ed_note.
note_fact holds facts already extracted from notes by rules: fact_type is one of medication_days_used, medication_nonadherence,
symptom, dyspnea, follow_up, edema, imaging_finding, social_need, smoking_status, goals_of_care, ejection_fraction, walk_distance.
Patient identifiers look like WHP-66F4B476DECF or P-1001.'''


def llm_recipe(question, error=None):
    """Tier 4. -> (recipe dict or None, step). error: one SQL execution error to correct, if any."""
    system = LLM_SYSTEM % (store.schema_text(), definitions())
    user = 'Question: ' + question + ('\n\nYour previous SQL failed to execute: ' + error + '\nReturn a corrected JSON object.' if error else '')
    text, step = _claude(LLM, system, user)
    try:
        out = _json(text)
        if out.get('cannot_answer'):
            return None, {**step, 'tier': 'llm', 'outcome': 'cannot_answer', 'reason': str(out['cannot_answer'])[:300]}
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
    options = {rid: r['title'] for rid, r in recipes.RECIPES.items()}
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
