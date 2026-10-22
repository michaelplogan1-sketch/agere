"""Atomic typed sub-questions answered by System One per patient / per document, aggregated in code (CS1 use case #1).

A broad clinical question ("which of the four GDMT pillars is each HFrEF patient on?", "does the chart support that P-1 is
intolerant to beta blockers?", "why is P-1 not on an SGLT2 inhibitor?") becomes a small set of ATOMIC typed questions, each
with explicit options including 'unknown'. Each atom is asked of one patient's (or one document's) evidence: ranked passages
from stack.retriever.search(patient=...) plus, for medication subjects, the governed medication_state rows (fills, listings,
stops/barriers). system_one.decide reads it (Jev first, Perplexity when Jev is unsure or fails). Code aggregates:
  any        a patient is selected when any atom takes a selected label (patient lists, 'why' factor tables, verdicts)
  latest     one label per patient x atom (per-pillar tables)
  count      patients with a selected label (count, or share over evaluated patients)
  conflict   per-document labels; a patient conflicts when documents both affirm and negate
  trajectory per-document labels ordered by date; the changes over time
Every non-unknown label cites the evidence ids System One chose (or the top passage). Patient count per call is bounded
(AGERE_ATOMIC_MAX_PATIENTS, default 25); a capped run is status 'partial' and says so in the value and limitations.

Planner order (no Claude needed): deterministic patterns, then the local Qwen typed interpreter (AGERE_LOCAL_TYPED), then
hosted Claude only with stack.allow_live (ProviderUnavailable / no credits -> continue without it).
Graph form: cohort -> 'atomic_questions' (registered in analysis_graph), so the cascade's literal and answer checks run.
"""
import json
import os
import re
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ...contracts import digest
from .question_input import QuestionNotUnderstood

VERSION = 'atomic-questions/1'
AGGREGATIONS = ('any', 'latest', 'trajectory', 'conflict', 'count')
RETURNS = ('table', 'patient_set', 'count', 'value', 'verdict')
UNITS = ('patient', 'document')
SOURCES = ('passages', 'medication_state', 'therapy_barriers', 'external')  # external = cited Perplexity Agent search
PROVIDERS = ('jev', 'perplexity')
COST_PER_CALL = {'jev': 0.0002, 'perplexity': 0.0001}  # measured 2026-10-22; used only when System One reports no cost
HARD_MAX_PATIENTS = 200
PASSAGES_PER_ATOM = 6
DOCUMENTS_PER_PATIENT = 6
TEXT_CHARS = 700
YES_NO = {'yes': 'Explicitly established by the evidence', 'no': 'Explicitly contradicted by the evidence',
          'unknown': 'Insufficient, contradictory or unresolved evidence'}
PILLAR_OPTIONS = {'on': 'Currently taking: a current fill, or an active listing with no later stop',
                  'stopped': 'Documented as stopped, held or discontinued',
                  'intolerant': 'Documented intolerance, adverse effect, allergy or contraindication',
                  'not_on': 'Not taking: never prescribed, or a lapsed listing with no current use',
                  'unknown': 'Insufficient or contradictory evidence'}
PILLAR_TEXT = {'rasi': 'an ACE inhibitor, ARB or ARNI (sacubitril/valsartan)',
               'beta_blocker': 'an evidence-based heart-failure beta blocker (carvedilol, metoprolol succinate or bisoprolol)',
               'mra': 'a mineralocorticoid receptor antagonist (spironolactone or eplerenone)',
               'sglt2i': 'an SGLT2 inhibitor (dapagliflozin, empagliflozin or sotagliflozin)'}
# question phrase -> medication_state subject (a pillar or an exact drug class)
MED_PHRASES = [(r'\bbeta[- ]?blockers?\b|\bbblockers?\b', 'beta_blocker'), (r'\bsglt-?2(?:\s*i)?\b|\bsglt2 inhibitors?\b|\bgliflozins?\b', 'sglt2_inhibitor'),
               (r'\bmras?\b|mineralocorticoid|spironolactone|eplerenone', 'mra'), (r'\barnis?\b|sacubitril|entresto', 'arni'),
               (r'\bace[- ]?inhibitors?\b|\bacei\b', 'ace_inhibitor'), (r'\barbs?\b|angiotensin receptor blockers?', 'arb'),
               (r'\brasi\b|\braas\b|renin[- ]angiotensin', 'rasi'), (r'\bstatins?\b', 'statin'), (r'\bglp-?1\b', 'glp1_agonist'),
               (r'\bmetformin\b', 'metformin'), (r'\binsulin\b', 'insulin'), (r'\banticoagula\w*|\bdoacs?\b', 'anticoagulant'),
               (r'\bloop diuretics?\b|furosemide|torsemide|bumetanide', 'loop_diuretic'), (r'\bopioids?\b', 'opioid')]
GDMT = re.compile(r'\b(?:gdmt|guideline[- ]directed (?:medical )?therap\w*|(?:four|4) (?:gdmt |hf |heart[- ]failure )?pillars|'
                  r'hf pillars|gdmt pillars)\b', re.I)
WHY = re.compile(r'^\s*why\b', re.I)
PREDICATE = re.compile(r'\b(?:on|taking|take|prescribed|receiving|intoleran\w*|tolerat\w*|allerg\w*|adverse|side effects?|stopped|'
                       r'discontinu\w*|held|eligib\w*|candidates?|contraindicat\w*|documented|declin\w*|refus\w*|support\w*)\b', re.I)
CONFLICT = re.compile(r'\b(?:conflict\w*|contradict\w*|disagree\w*|inconsistent|discrepan\w*)\b', re.I)
TRAJECTORY = re.compile(r'\b(?:over time|trajector\w*|changed?|changes|evolv\w*|history of changes|timeline)\b', re.I)
LEAD = re.compile(r'^\s*(?:does|do|did|is|are|was|were|has|have|had|can|could|should)\s+(?:the\s+)?(?:chart|record|notes?|evidence|'
                  r'documentation)\s+(?:support|show|establish|indicate|document|say)\s+(?:that\s+)?', re.I)
