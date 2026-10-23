"""Chart reader: answers a question about ONE patient from every structured source, or from the notes through note frames.

  resolve_subject(stack, text)       -> {'patient_id', 'quote'} | None        (spec interface; patient_ids does the matching)
  answer(stack, spec, *, as_of, ledger)  -> answer contract                    (spec interface; B's spec path calls this)
  ask(stack, question, ...)          -> answer contract                        (question text in, answer out; the dev-set entry)
  tier(stack, question, population=None, *, ledger=None) -> (graph, receipt) | None   (cascade rung, like atomic_questions.tier)

How a question is answered (docs/agere/CHART_READER.md):
  1. patient_ids resolves the subject: an exact id (SYN-/WHP-/P-), a member id, or a pronoun bound to the previous turn. Ambiguous or
     unknown references return a clarification, never a guess.
  2. chart_intents.parse types the question (intent, domain, concept, window, ordinal) with a grammar. Slots the grammar leaves open are
     chosen by System One (Jev, then Perplexity) from closed lists; if they stay open the question is declined, not guessed.
  3. A question about what the notes SAY goes to note_frames.answer_patient with a note predicate (frame, concept, statuses, detail, window),
     System One choosing the frame and concept from closed lists when the grammar is unsure.
  4. chart_queries runs the typed intent over the structured sources with the decision context (a named patient is never excluded for being
     deceased, and the answer says so).
Every answer carries the value or table with units and dates, the warehouse record ids it used, the decision-context receipt and a one-line
read-back of how the question was understood.
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from pathlib import Path

from . import chart_intents as CI
from . import chart_queries as Q
from . import chart_summary as CS
from . import decision_context as DC
from . import patient_ids as PI
from .question_input import QuestionNotUnderstood

VERSION = 'chart-reader/1'
OP = 'chart_reader'
SO_PROVIDERS = ('jev', 'perplexity')
NOTE_DOMAIN = 'notes'

# -- the store ----------------------------------------------------------------------------------------------------------------
_LEX, _LEX_LOCK = {}, threading.Lock()


def semantic_path(stack):
    path = Path(getattr(stack, 'path', stack))
    return path / 'semantic.sqlite3' if path.is_dir() else path


def connect(stack):
    return sqlite3.connect(f'file:{semantic_path(stack)}?mode=ro', uri=True, timeout=30)


def as_of_of(stack, db=None):
    try:
        return str(stack.retriever.meta['snapshot']['as_of'])[:10]
    except Exception:  # noqa: BLE001 - a light stack (tests, tools) has no retriever
        pass
    own = db is None
    db = db or connect(stack)
    try:
        row = db.execute("SELECT value FROM meta WHERE key = 'as_of'").fetchone()
        return str(row[0])[:10]
    finally:
        if own:
            db.close()


def lexicon_of(stack, db):
    path = semantic_path(stack)
    try:
        key = (str(path), path.stat().st_mtime_ns)
    except OSError:
        key = (str(path), 0)
    with _LEX_LOCK:
        if key not in _LEX:
            _LEX.clear()
            _LEX[key] = CI.drug_lexicon(db)
        return _LEX[key]


# -- subject ------------------------------------------------------------------------------------------------------------------
def subject_status(stack, text, prior=None):
    """patient_ids.resolve with the request's prior subject (set by the conversation hook) as the default for pronouns."""
    return PI.resolve(semantic_path(stack), text, prior or PI.current_prior())


def resolve_subject(stack, text):
    """Spec interface: {'patient_id', 'quote'} when the text names (or, in a follow-up, points at) exactly one patient, else None.
    An ambiguous, partial or unknown id returns None; call subject_status for the clarification message."""
    r = subject_status(stack, text)
    if r['status'] != 'found':
        return None
    return {'patient_id': r['patient_id'], 'quote': r['quote'] or r['patient_id'], 'how': r['how']}


# -- slot filling by System One from closed lists -----------------------------------------------------------------------------------
INTENT_OPTIONS = {
    'latest_value': 'The most recent value or current state of one thing',
    'trend': 'How a measure changed over time (better, worse, by how much)',
    'list': 'The items of a list (medications, allergies, visits, claims)',
    'count_in_window': 'How many times something happened (optionally in a window)',
    'last_event': 'The date of the most recent occurrence of an event',
    'timeline': 'Every dated result or event in order',
    'status': 'A yes or no: is it true that the patient has, is on, or had something',
    'sum': 'A total amount of money',
    'summary': 'A brief of the whole chart',
}
NOTE_FRAME_HINT = 'What the clinical notes, scanned documents or outside records SAY (not a structured table)'


class Spend:
    def __init__(self):
        self.calls, self.usd, self.errors = 0, 0.0, 0

    def add(self, decision):
        from .atomic_questions import _spent
        c, u = _spent(decision)
        self.calls += c
        self.usd += u
        if decision.get('error'):
            self.errors += 1

    def receipt(self):
        return {'new_paid_api_calls': self.calls, 'system_one_usd': round(self.usd, 6), 'system_one_errors': self.errors}


def _so_choice(question, options, *, as_of, task_key, decide, allow_live, spend):
    from .atomic_questions import ask
    d = ask(question, options, [], as_of=as_of, task_key=task_key, providers=SO_PROVIDERS, allow_live=allow_live, decide=decide)
    spend.add(d)
    label = d.get('label')
    return label if label in options and label != 'unknown' else None


def concept_options(domain):
    if domain == 'lab':
        from .concepts import MEASUREMENTS
        return {k: v['label'] for k, v in MEASUREMENTS.items()}
    if domain in ('medication', 'fill', 'prescription', 'returned_fill'):
        return {cls: cls.replace('_', ' ') for _, cls in CI.CLASS_TERMS}
    if domain == 'condition':
        from .concepts import CONDITIONS
        return {k: v['label'] for k, v in CONDITIONS.items()}
    if domain == 'immunization':
        return {like: like.rstrip('%') for _, like in CI.VACCINES}
    if domain == 'screening':
        return {test: label for _, test, label in CI.SCREENINGS}
    if domain == 'social_need':
        return {need: need.replace('_', ' ') for _, need in CI.NEEDS}
    return {}


def fill_gaps(it, question, as_of, *, decide=None, allow_live=False, spend=None):
    """Choose the slots the grammar left open from closed lists with System One. Returns the intent (gaps that stay open are kept)."""
    spend = spend or Spend()
    it = dict(it)
    gaps = list(it.get('gaps') or [])
    stem = f'Patient chart question: {question}'
    if 'domain' in gaps:
        options = {**{k: v for k, v in CI.DOMAINS.items()}, 'summary': 'A brief of the whole chart', NOTE_DOMAIN: NOTE_FRAME_HINT}
        label = _so_choice(f'{stem}\nWhich part of the patient chart does the question ask about?', options, as_of=as_of, task_key='chart_reader_domain',
                           decide=decide, allow_live=allow_live, spend=spend)
        if label:
            it['domain'] = label
            it['source'] = 'system_one'
            gaps.remove('domain')
    if 'intent' in gaps and it.get('domain') and it['domain'] != NOTE_DOMAIN:
        label = _so_choice(f'{stem}\nWhat kind of answer does the question want?', INTENT_OPTIONS, as_of=as_of, task_key='chart_reader_intent',
                           decide=decide, allow_live=allow_live, spend=spend)
        if label:
            it['intent'] = label
            it['source'] = 'system_one'
            gaps.remove('intent')
    if 'concept' in gaps and it.get('domain'):
        options = concept_options(it['domain'])
        if options:
            label = _so_choice(f'{stem}\nWhich one does the question ask about?', options, as_of=as_of, task_key=f"chart_reader_concept_{it['domain']}",
                               decide=decide, allow_live=allow_live, spend=spend)
            if label:
                kind = {'lab': 'lab', 'condition': 'condition', 'immunization': 'vaccine', 'screening': 'screening', 'social_need': 'need'}.get(it['domain'], 'class')
                it['concept'] = ({'kind': 'lab', 'value': label, 'concepts': [label]} if kind == 'lab' else {'kind': kind, 'value': label})
                it['source'] = 'system_one'
                gaps.remove('concept')
    it['gaps'] = gaps
    return it


# -- note questions ------------------------------------------------------------------------------------------------------------------
STRONG_NOTE = re.compile(
    r"\b(?:in (?:the |his |her |their )?notes?|the notes?|notes? (?:say|show|record|mention|document|list)|note (?:says|stopped|stated)|chart (?:say|show|mention)s?|"
    r"discussed|discussion|conversation|goals[- ]of[- ]care|code status|dnr|dni|resuscitat\w*|chest compressions|breathing tube|intubat\w*|"
    r"advance directive|polst|molst|living will|health ?care proxy|why was|why did|why is|reason (?:for|why|\w+ (?:was|were))|accept(?:ed)?|declin\w+|refus\w+|"
    r"(?:report|reports|reported|says|said)\s+(?:taking|using|having)|discharge summary|impression|x-?ray|radiolog\w+|ruled out|rule[- ]out|stress test|"
    r"memory concern|trouble|barriers?|intoleran\w+|falls?|fell|raised|later notes?|anywhere|mentioned|documented in|recommended|"
    r"outside (?:report|record|chest)|scanned|did the patient|does the patient|patient (?:choose|chose|want|wants|prefer))\b", re.I)