WHY_FACTORS = [('intolerance', 'Is there documented intolerance, an adverse effect or an allergy to {drug}?'),
               ('contraindication', 'Is there a documented contraindication to {drug} (for example kidney function, potassium, '
                                    'blood pressure or heart rate)?'),
               ('declined', 'Did the patient decline or refuse {drug}?'),
               ('cost_access', 'Is cost, coverage or access documented as a barrier to {drug}?'),
               ('not_indicated', 'Is {drug} documented as not indicated for this patient?'),
               ('clinician_stopped', 'Was {drug} stopped or held by a clinician?'),
               ('never_prescribed', 'Is there no record that {drug} was ever prescribed for this patient?')]
_MEMO, _MEMO_LOCK, MEMO_SIZE = OrderedDict(), threading.Lock(), 4096


def max_patients():
    try: return max(1, min(HARD_MAX_PATIENTS, int(os.environ.get('AGERE_ATOMIC_MAX_PATIENTS', '25'))))
    except ValueError: return 25


def code_identity():
    import hashlib
    return digest([VERSION, hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])


# -- question reading ------------------------------------------------------------------------------------------------
def med_subjects(question):
    """Medication subjects literally named in the question, in question order (exact drug names via drug_classes)."""
    found = []
    for rx, subject in MED_PHRASES:
        m = re.search(rx, question, re.I)
        if m: found.append((m.start(), subject))
    if not found:
        try:
            from . import drug_classes
            for w in re.findall(r'[A-Za-z][A-Za-z-]{4,}', question):
                for c in drug_classes.classify(w) or []: found.append((question.find(w), c))
        except Exception:  # noqa: BLE001 - no drug lexicon: phrase table only
            pass
    return list(dict.fromkeys(s for _, s in sorted(found)))


def population_concept(question):
    """A condition concept naming the population ("for HFrEF patients", "patients with CKD"): the most specific match."""
    from .concepts import CONDITIONS
    phrase = ' '.join(m.group(1) for m in re.finditer(
        r'\b(?:for|among|of|in)\s+(?:the\s+|our\s+|all\s+)?([\w\s/-]{2,60}?)\s+(?:patients|members|people)\b'
        r'|\b(?:which|how many|what share of|list)\s+([\w\s/-]{2,60}?)\s+(?:patients|members)\b', question, re.I) if m.group(1)) \
        or ' '.join(g for m in re.finditer(r'\b(?:which|how many|list)\s+([\w\s/-]{2,60}?)\s+(?:patients|members)\b', question, re.I)
                    for g in m.groups() if g)
    phrase += ' ' + ' '.join(m.group(1) for m in re.finditer(r'\b(?:patients|members)\s+(?:with|who have|having)\s+([\w\s/-]{2,60}?)(?:\s+(?:are|is|who|that|on|have|had)\b|[,?.]|$)',
                                                              question, re.I))
    if not phrase.strip(): return None
    best = None
    for cid, c in CONDITIONS.items():
        m = re.search(c.get('text') or r'(?!)', phrase, re.I)
        if m and not (c.get('exclude') and re.search(c['exclude'], phrase, re.I)):
            score = (len(m.group()), -len(cid))
            if best is None or score > best[0]: best = (score, cid)
    return best[1] if best else None


def requested(question):
    from .analysis_graph import requested_shape
    return requested_shape(question)


def atom_text(question, named):
    """The question restated about ONE patient: lead-in removed, patient ids -> 'the patient'."""
    text = LEAD.sub('', question).strip()
    for pid in named: text = re.sub(rf'(?<![\w-]){re.escape(pid)}(?![\w-])', 'the patient', text)
    text = re.sub(r'^\s*(?:which|how many|list|what share of|who)\s+(?:of\s+)?(?:the\s+)?([\w\s/-]{0,60}?)(?:patients|members)\s+',
                  'the patient ', text, flags=re.I)
    return ('For this patient only, does the submitted evidence establish: ' + text.rstrip('?') + '?')[:600]


def _shape(question, named):
    shape = requested(question)
    if CONFLICT.search(question): return 'conflict', 'table', 'document'
    if TRAJECTORY.search(question) and not GDMT.search(question): return 'trajectory', 'table', 'document'
    if shape == 'count': return 'count', 'count', 'patient'
    if shape == 'value': return 'count', 'value', 'patient'
    if shape == 'patient_set': return 'any', 'patient_set', 'patient'
    if shape == 'table': return 'latest', 'table', 'patient'
    return 'any', ('verdict' if len(named) == 1 else 'table'), 'patient'


def _sub(key, atom, options, subject='', task_key='atomic_yes_no'):
    return {'key': key, 'atom': atom[:600], 'options': dict(options), 'subject': subject or '', 'task_key': task_key}