STRUCTURED_FIRST = re.compile(r"\b(?:ed visits?|admissions?|claims?|fills?|pick(?:ed)? up|prescribed|prescriptions?|mammogra\w+|flu shot|vaccin\w+|a1c|egfr|ldl|"
                              r"blood pressure|practice|attributed|allergies|medication list|active medications?)\b", re.I)
FRAME_CUES = [  # (frame, pattern); the first match wins, more specific first
    ('care_process', r"goals[- ]of[- ]care|code status|\bdnr\b|\bdni\b|resuscitat\w*|chest compressions|breathing tube|intubat\w*|advance directive|polst|molst|living will|health ?care proxy|counsel\w*|referr\w+|hospice|palliative"),
    ('preference_refusal', r"\baccept\w*|\bdeclin\w*|\brefus\w*|\bprefer\w*|\bwants?\b|\bchoose\b|\bchose\b|\bdecid\w*|\bagree\w*"),
    ('medication_event', r"\bstopped\b|\bdiscontinu\w*|\bheld\b|\bswitch\w*|\bstarted\b|\bstarting\b|\bdose\b|\btaking\b|\btakes\b|\bstill list\w*|\blist\w* (?:it|them)\b|\brestart\w*|\bwhy was\b.*\b(?:stopped|held)\b|\bmedication\b"),
    ('adverse_event', r"intoleran\w+|allerg\w+|side effects?|adverse|reaction|angioedema|\bfalls?\b|\bfell\b"),
    ('function_cognition', r"memory|cognit\w*|mobility|gait|balance|\badl\b|vision|hearing|home safety|dementia|\bfalls?\b"),
    ('social_need', r"social|financial|barriers?|transport\w*|getting to|food|housing|afford\w*|isolat\w*|utilit\w*|trouble"),
    ('procedure_test', r"x-?ray|imaging|\bct\b|\bmri\b|stress test|biopsy|colonoscop\w*|echo\w*|impression|radiolog\w*|ultrasound|\bekg\b|\becg\b|\btest\b|report"),
    ('plan_followup', r"follow[- ]?up|recommend\w*|return|disposition|discharged to|\bplan\b"),
    ('finding_value', r"smoking status|tobacco|score|\bvalue\b|reading|measure\w*"),
    ('condition', r"ruled out|rule[- ]out|diagnos\w+|history of|syndrome|disease"),
    ('symptom', r"symptom|complain\w*|reports? (?:pain|dyspnea|nausea)|\bpain\b"),
]
CONCEPT_PHRASES = [  # (cue pattern, canonical concept term) for notes concepts that are not a drug
    (r"goals[- ]of[- ]care", 'goals-of-care conversation'), (r"code status|\bdnr\b|\bdni\b|resuscitat\w*|chest compressions|breathing tube|intubat\w*", 'code status'),
    (r"advance directive|polst|molst|living will|health ?care proxy", 'advance directive'),
    (r"social or financial|financial barriers?|social barriers?|financial|afford\w*", 'social or financial barrier'),
    (r"trouble getting to appointments|transport\w*|getting to appointments", 'transportation to appointments'),
    (r"\bfalls?\b|\bfell\b", 'falls'), (r"memory (?:concern|problem|loss)|cognit\w*", 'memory concern'),
    (r"smoking status|tobacco", 'smoking status'), (r"drug intolerance|intoleran\w+", 'medication intolerance'),
    (r"chest x-?ray", 'chest x-ray'), (r"stress test", 'stress test'), (r"acute coronary syndrome|\bacs\b", 'acute coronary syndrome'),
    (r"discharge summary|follow[- ]?up", 'discharge follow-up'), (r"discharged to|disposition", 'discharge disposition'),
]
RETURN_CUES = [  # (cue, frame, detail field to show)
    (r"\bwhy\b|\breason", 'medication_event', 'reason'), (r"\bwhy\b|\breason", 'preference_refusal', 'reason'),
    (r"what was done|done about|response", 'social_need', 'response'), (r"what did the patient (?:choose|decide)|\bchoose\b|\bchose\b", 'care_process', 'outcome'),
    (r"\bdecision\b|accept|declin", 'preference_refusal', 'decision'), (r"how many|\bcount\b|number of", 'function_cognition', 'count'),
    (r"how many|number of", 'adverse_event', 'count'), (r"impression|result", 'procedure_test', 'result_text'),
    (r"discharged to|disposition|where", 'plan_followup', 'disposition'), (r"follow[- ]?up|recommend", 'plan_followup', 'item'),
]


def is_note_question(question, intent):
    q = question or ''
    if intent.get('domain') == NOTE_DOMAIN:
        return True
    if not STRONG_NOTE.search(q):
        return False
    if intent.get('domain') is None:
        return True
    # a crisp structured request wins unless the wording is about what a note says
    if STRUCTURED_FIRST.search(q) and not re.search(r'\bnotes?\b|\bdiscussed\b|\bchart say|\bdocument\w* in\b', q, re.I):
        return False
    return True


def _note_frames(question):
    q = question or ''
    found = []
    for frame, pat in FRAME_CUES:
        if re.search(pat, q, re.I) and frame not in found:
            found.append(frame)
    return found


def note_predicates(question, as_of, *, lexicon=None, prior=None, decide=None, allow_live=False, spend=None):
    """-> [predicate dict] from the grammar, System One choosing frame and concept from closed lists where the cues are silent."""
    spend = spend or Spend()
    q = question or ''
    window = CI.window_of(q, as_of)
    later = re.search(r'\b(?:a |the |an )?((?:19|20)\d\d-\d\d-\d\d)\b.*\blater notes?\b|\blater notes?\b.*\b((?:19|20)\d\d-\d\d-\d\d)\b', q, re.I)
    after = re.search(r'((?:19|20)\d\d-\d\d-\d\d)', q) if re.search(r'\blater\b|\bafter\b|\bsince\b', q, re.I) else None
    if after and not window:
        from datetime import date, timedelta
        start = (date.fromisoformat(after.group(1)) + timedelta(days=1)).isoformat()
        window = {'kind': 'after_date', 'start': start, 'end': str(as_of)[:10], 'start_exclusive': False, 'phrase': after.group(0)}
    frames = _note_frames(q)
    if not frames:
        from . import note_frames as NF
        label = _so_choice(f'Patient chart question: {q}\nWhich kind of note statement does the question ask about?',
                           {k: v['label'] for k, v in NF.FRAMES.items() if k != 'note_contradiction'}, as_of=as_of, task_key='chart_reader_note_frame',
                           decide=decide, allow_live=allow_live, spend=spend)
        frames = [label] if label else []
    if not frames:
        return []
    drug = CI._drug(q, lexicon)
    concept_term = None
    for pat, term in CONCEPT_PHRASES:
        if re.search(pat, q, re.I):
            concept_term = term
            break
    preds = []
    primary = frames[:1] if len(frames) == 1 else frames[:2]
    # a drug named in a medication/preference/adverse question is the concept of that frame; otherwise the canonical phrase
    for frame in primary:
        term, quote = None, None
        if drug and frame in ('medication_event', 'preference_refusal', 'adverse_event'):
            term, quote = drug['value'].replace('_', ' '), drug.get('term') or drug['value']
        elif concept_term:
            term, quote = concept_term, concept_term
        elif drug:
            term, quote = drug['value'].replace('_', ' '), drug.get('term') or drug['value']
        if not term:
            m = re.search(r"\b(?:about|of|for|on|regarding|re:?)\s+(?:the\s+|a\s+|an\s+)?([a-z][a-z -]{3,40}?)(?:\s+(?:in|for|at|during|when|and|from)\b|[?.,]|$)", q, re.I)
            term = quote = (m.group(1).strip() if m else None)
        if not term:
            continue
        statuses = ['denied'] if re.search(r'\bruled out\b|\bnot (?:present|taking)\b|\bdenies\b', q, re.I) and frame == 'condition' else ['documented']
        detail = {}
        if frame == 'medication_event':
            if re.search(r'\bstopped\b|\bdiscontinu\w*', q, re.I):
                detail['event'] = 'stopped'
            elif re.search(r'\bstarted\b|\bstarting\b|\bnew\b', q, re.I):
                detail['event'] = 'started'
        ret = [fld for cue, fr, fld in RETURN_CUES if fr == frame and re.search(cue, q, re.I)]
        pred = {'op': 'note', 'frame': frame, 'concept': {'term': term, 'codes': [], 'quote': quote}, 'statuses': statuses, 'quote': q[:300]}
        if detail:
            pred['detail'] = detail
        if ret:
            pred['return_detail'] = list(dict.fromkeys(ret))
        if window:
            pred['window'] = {'start': window['start'], 'end': window['end'], 'quote': window.get('phrase') or ''}
        preds.append(pred)
    return preds