def deterministic(stack, question, population=None):
    """Pattern planner. -> plan core {'sub_questions','aggregation','returns','unit','select_labels','patients','concept'} or None."""
    from .evidence_proposers import named_patients, population_ids
    named = named_patients(stack, question)
    patients = named or population_ids(population)
    concept = None if patients else population_concept(question)
    subjects = med_subjects(question)
    if GDMT.search(question):
        from .medication_state import PILLARS
        subs = [_sub(p, f'Currently, what is this patient\'s status for {PILLAR_TEXT[p]}?', PILLAR_OPTIONS, p, 'atomic_gdmt_pillar')
                for p in PILLARS]
        aggregation, returns, unit = ('latest', 'table', 'patient')
        shape = requested(question)
        if shape in ('patient_set', 'count') and re.search(r'\b(?:not|missing|without|no)\b', question, re.I):
            aggregation, returns = 'any', 'patient_set' if shape == 'patient_set' else 'count'
            return dict(sub_questions=subs, aggregation=aggregation, returns=returns, unit=unit,
                        select_labels=['stopped', 'intolerant', 'not_on'], patients=patients, concept=concept)
        return dict(sub_questions=subs, aggregation=aggregation, returns=returns, unit=unit, select_labels=['on'],
                    patients=patients, concept=concept)
    if WHY.search(question):
        if not subjects or not patients: return None
        drug = subjects[0].replace('_', ' ')
        subs = [_sub(k, t.format(drug=drug), YES_NO, subjects[0], 'atomic_why_factor') for k, t in WHY_FACTORS]
        return dict(sub_questions=subs, aggregation='any', returns='table', unit='patient', select_labels=['yes'],
                    patients=patients, concept=concept)
    over_documents = bool(subjects) and bool(CONFLICT.search(question) or TRAJECTORY.search(question))
    if not (PREDICATE.search(question) or over_documents): return None
    if not (named or patients or concept or re.search(r'\b(?:which|how many|list|who|what share)\b', question, re.I)): return None
    aggregation, returns, unit = _shape(question, named)
    atom = (f"According to this document, is the patient taking {subjects[0].replace('_', ' ')}?"
            if unit == 'document' and subjects and not PREDICATE.search(LEAD.sub('', question)) else atom_text(question, named))
    subs = [_sub('assertion', atom, YES_NO, subjects[0] if subjects else '', 'atomic_yes_no')]
    return dict(sub_questions=subs, aggregation=aggregation, returns=returns, unit=unit, select_labels=['yes'],
                patients=patients, concept=concept)


# -- model planners (local Qwen, then hosted Claude) -----------------------------------------------------------------
PLANNER_SYSTEM = (
    'Split a clinical question into ATOMIC typed sub-questions, each answerable about ONE patient from that patient\'s chart. '
    'Reply with JSON only: {"sub_questions":[{"key":"snake_case","atom":"question about this patient","options":{"label":'
    '"description",...,"unknown":"Insufficient evidence"}}],"aggregation":"any|latest|count|conflict|trajectory",'
    '"returns":"table|patient_set|count|value|verdict","select_labels":["label"],"population_concept":"condition id or empty"}. '
    'At most 8 sub-questions; every options object includes "unknown". Never answer the question.')


def _model_core(stack, question, population, text):
    from .evidence_proposers import named_patients, population_ids
    m = re.search(r'\{.*\}', text or '', re.S)
    if not m: return None
    raw = json.loads(m.group())
    subs = [_sub(re.sub(r'[^a-z0-9_]', '_', str(s.get('key') or f'q{i}').lower())[:40] or f'q{i}', str(s['atom']),
                 {str(k)[:40]: str(v)[:200] for k, v in (s.get('options') or {}).items()}, '', 'atomic_model_planned')
            for i, s in enumerate(raw.get('sub_questions') or [])][:8]
    if not subs or any('unknown' not in s['options'] or len(s['options']) < 2 for s in subs): return None
    aggregation = raw.get('aggregation') if raw.get('aggregation') in AGGREGATIONS else 'any'
    returns = raw.get('returns') if raw.get('returns') in RETURNS else 'table'
    from .concepts import CONDITIONS
    named = named_patients(stack, question)
    patients = named or population_ids(population)
    concept = raw.get('population_concept') if raw.get('population_concept') in CONDITIONS and not patients else None
    labels = [l for l in raw.get('select_labels') or [] if any(l in s['options'] for s in subs)] or \
        [next(l for l in subs[0]['options'] if l != 'unknown')]
    for s in subs:
        s['subject'] = (med_subjects(s['atom']) or [''])[0]
    return dict(sub_questions=subs, aggregation=aggregation, returns=returns,
                unit='document' if aggregation in ('conflict', 'trajectory') else 'patient', select_labels=labels,
                patients=patients, concept=concept or (population_concept(question) if not patients else None))


def local_core(stack, question, population):
    if os.environ.get('AGERE_LOCAL_TYPED', 'on') == 'off': return None
    from .nl2sql import MODEL_DIR, LocalModel
    if not MODEL_DIR.exists(): return None
    text, _ = LocalModel.get()(PLANNER_SYSTEM, question, max_tokens=700)
    return _model_core(stack, question, population, text)


def hosted_core(stack, question, population, ledger=None):
    """Hosted Claude planner: allow_live only; ProviderUnavailable (no credits) and any failure -> None, never raises."""
    if not getattr(stack, 'allow_live', False): return None, None
    from ..gateway import ProviderUnavailable, BudgetLedger, default_ledger_path
    from .nl2sql import HostedModel
    owned = ledger is None
    ledger = ledger or BudgetLedger(default_ledger_path())
    try:
        text, receipt = HostedModel(ledger, stack.receipts)(PLANNER_SYSTEM, question)
        return _model_core(stack, question, population, text), receipt
    except ProviderUnavailable as exc:
        return None, {'status': 'provider_unavailable', 'kind': exc.kind}
    except Exception as exc:  # noqa: BLE001 - the planner is optional; heal, don't block
        return None, {'status': 'failed', 'reason': f'{type(exc).__name__}: {exc}'[:300]}
    finally:
        if owned: ledger.close()