def note_core(stack, predicates, patient_id, as_of, *, ledger=None, nf=None):
    """Run each note predicate through note_frames.answer_patient and merge into one chart-reader core."""
    if nf is None:
        from . import note_frames as nf  # builder A's module (spec: answer_patient)
    parts, sources, rows = [], [], []
    status_all = []
    for pred in predicates:
        try:
            pred = nf.validate_predicate(pred) if hasattr(nf, 'validate_predicate') else pred
        except ValueError:
            pred = {k: v for k, v in pred.items() if k not in ('detail', 'return_detail')}
        res = nf.answer_patient(stack, pred, patient_id, as_of=as_of, ledger=ledger)
        status = res.get('status', 'unknown')
        status_all.append(status)
        detail = res.get('detail') or {}
        shown = {k: v for k, v in detail.items() if not pred.get('return_detail') or k in pred['return_detail']} or detail
        ev = res.get('evidence') or []
        label = pred['frame'].replace('_', ' ')
        term = pred['concept']['term']
        words = {'documented': 'Documented', 'denied': 'Denied in the notes', 'possible': 'Possible (not confirmed)', 'planned_not_done': 'Planned, not shown as done',
                 'historical': 'Documented as history', 'other_person': 'About another person', 'not_mentioned': 'Not mentioned in the notes',
                 'unknown': 'Undecided: the evidence does not settle it'}
        line = f"{words.get(status, status)}: {label} - {term}"
        if shown:
            line += ' (' + '; '.join(f"{k}: {', '.join(map(str, v)) if isinstance(v, (list, tuple)) else v}" for k, v in shown.items() if v not in (None, [], '')) + ')'
        for e in ev[:3]:
            line += f" | {e.get('date') or ''} {e.get('note_type') or ''} {e.get('record_id') or ''}: \"{str(e.get('quote') or '')[:200]}\""
        parts.append(line)
        rows.append({'frame': pred['frame'], 'concept': term, 'status': status, 'detail': detail, 'p': res.get('p')})
        sources.extend(Q.src(e.get('note_type') or 'clinical_note', e.get('record_id'), e.get('date'), quote=e.get('quote')) for e in ev)
    positive = any(s in ('documented', 'possible', 'planned_not_done', 'historical') for s in status_all)
    headline = ' || '.join(parts) if parts else 'No note predicate could be built from the question'
    kind = 'note'
    return Q.core(kind, headline, value=headline, table=rows, sources=sources, facts={'statuses': status_all, 'positive': positive},
                  notes=['note frames: System One reads retrieved passages; code reduces them to one patient value; an undecided patient is never read as "no"'])


# -- the answer ---------------------------------------------------------------------------------------------------------------------
def validate_chart(chart):
    """Validate the typed `chart` part of a spec ({intent, domain, concept, window, ordinal, attribute, mode}). Raises ValueError."""
    if not isinstance(chart, dict):
        raise ValueError('chart must be an object')
    if chart.get('intent') not in CI.INTENTS:
        raise ValueError(f'chart.intent must be one of {list(CI.INTENTS)}')
    if chart.get('intent') != 'summary' and chart.get('domain') not in CI.DOMAINS and chart.get('domain') != NOTE_DOMAIN:
        raise ValueError(f'chart.domain must be one of {list(CI.DOMAINS)} or {NOTE_DOMAIN!r}')
    w = chart.get('window')
    if w is not None and (not isinstance(w, dict) or not {'start', 'end'} <= set(w)):
        raise ValueError('chart.window needs start and end (ISO dates)')
    return chart