def plan(stack, question, population=None, *, maximum_patients=None, ledger=None, hosted=True):
    """-> {'subject_question','options','sub_questions','scope':{'patients','cohort','sources','window'},'aggregation','returns',
    'unit','select_labels','maximum_patients','planner','planner_receipt'} or None."""
    from .evidence_proposers import window
    core, planner, receipt = deterministic(stack, question, population), 'deterministic', None
    if core is None:
        try: core, planner = local_core(stack, question, population), 'qwen_typed_interpreter'
        except Exception:  # noqa: BLE001 - local model absent/busy: next planner
            core = None
    if core is None and hosted:
        core, receipt = hosted_core(stack, question, population, ledger)
        planner = 'hosted_claude'
    if core is None: return None
    as_of = stack.retriever.meta['snapshot']['as_of']
    spec = None
    if not core['patients']:
        from .cohorts import empty
        spec = empty()
        if core.get('concept'):
            spec['all'] = [{'kind': 'concept', 'concept': core['concept'], 'window': {'basis': 'all_observed', 'start': '', 'end': ''}}]
    subs = core['sub_questions']
    sources = ['passages'] + (['medication_state'] if any(s['subject'] for s in subs) else [])
    if any(s['task_key'] == 'atomic_why_factor' and s['subject'] for s in subs): sources.append('therapy_barriers')
    from . import external_evidence
    if any(external_evidence.needs_external(s['atom']) for s in subs): sources.append('external')
    cap = max(1, min(HARD_MAX_PATIENTS, int(maximum_patients or max_patients())))
    return {'schema': VERSION, 'question': question, 'subject_question': subs[0]['atom'], 'options': subs[0]['options'],
            'sub_questions': subs, 'scope': {'patients': list(core['patients'] or []) or None, 'cohort': spec,
                                             'population_concept': core.get('concept'), 'sources': sources,
                                             'window': window(question, as_of)},
            'aggregation': core['aggregation'], 'returns': core['returns'], 'unit': core['unit'],
            'select_labels': core['select_labels'], 'maximum_patients': cap, 'planner': planner, 'planner_receipt': receipt}


# -- parameters (the analysis_graph node contract) -------------------------------------------------------------------
def parameters(p):
    return {'question': p['question'][:1000], 'sub_questions': p['sub_questions'], 'sub_question_count': len(p['sub_questions']),
            'aggregation': p['aggregation'], 'returns': p['returns'], 'unit': p['unit'], 'select_labels': p['select_labels'],
            'maximum_patients': p['maximum_patients'], 'window': p['scope']['window'], 'sources': p['scope']['sources'],
            'providers': list(PROVIDERS), **({'denominator': 'evaluated_patients'} if p['returns'] == 'value' else {})}


def validate(p):
    """analysis_graph validator for the 'atomic_questions' node."""
    from .clinical_events import validate_window
    allowed = {'question', 'sub_questions', 'sub_question_count', 'aggregation', 'returns', 'unit', 'select_labels',
               'maximum_patients', 'window', 'sources', 'providers', 'denominator'}
    if not isinstance(p, dict) or set(p) - allowed or not {'question', 'sub_questions', 'aggregation', 'returns'} <= set(p):
        raise ValueError('atomic_questions parameters: question, sub_questions, aggregation, returns (+ unit, select_labels, '
                         'maximum_patients, window, sources, providers)')
    if not isinstance(p['question'], str) or not 1 <= len(p['question']) <= 1000: raise ValueError('Bounded original question required')
    subs = p['sub_questions']
    if not isinstance(subs, list) or not 1 <= len(subs) <= 8: raise ValueError('One to eight atomic sub-questions required')
    keys = set()
    for s in subs:
        if not isinstance(s, dict) or set(s) != {'key', 'atom', 'options', 'subject', 'task_key'}: raise ValueError('Invalid sub-question fields')
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,39}', str(s['key'])) or s['key'] in keys: raise ValueError('Unique sub-question keys required')
        keys.add(s['key'])
        if not isinstance(s['atom'], str) or not 1 <= len(s['atom']) <= 600: raise ValueError('Bounded atomic question required')
        o = s['options']
        if not isinstance(o, dict) or 'unknown' not in o or not 2 <= len(o) <= 10 or \
                any(not isinstance(k, str) or not isinstance(v, str) or not v for k, v in o.items()):
            raise ValueError("Typed options with 'unknown' required")
        if not isinstance(s['subject'], str) or not isinstance(s['task_key'], str): raise ValueError('Subject and task key are strings')
        if s['subject']:
            from .medication_state import subjects
            subjects([s['subject']])
    if p['aggregation'] not in AGGREGATIONS: raise ValueError(f'aggregation: {AGGREGATIONS}')
    if p['returns'] not in RETURNS: raise ValueError(f'returns: {RETURNS}')
    unit = p.get('unit', 'patient')
    if unit not in UNITS or (p['aggregation'] in ('conflict', 'trajectory')) != (unit == 'document'):
        raise ValueError('conflict/trajectory read per document; the other aggregations per patient')
    labels = p.get('select_labels') or []
    if not isinstance(labels, list) or any(not any(l in s['options'] for s in subs) for l in labels):
        raise ValueError('select_labels must be option labels')
    cap = p.get('maximum_patients', max_patients())
    if type(cap) is not int or not 1 <= cap <= HARD_MAX_PATIENTS: raise ValueError(f'maximum_patients: 1..{HARD_MAX_PATIENTS}')
    sources = p.get('sources') or ['passages']
    if not isinstance(sources, list) or any(s not in SOURCES for s in sources): raise ValueError(f'sources: {SOURCES}')
    providers = p.get('providers') or list(PROVIDERS)
    if not isinstance(providers, list) or not providers or any(x not in PROVIDERS for x in providers): raise ValueError(f'providers: {PROVIDERS}')
    if p.get('denominator') not in (None, 'evaluated_patients'): raise ValueError('denominator: evaluated_patients')
    return {'question': p['question'], 'sub_questions': subs, 'sub_question_count': len(subs), 'aggregation': p['aggregation'],
            'returns': p['returns'], 'unit': unit, 'select_labels': labels, 'maximum_patients': cap,
            'window': validate_window(p.get('window')), 'sources': sources, 'providers': providers,
            **({'denominator': p['denominator']} if p.get('denominator') else {})}


# -- evidence --------------------------------------------------------------------------------------------------------
def medication_rows(stack, ids, subjects_):
    """Governed medication_state rows for these patients x subjects -> {(patient, subject): row}."""
    if not ids or not subjects_: return {}
    import sqlite3
    from . import medication_state, semantic_layer
    path = Path(stack.path)
    target = semantic_layer.ensure(path) if (path/'run.json').exists() else path/'semantic.sqlite3'  # as medication_state.execute
    if not Path(target).exists(): return {}
    db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    try:
        rows = medication_state.classify(db, stack.retriever.meta['snapshot']['as_of'][:10], sorted(set(subjects_)), keep=set(ids))
    finally:
        db.close()
    return {(r['patient_id'], r['subject']): r for r in rows}


def structured_evidence(row):
    if not row: return None
    from .medication_state import VERSION as MS
    text = (f"Structured medication record ({MS}) for {row['subject'].replace('_', ' ')}: state {row['state']}; {row['basis']}; "
            f"last fill {row.get('last_fill_date') or 'none'}, covered through {row.get('covered_through') or 'n/a'}, "
            f"last listed {row.get('last_listed_date') or 'none'}, stop/barrier {row.get('stop_evidence') or 'none'}.")
    day = row.get('stop_date') or row.get('last_fill_date') or row.get('last_listed_date') or 'undated'
    return {'id': f"medstate:{row['patient_id']}:{row['subject']}", 'text': text[:TEXT_CHARS], 'date': str(day)[:10],
            'note_id': (row.get('evidence_record_ids') or 'medication_state').split(',')[0] or 'medication_state'}


def passage_evidence(row):
    s = row.get('source') or {}
    return {'id': f"passage:{row['id']}", 'text': (row.get('text') or '')[:TEXT_CHARS],
            'date': str(s.get('event_date') or 'undated')[:10], 'note_id': str(s.get('record_id') or row.get('note_id') or '')}


def dated(e, as_of):
    """Provider requests need an ISO date on or before the cutoff: an undated source carries the as-of date and says so in
    its text; a source dated after the cutoff never enters a request (-> None)."""
    cutoff = str(as_of)[:10]
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', e['date'] or ''):
        return e if e['date'] <= cutoff else None
    return {**e, 'date': cutoff, 'text': ('[undated source] ' + e['text'])[:TEXT_CHARS]}


def retrieval_query(sub):
    words = sub['atom'].replace('For this patient only, does the submitted evidence establish:', '')
    return (words + ' ' + (sub['subject'] or '').replace('_', ' ')).strip()[:1500] or 'medication'


# -- System One calls -------------------------------------------------------------------------------------------------
def _memo_get(key):
    with _MEMO_LOCK:
        if key in _MEMO:
            _MEMO.move_to_end(key); return _MEMO[key]
    return None


def _memo_put(key, value):
    with _MEMO_LOCK:
        _MEMO[key] = value
        while len(_MEMO) > MEMO_SIZE: _MEMO.popitem(last=False)


def _spent(decision):
    """(new paid calls, usd) of one decide() result, from its steps when reported."""
    if decision.get('memo_hit'): return 0, 0.0
    steps = decision.get('steps') or [decision]
    calls, usd = 0, 0.0
    for s in steps:
        if s.get('cache_hit') or s.get('status') in ('failed', 'error', 'skipped', 'below_threshold') or s.get('error'): continue
        if s.get('provider') == 'local_student': continue  # the free local student is never a paid call
        if s.get('label') is None and s.get('probabilities') is None: continue
        calls += 1
        c = s.get('cost_usd')
        usd += float(c) if isinstance(c, (int, float)) else COST_PER_CALL.get(s.get('provider'), 0.0)
    return calls, usd


def ask(question, options, evidence, *, as_of, task_key, providers, allow_live, decide=None):
    """One memoized System One decision; never raises (a failure is an 'unknown' with its error)."""
    key = digest([VERSION, question, options, evidence, as_of, task_key, list(providers)])
    hit = _memo_get(key)
    if hit is not None: return {**hit, 'memo_hit': True}
    try:
        if decide is None:
            from . import system_one
            decide = system_one.decide
        from . import system_one_deciders
        order = system_one_deciders.order_for(task_key, providers)  # decider bake-off primary first
        out = dict(decide(question, options, evidence, as_of=as_of, order=order, task_key=task_key, allow_live=allow_live))
    except Exception as exc:  # noqa: BLE001 - both providers failing is an unknown, not an outage of the whole answer
        return {'label': 'unknown', 'probabilities': None, 'evidence_ids': [], 'error': f'{type(exc).__name__}: {exc}'[:300],
                'decided_by': None, 'steps': [], 'memo_hit': False}
    if out.get('status') in ('providers_failed', 'prepared'):  # no provider answered (outage, or not live and not cached)
        return {**out, 'label': 'unknown', 'error': out.get('status') + ': ' + '; '.join(
            str(st.get('error') or st.get('status')) for st in out.get('steps') or [])[:300], 'memo_hit': False}
    if out.get('label') not in options: out['label'] = 'unknown'
    _memo_put(key, out)
    return {**out, 'memo_hit': False}


def workers():
    try: return max(1, min(16, int(os.environ.get('AGERE_ATOMIC_WORKERS', '4'))))
    except ValueError: return 4