def read_back(it, pid, as_of, *, note_predicates_=None, deceased=None):
    """One line: how the question was understood (intent, patient, window, source rule)."""
    domain, intent = it.get('domain'), it.get('intent')
    if domain == NOTE_DOMAIN or note_predicates_:
        parts = []
        for p in note_predicates_ or []:
            parts.append(f"{p['frame'].replace('_', ' ')} '{p['concept']['term']}' as {'/'.join(p['statuses'])}")
        win = (note_predicates_ or [{}])[0].get('window')
        return f"What the notes say for {pid}: " + '; '.join(parts) + (f" ({win['start']} to {win['end']})" if win else ' (all notes)') + f', as of {as_of}.'
    c = it.get('concept') or {}
    what = {
        'lab': lambda: (CI.LAB_LABELS.get(c.get('value'), (c.get('value') or 'laboratory result').replace('_', ' ')) if c.get('kind') == 'lab' else 'latest laboratory results'),
        'medication': lambda: f"{Q.concept_label(c) if c else 'active medications'}", 'fill': lambda: f"{Q.concept_label(c) if c else 'medication'} fills",
        'prescription': lambda: 'prescriptions', 'ed_visit': lambda: 'ED visits', 'inpatient': lambda: 'hospital admissions', 'observation': lambda: 'observation stays',
        'readmission': lambda: '30-day readmissions', 'claims': lambda: 'plan-paid claims', 'condition': lambda: (c.get('value') or 'conditions').replace('_', ' '),
    }.get(domain, lambda: CI.DOMAINS.get(domain, str(domain)).split(' (')[0].lower())()
    phrase = {'latest_value': 'Latest', 'trend': 'Trend of', 'list': 'List of', 'count_in_window': 'Count of', 'last_event': 'Date of the most recent',
              'timeline': 'Every dated', 'status': 'Whether the patient has / is on', 'sum': 'Total', 'summary': 'Brief of the whole chart for'}.get(intent, intent)
    if it.get('ordinal') == 2:
        phrase = 'The result before the latest'
    if domain == 'lab' and intent == 'latest_value' and it.get('mode') == 'any':
        phrase = 'Most recent (any current result)'
    tail = []
    if domain == 'lab' and intent in ('latest_value',) and it.get('mode') == 'latest' and not it.get('ordinal'):
        tail.append('latest result per concept')
    win = it.get('window')
    tail.append(CI.describe_window(win) if win else ('all recorded history' if intent not in ('latest_value', 'summary') else f'as of {as_of}'))
    if domain in ('ed_visit', 'inpatient', 'observation'):
        tail.append('all-source events (EHR, ADT, claims)')
    if deceased:
        tail.append('named patient kept although deceased')
    elif domain != 'summary':
        tail.append('named patient: the living-patient default does not apply')
    return f"{phrase} {what} for {pid}: " + '; '.join(tail) + '.'


def _intent_from_spec(spec):
    chart = spec.get('chart')
    if chart is not None:
        validate_chart(chart)
        return {'intent': chart['intent'], 'domain': chart.get('domain'), 'concept': chart.get('concept'), 'window': chart.get('window'), 'ordinal': chart.get('ordinal'),
                'attribute': chart.get('attribute'), 'mode': chart.get('mode', 'latest'), 'flags': list(chart.get('flags') or []), 'gaps': [], 'source': 'spec',
                'question': spec.get('question', '')}
    return None


def build_intent(stack, question, as_of, db, *, quotes=(), prior=None, decide=None, allow_live=False, spend=None):
    lex = lexicon_of(stack, db)
    it = CI.parse(question, as_of, quotes=quotes, lexicon=lex, prior=(prior or {}).get('frame'))
    if it.get('gaps'):
        it = fill_gaps(it, question, as_of, decide=decide, allow_live=allow_live, spend=spend)
    return it