def run(stack, patient_ids, params, as_of, *, decide=None):
    """Execute validated parameters over patient_ids (already the scoped membership). -> report (see answer())."""
    started = time.perf_counter()
    p = validate(params)
    scoped = sorted(patient_ids)
    ids = scoped[:p['maximum_patients']]
    subs = p['sub_questions']
    meds = medication_rows(stack, ids, [s['subject'] for s in subs if s['subject']]) if 'medication_state' in p['sources'] else {}
    live = bool(getattr(stack, 'allow_live', False))
    barriers, barrier_receipts = {}, []
    if 'therapy_barriers' in p['sources']:  # build facts + live tier-3 cascade (therapy_barrier_answers)
        from . import therapy_barrier_answers as tba
        for subject in dict.fromkeys(s['subject'] for s in subs if s['subject'] and s['task_key'] == 'atomic_why_factor'):
            try:
                ev, rec = tba.evidence(stack, ids, subject, as_of=as_of, live=True, decide=decide)
            except Exception as exc:  # noqa: BLE001 - barrier facts are extra evidence; passages still answer
                ev, rec = {}, {'status': 'failed', 'reason': f'{type(exc).__name__}: {exc}'[:200]}
            for pid, items in ev.items(): barriers[(pid, subject)] = items
            barrier_receipts.append({'subject': subject, **{k: v for k, v in (rec or {}).items() if k != 'decisions'},
                                     'facts': sum(len(v) for v in ev.values())})
    external, external_receipts = {}, []
    if 'external' in p['sources']:  # cited Perplexity Agent search for general criteria the warehouse cannot hold
        from . import external_evidence
        for s in subs:
            if not external_evidence.needs_external(s['atom']): continue
            q = external_evidence.reference_query(s['atom'], s['subject'])
            hit = next((r for r in external_receipts if r['query_in'] == q), None)
            if hit is None:
                res = external_evidence.search(q, allow_live=live)
                hit = {'query_in': q, 'result': res}; external_receipts.append(hit)
            item = external_evidence.evidence_item(hit['result'], as_of)
            if item: external[s['key']] = item
    packets = []
    tasks, passages = [], {}
    from .answer_budget import stop_check
    stop = stop_check()  # the tier's time share; bound here because pool threads below do not inherit the cascade's context
    for pid in ids:  # retrieval on this thread (the retriever's sqlite connection is not shared across threads)
        if stop(): raise QuestionNotUnderstood(f'tier time share exhausted while gathering evidence ({len(packets)} packets of {len(ids) * len(subs)})')
        for s in subs:
            if p['unit'] == 'document':
                found = stack.retriever.search(retrieval_query(s), patient=pid, limit=DOCUMENTS_PER_PATIENT)
            else:  # bounded subject-anchored packet with learned category selection (evidence_packets)
                from . import evidence_packets
                found = evidence_packets.packet(stack, retrieval_query(s), pid, subject=s['subject'] or None, question=p['question'],
                                                limit=PASSAGES_PER_ATOM + 2, window=p['window'])
                packets.append({'patient_id': pid, 'key': s['key'], **found['stats'], 'category_order': found['selection'].get('category_order')})
            rows = [passage_evidence(r) for r in found.get('evidence') or [] if r.get('text')]
            for r in found.get('evidence') or []:
                src = r.get('source') or {}
                passages[f"passage:{r['id']}"] = {'record_id': src.get('record_id'), 'field': src.get('field'),
                                                  'date': (src.get('event_date') or '')[:10] or None, 'text': r.get('text')}
            from .ocr_passages import atomic_evidence  # scanned/faxed document spans with page/bbox provenance
            rows += atomic_evidence(stack.path, pid, retrieval_query(s), passages, already={e['id'] for e in rows}, as_of=as_of)
            structured = structured_evidence(meds.get((pid, s['subject']))) if s['subject'] else None
            extra = (barriers.get((pid, s['subject']), []) if s['task_key'] == 'atomic_why_factor' else []) + \
                ([external[s['key']]] if s['key'] in external else [])
            if p['unit'] == 'document':
                for doc in sorted(rows, key=lambda r: r['date']):
                    tasks.append((pid, s, doc, [doc]))
            else:
                tasks.append((pid, s, None, ([structured] if structured else []) + extra + rows))
    normal = []
    for pid, s, doc, ev in tasks:
        if doc is None:
            normal.append((pid, s, None, [d for d in (dated(e, as_of) for e in ev) if d]))
        elif dated(doc, as_of):
            normal.append((pid, s, dated(doc, as_of), [dated(doc, as_of)]))
    tasks = normal

    def one(task):
        if stop(): raise QuestionNotUnderstood('tier time share exhausted before every sub-question was asked')  # stop spending
        pid, s, doc, evidence = task
        return ask(s['atom'], s['options'], evidence, as_of=as_of[:10], task_key=s['task_key'], providers=p['providers'],
                   allow_live=live, decide=decide)
    with ThreadPoolExecutor(max_workers=workers()) as pool:
        decisions = list(pool.map(one, tasks))
    cells, calls, usd, errors, by = [], 0, 0.0, 0, {}
    for (pid, s, doc, evidence), d in zip(tasks, decisions):
        c, u = _spent(d)
        calls, usd = calls + c, usd + u
        errors += bool(d.get('error'))
        by[d.get('decided_by') or 'none'] = by.get(d.get('decided_by') or 'none', 0) + 1
        texts = {e['id']: e for e in evidence}
        cited = [i for i in (d.get('evidence_ids') or []) if i in texts] or ([evidence[0]['id']] if evidence and d['label'] != 'unknown' else [])
        cells.append({'patient_id': pid, 'key': s['key'], 'label': d['label'], 'probabilities': d.get('probabilities'),
                      'calibrated_probabilities': d.get('calibrated_probabilities'), 'decided_by': d.get('decided_by'),
                      'document': doc['id'] if doc else None, 'date': doc['date'] if doc else None,
                      'error': d.get('error'), 'evidence_count': len(evidence),
                      'sources': [{'patient_id': pid, 'evidence_id': i, 'text': texts[i]['text'], 'date': texts[i]['date'],
                                   'record_id': texts[i]['note_id'], **({k: v for k, v in passages[i].items() if k in ('field', 'page', 'bbox', 'citation')}
                                                                      if i in passages else {})}
                                  for i in cited[:3]]})
    structured_states = {f'{pid}|{s}': r['state'] for (pid, s), r in meds.items()}
    for rec in barrier_receipts:
        calls, usd = calls + int(rec.get('new_paid_api_calls') or 0), usd + float(rec.get('estimated_usd') or 0.0)
    for rec in external_receipts:
        if rec['result'].get('cost_usd'): calls, usd = calls + 1, usd + float(rec['result']['cost_usd'])
    return {'schema': VERSION, 'as_of': as_of[:10], 'parameters': p, 'population_patients': len(scoped), 'evaluated_patient_ids': ids,
            'capped': len(scoped) > len(ids), 'cap': p['maximum_patients'], 'cells': cells, 'structured_states': structured_states,
            'system_one': {'decisions': len(cells), 'new_paid_api_calls': calls, 'estimated_usd': round(usd, 6), 'errors': errors,
                           'decided_by': by, 'memo_hits': sum(1 for d in decisions if d.get('memo_hit'))},
            'evidence_packets': packets, 'therapy_barriers': barrier_receipts,
            'external_evidence': [{'query': r['query_in'], 'status': r['result'].get('status'), 'sources': r['result'].get('sources'),
                                   'cached': r['result'].get('cached'), 'cost_usd': r['result'].get('cost_usd')} for r in external_receipts],
            'new_paid_api_calls': calls, 'do_not_cache': errors > 0 or not cells,
            'elapsed_seconds': round(time.perf_counter() - started, 3), 'clinical_decision_authority': False}