def answer(stack, spec, *, as_of=None, ledger=None, decide=None, question=None, prior=None, allow_live=None, nf=None, narrative_model=None):
    """Spec interface. `spec`: {'subject': {'kind': 'patient', 'patient_id', 'quote'}, 'question': text, optional 'chart': typed intent
    (validate_chart), optional 'note': [note predicates]}. Returns the answer contract (status, answer_type, value / table, unit, count,
    headline, sources, patient_ids, subject, intent, read_back, decision_context, deceased, notes, limitations, cost)."""
    started = time.perf_counter()
    subject = (spec or {}).get('subject') or {}
    pid = subject.get('patient_id')
    question = question or spec.get('question') or ''
    spend = Spend()
    allow_live = bool(getattr(stack, 'allow_live', False)) if allow_live is None else allow_live
    if not pid:
        return unsupported('A single-patient answer needs a subject patient_id.', question, started, spend)
    db = connect(stack)
    try:
        as_of = str(as_of or as_of_of(stack, db))[:10]
        if db.execute('SELECT 1 FROM patient WHERE patient_id = ?', (pid,)).fetchone() is None:
            return unsupported(f'{pid} is not a patient in this warehouse.', question, started, spend, pid=pid)
        with DC.scope(question, as_of) as state:
            state.note('named_patient', {'patient_id': pid, 'rule': 'a named patient is never excluded for being deceased'})
            ch = Q.Chart(db, pid, as_of, lexicon_of(stack, db))
            note_preds = spec.get('note')
            it = _intent_from_spec(spec)
            if it is None and not note_preds:
                it = build_intent(stack, question, as_of, db, quotes=[subject.get('quote')], prior=prior, decide=decide, allow_live=allow_live, spend=spend)
                if is_note_question(question, it):
                    note_preds = note_predicates(it.get('question') or question, as_of, lexicon=ch.lexicon, prior=prior, decide=decide, allow_live=allow_live, spend=spend)
                    it = {**it, 'domain': NOTE_DOMAIN, 'intent': it.get('intent') or 'status'}
            if note_preds:
                it = it or {'intent': 'status', 'domain': NOTE_DOMAIN, 'gaps': [], 'flags': [], 'question': question}
                it = {**it, 'domain': NOTE_DOMAIN}
                core = note_core(stack, note_preds, pid, as_of, ledger=ledger, nf=nf)
            elif it.get('gaps'):
                missing = it['gaps']
                return unsupported(f'The question could not be typed: {", ".join(missing)} left open (System One did not settle them).', question, started, spend, pid=pid,
                                   intent=it, missing=missing)
            elif it['intent'] == 'summary':
                core = CS.summary(ch, it, semantic_path(stack).parent)
                if narrative_model is not None:
                    core['narrative'] = CS.narrate(core, model=narrative_model)
            elif it['domain'] == 'care_gap':
                core = CS.care_gaps(ch, it, semantic_path(stack).parent)
            else:
                if it['domain'] == 'lab' and it['intent'] in ('trend', 'timeline') or it.get('mode') == 'any':
                    state.lab_mode = 'any'
                core = Q.run(ch, it)
            if core is None:
                return unsupported(f"No reader for {it.get('intent')} over {it.get('domain')} (concept {it.get('concept')}).", question, started, spend, pid=pid, intent=it)
            receipt = DC.receipt(question, state)
            death = ch.death
    finally:
        db.close()
    return finish(core, pid, subject, it, as_of, question, receipt, death, note_preds, started, spend)


def unsupported(message, question, started, spend, *, pid=None, intent=None, missing=None):
    out = {'status': 'unsupported', 'answer_type': 'unsupported', 'value': None, 'count': None, 'patient_ids': [pid] if pid else [], 'table': [], 'sources': [],
           'headline': message, 'missing': missing or [message], 'intent': _clean_intent(intent), 'notes': [], 'limitations': [message],
           'cost': spend.receipt(), 'seconds': round(time.perf_counter() - started, 3), 'chart_reader': VERSION}
    if pid:
        out['subject'] = PI.make_subject(pid)
    return out


def _clean_intent(it):
    if not it:
        return None
    keep = ('intent', 'domain', 'concept', 'window', 'ordinal', 'attribute', 'mode', 'flags', 'gaps', 'source')
    return {k: it.get(k) for k in keep if k in it}


def finish(core, pid, subject, it, as_of, question, receipt, death, note_preds, started, spend):
    deceased = None
    headline = core['headline']
    if death:
        deceased = {'deceased': True, 'death_date': death['death_date'], 'source': death['death_source']}
        headline = f"{headline} [{pid} is deceased (death date {death['death_date']}); a named patient is answered even though the living-patient default would exclude them]"
    intent_clean = _clean_intent(it)
    subj = PI.make_subject(pid, subject.get('quote'), intent_clean)
    status = 'answered'
    value, count = core.get('value'), core.get('count')
    out = {'status': status, 'answer_type': core['answer_type'], 'value': value, 'unit': core.get('unit'), 'count': count,
           'headline': headline, 'table': core.get('table') or [], 'sources': core.get('sources') or [], 'patient_ids': [pid], 'subject': subj,
           'intent': intent_clean, 'read_back': read_back(it, pid, as_of, note_predicates_=note_preds, deceased=bool(death)),
           'decision_context': receipt, 'deceased': deceased, 'facts': core.get('facts') or {}, 'notes': core.get('notes') or [],
           'limitations': [n for n in (core.get('notes') or []) if n.startswith('window')] or [],
           'method': f'{VERSION}: typed intent over structured sources' + (' and note frames' if note_preds else '') + f'; as of {as_of}',
           'as_of': as_of, 'cost': spend.receipt(), 'seconds': round(time.perf_counter() - started, 3), 'chart_reader': VERSION}
    for extra in ('text', 'sections', 'narrative'):
        if extra in core:
            out[extra] = core[extra]
    if core['answer_type'] == 'none':
        out['answer_type'] = 'none'
    if core['answer_type'] == 'yes_no':
        out['yes_no'] = bool(value)
    return out