def execute(stack, plan_, *, decide=None):
    """Run a plan directly (no graph): resolve its scope, call System One, aggregate. -> {'report', 'answer'}."""
    as_of = stack.retriever.meta['snapshot']['as_of']
    ids = plan_['scope']['patients']
    if not ids:
        from .cohorts import select
        ids = select(stack.path, plan_['scope']['cohort'])['patient_ids']
    report = run(stack, ids, parameters(plan_), as_of, decide=decide)
    graph = {'coverage': 'supported_observation', 'unmet_requirements': []}
    return {'report': report, 'answer': answer(report, graph, plan_['question'])}


# -- aggregation / answer ---------------------------------------------------------------------------------------------
def _pretty(label): return str(label).replace('_', ' ')


def aggregate(report):
    p = report['parameters']
    agg, selected_labels = p['aggregation'], set(p['select_labels'])
    keys = [s['key'] for s in p['sub_questions']]
    by_patient = {pid: [c for c in report['cells'] if c['patient_id'] == pid] for pid in report['evaluated_patient_ids']}
    table, chosen = [], []
    for pid, cells in by_patient.items():
        row = {'patient_id': pid}
        if agg in ('conflict', 'trajectory'):
            for k in keys:
                seq = sorted((c for c in cells if c['key'] == k), key=lambda c: c['date'] or '')
                labels = [c['label'] for c in seq if c['label'] != 'unknown']
                if agg == 'conflict':
                    row[k] = 'conflict' if len(set(labels)) > 1 else (labels[0] if labels else 'unknown')
                    row[f'{k}_labels'] = sorted(set(labels))
                else:
                    changes = [f"{c['date'] or 'undated'}: {_pretty(c['label'])}" for i, c in enumerate(seq)
                               if c['label'] != 'unknown' and (i == 0 or c['label'] != next((x['label'] for x in reversed(seq[:i]) if x['label'] != 'unknown'), None))]
                    row[k] = labels[-1] if labels else 'unknown'
                    row[f'{k}_trajectory'] = changes
            hit = any(row[k] == 'conflict' for k in keys) if agg == 'conflict' else any(row[k] in selected_labels for k in keys)
        else:
            for c in cells:
                row[c['key']] = c['label']
                if c['probabilities']: row[f"{c['key']}_p"] = round(float((c['probabilities'] or {}).get(c['label'], 0) or 0), 3)
                for s in p['sub_questions']:
                    if s['key'] == c['key'] and s['subject'] and f"{pid}|{s['subject']}" in report['structured_states']:
                        row[f"{c['key']}_structured"] = report['structured_states'][f"{pid}|{s['subject']}"]
            hit = any(row.get(k) in selected_labels for k in keys)
        table.append(row)
        if hit: chosen.append(pid)
    return table, chosen


def answer(report, plan_, question):
    """Answer contract for analysis_graph.build_answer_core."""
    p = report['parameters']
    table, chosen = aggregate(report)
    n, total = len(report['evaluated_patient_ids']), report['population_patients']
    partial = report['capped'] or plan_.get('coverage') != 'supported_observation' or bool(plan_.get('unmet_requirements'))
    cap_note = (f"Capped: System One read the first {n} of {total} patients in scope (maximum {report['cap']} per call); "
                f'the remaining {total - n} were not read.') if report['capped'] else None
    sources = [s for c in report['cells'] if c['label'] != 'unknown' for s in c['sources']]
    probabilities = {}
    for c in report['cells']:
        probabilities.setdefault(c['patient_id'], {})[c['key'] + (f"@{c['document']}" if c['document'] else '')] = c['probabilities']
    keys = [s['key'] for s in p['sub_questions']]
    returns = p['returns']
    if returns == 'verdict' and len(table) == 1 and len(keys) == 1:
        label = table[0].get(keys[0], 'unknown')
        value = {'yes': 'Yes: the chart supports it', 'no': 'No: the chart contradicts it'}.get(label, f'{_pretty(label).capitalize()}: the chart does not settle it')
        value += f" ({report['evaluated_patient_ids'][0]}; {p['sub_questions'][0]['atom']})"
        out = {'answer_type': 'value', 'value': value, 'count': None, 'patient_ids': report['evaluated_patient_ids']}
    elif returns == 'count':
        out = {'answer_type': 'count', 'count': len(chosen), 'patient_ids': chosen,
               'value': f"{len(chosen)} of {n} evaluated patients ({' or '.join(map(_pretty, p['select_labels']))})"}
    elif returns == 'value':
        share = len(chosen) / n if n else None
        out = {'answer_type': 'value', 'count': len(chosen), 'patient_ids': chosen,
               'value': (f"{share:.1%} ({len(chosen)} of {n} evaluated patients)" if share is not None else 'No patients evaluated')}
    elif returns == 'patient_set':
        out = {'answer_type': 'patient_set', 'count': len(chosen), 'patient_ids': chosen,
               'value': f"{len(chosen)} of {n} evaluated patients: {', '.join(chosen[:20])}{' ...' if len(chosen) > 20 else ''}"}
    else:
        summary = '; '.join(f"{k}: " + ', '.join(f'{_pretty(l)} {sum(1 for r in table if r.get(k) == l)}'
                                                  for l in dict.fromkeys(r.get(k) for r in table) if l) for k in keys)
        out = {'answer_type': 'table', 'count': len(table), 'patient_ids': chosen,
               'value': f'{n} patients x {len(keys)} sub-questions ({summary})'}
    if cap_note: out['value'] = f"{out['value']} [{cap_note}]"
    s1 = report['system_one']
    method = (f"{VERSION}: {len(keys)} atomic typed sub-question(s) asked of each patient's "
              f"{'documents one at a time' if p['unit'] == 'document' else 'ranked passages'}"
              f"{' and governed medication_state rows' if 'medication_state' in p['sources'] else ''}"
              f"{', therapy-barrier facts (build + live tier-3 cascade)' if 'therapy_barriers' in p['sources'] else ''}"
              f"{', cited external references (Perplexity Agent search)' if 'external' in p['sources'] else ''} by System One "
              f"(Jev first, Perplexity when Jev is unsure); aggregated in code by '{p['aggregation']}'.")
    limitations = [method, f"System One decisions {s1['decisions']} (new paid calls {s1['new_paid_api_calls']}, ~${s1['estimated_usd']:.4f}; "
                           f"decided by {s1['decided_by']}); a typed reading of bounded ranked passages, not clinical adjudication."]
    if s1['errors']: limitations.append(f"{s1['errors']} System One decision(s) failed and are reported as unknown.")
    if cap_note: limitations.insert(0, cap_note)
    return {**out, 'status': 'partial' if partial else 'answered', 'table': table, 'sources': sources,
            'probabilities': probabilities, 'method': method, 'limitations': limitations,
            'capped': {'cap': report['cap'], 'evaluated': n, 'in_scope': total} if report['capped'] else None,
            'sub_questions': [{'key': s['key'], 'atom': s['atom'], 'options': list(s['options'])} for s in p['sub_questions']],
            'membership_role': 'selected'}


# -- cascade rung -----------------------------------------------------------------------------------------------------
def graph_of(question, plan_):
    from .evidence_proposers import cohort_node, graph_of as base
    scope = plan_['scope']
    if scope['patients']:
        population = cohort_node(scope['patients'], f"The named patient(s): {', '.join(scope['patients'][:10])}")
    else:
        label = scope.get('population_concept') or 'all observed patients'
        population = {'id': 'population', 'operation': 'cohort', 'inputs': [], 'parameters': {'specification': scope['cohort']},
                      'purpose': f'Patients in scope: {label}'}
    node = {'id': 'atomic', 'operation': 'atomic_questions', 'inputs': ['population'], 'parameters': parameters(plan_),
            'purpose': 'Ask each atomic typed sub-question of each patient via System One (Jev, then Perplexity); aggregate in code'}
    return base(question, [population, node])


def tier(stack, question, population=None, *, maximum_patients=None, decide=None, ledger=None, early_check=None):
    """Ladder-rung contract: (graph, receipt) or None. System One is called here once (memoized), so the receipt carries
    the exact new calls and cost and stack.run_graph replays the same decisions free."""
    if not getattr(stack, 'allow_live', False) and os.environ.get('AGERE_ATOMIC_OFFLINE') != 'on': return None
    started = time.perf_counter()
    # In the cascade, planning stays free (patterns, local Qwen): Claude planning is the hosted interpreter tier's job, so this
    # tier never spends a Claude call ahead of it (AGERE_ATOMIC_HOSTED_PLANNER=on restores the hosted planner).
    p = plan(stack, question, population, maximum_patients=maximum_patients, ledger=ledger,
             hosted=os.environ.get('AGERE_ATOMIC_HOSTED_PLANNER', 'off') == 'on')
    if p is None: return None
    as_of = stack.retriever.meta['snapshot']['as_of']
    graph = graph_of(question, p)
    from .analysis_graph import validate as validate_graph
    validate_graph(graph, as_of)
    if early_check: early_check(graph)  # the cascade's literal check on the plan, before the first paid decision
    pre = execute(stack, p, decide=decide)['report']
    s1 = pre['system_one']
    if pre['cells'] and s1['errors'] == len(pre['cells']): return None  # System One unavailable for every decision: next proposer
    planner_receipt = p.get('planner_receipt') or {}
    steps = [{'name': f"planner:{p['planner']}", 'receipt': planner_receipt if planner_receipt.get('cost_estimate') else {}},
             {'name': 'system_one', 'receipt': {'cost_estimate': {'estimated_usd': s1['estimated_usd']}, 'cache_hit': s1['new_paid_api_calls'] == 0,
                                                'calls': s1['new_paid_api_calls'], 'decided_by': s1['decided_by']}}]
    planner_calls = 1 if planner_receipt.get('cost_estimate') and not planner_receipt.get('cache_hit') else 0
    return graph, {'route': 'atomic_system_one', 'model': 'system_one (jev -> perplexity)', 'frontier_called': False,
                   'new_paid_api_calls': s1['new_paid_api_calls'] + planner_calls, 'steps': steps,
                   'cost_class': 'Jev/Perplexity ~$0.0002 per patient', 'planner': p['planner'],
                   'components': ['atomic_questions', 'system_one', 'decider_bakeoff', 'system_one_student', 'jev_typesafe_review',
                                  'perplexity_decisions_review', 'warehouse_retriever', 'evidence_packets', 'learned_evidence_selection']
                                 + (['medication_state'] if 'medication_state' in p['scope']['sources'] else [])
                                 + (['therapy_barrier_cascade', 'therapy_barrier_classifier'] if 'therapy_barriers' in p['scope']['sources'] else [])
                                 + (['perplexity_agent_search'] if 'external' in p['scope']['sources'] else []),
                   'preflight': {'patients_in_scope': pre['population_patients'], 'evaluated': len(pre['evaluated_patient_ids']),
                                 'capped': pre['capped'], 'decisions': s1['decisions'], 'errors': s1['errors'],
                                 'therapy_barriers': pre.get('therapy_barriers'), 'external_evidence': pre.get('external_evidence')},
                   'atomic_version': VERSION, 'elapsed_seconds': round(time.perf_counter() - started, 3)}