def ask(stack, question, *, conversation_id=None, prior=None, as_of=None, ledger=None, decide=None, allow_live=None, nf=None, narrative_model=None):
    """Question text in, answer contract out. Resolves the patient (id, member id, or pronoun via `prior` / the conversation memory),
    asks for a clarification when it cannot, then answers. A follow-up inherits the prior frame (the subject dict that answer() returned)."""
    started = time.perf_counter()
    spend = Spend()
    if prior is None and conversation_id:
        prior = PI.recall(semantic_path(stack).parent, conversation_id)
    sub = subject_status(stack, question, prior)
    if sub['status'] == 'none' and prior and prior.get('patient_id') and _elliptical(question):
        sub = {'status': 'found', 'patient_id': prior['patient_id'], 'quote': None, 'how': 'prior_turn', 'message': None}
    if sub['status'] != 'found':
        msg = sub['message'] or 'No patient is named in the question.'
        out = unsupported(msg, question, started, spend)
        out['status'] = 'needs_clarification' if sub['status'] in ('ambiguous', 'unknown', 'multiple') else 'unsupported'
        out['clarification'] = msg
        return out
    spec = {'subject': {'kind': 'patient', 'patient_id': sub['patient_id'], 'quote': sub['quote'] or sub['patient_id']}, 'question': question}
    out = answer(stack, spec, as_of=as_of, ledger=ledger, decide=decide, question=question, prior=prior, allow_live=allow_live, nf=nf, narrative_model=narrative_model)
    if conversation_id and out.get('subject'):
        PI.remember(conversation_id, out['subject'])
    return out


def _elliptical(question):
    q = (question or '').strip().lower()
    return len(q.split()) <= 10 and bool(re.match(r"^(?:and|what about|how about|also|then|so|the one|which|when was|when did|is it|was it)\b", q))


# -- cascade rung ---------------------------------------------------------------------------------------------------------------------
def tier(stack, question, population=None, *, ledger=None, decide=None):
    """Ladder-rung contract: (graph, receipt) or None. A single-patient question (a patient id, member id, or a pronoun in a conversation) is
    typed here; the graph carries one chart_reader node whose engine (chart_reader_op) answers it. Ambiguous / unknown ids decline with a
    clarification (QuestionNotUnderstood). Not a rung for a saved cohort (population) or a question that names no patient."""
    started = time.perf_counter()
    if population is not None:
        return None
    sub = subject_status(stack, question)
    if sub['status'] in ('ambiguous', 'unknown', 'multiple'):
        raise QuestionNotUnderstood(sub['message'])
    if sub['status'] != 'found':
        return None
    spend = Spend()
    allow_live = bool(getattr(stack, 'allow_live', False))
    db = connect(stack)
    try:
        as_of = as_of_of(stack, db)
        prior = PI.current_prior()
        it = build_intent(stack, question, as_of, db, quotes=[sub['quote']], prior=prior, decide=decide, allow_live=allow_live, spend=spend)
        note = is_note_question(question, it)
        preds = note_predicates(it.get('question') or question, as_of, lexicon=lexicon_of(stack, db), decide=decide, allow_live=allow_live, spend=spend) if note else None
    finally:
        db.close()
    if note and not preds:
        return None
    if not note and it.get('gaps'):
        return None
    params = {'patient_id': sub['patient_id'], 'question': question[:2000], 'subject_quote': sub['quote'] or sub['patient_id'], 'how': sub['how']}
    from .engine_suite import plan
    graph, info = plan(OP, params, f"Read one patient's chart: {sub['patient_id']}", question,
                       [f"patient {sub['patient_id']} ({sub['how']})", *( [f"intent {it.get('intent')} over {it.get('domain')}"] if not note else ['note frames'])])
    steps = [{'name': 'chart_reader.parse', 'receipt': {'cost_estimate': {'estimated_usd': spend.usd}, 'calls': spend.calls}}] if spend.calls else []
    components = ['chart_reader', 'patient_ids', 'decision_context'] + (['note_frames', 'system_one'] if note else []) + (['system_one'] if spend.calls else [])
    return graph, {'route': 'chart_reader', 'model': None, 'frontier_called': False, 'new_paid_api_calls': spend.calls, 'steps': steps,
                   'cost_class': 'free (grammar + SQL); System One ~$0.0002 per open slot or note passage', 'components': list(dict.fromkeys(components)),
                   'chart_reader': VERSION, 'intent': _clean_intent(it), 'subject': {'patient_id': sub['patient_id'], 'how': sub['how']},
                   'elapsed_seconds': round(time.perf_counter() - started, 3)}
