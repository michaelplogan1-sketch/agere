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

import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

from . import chart_intents as CI
from . import chart_notes as CN
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
    """patient_ids.resolve with the request's prior subject (set by the conversation hook) as the default for pronouns. An elliptical
    follow-up ("and the one before that?") with no patient and no pronoun also binds the prior subject."""
    prior = prior or PI.current_prior()
    r = PI.resolve(semantic_path(stack), text, prior)
    if r['status'] == 'none' and prior and prior.get('patient_id') and _elliptical(text):
        return {**r, 'status': 'found', 'patient_id': prior['patient_id'], 'quote': None, 'how': 'prior_turn'}
    return r


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
        gaps = list(gaps)
        options = {**{k: v for k, v in CI.DOMAINS.items()}, 'summary': 'A brief of the whole chart', NOTE_DOMAIN: NOTE_FRAME_HINT}
        label = _so_choice(f'{stem}\nWhich part of the patient chart does the question ask about?', options, as_of=as_of, task_key='chart_reader_domain',
                           decide=decide, allow_live=allow_live, spend=spend)
        if label:
            it['domain'] = label
            it['source'] = 'system_one'
            if label not in ('summary', NOTE_DOMAIN):
                it = CI.complete(it)
                gaps = list(it['gaps'])
            else:
                gaps.remove('domain')
                if label == 'summary':
                    it['intent'] = 'summary'
                    gaps = [g for g in gaps if g != 'intent']
    if 'intent' in gaps and it.get('domain') and it['domain'] not in (NOTE_DOMAIN, 'summary'):
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


# -- the answer ---------------------------------------------------------------------------------------------------------------------
def validate_chart(chart):
    """Validate the typed `chart` part of a spec ({intent, domain, concept, window, ordinal, attribute, mode}). Raises ValueError."""
    if not isinstance(chart, dict):
        raise ValueError('chart must be an object')
    if chart.get('intent') not in CI.INTENTS:
        raise ValueError(f'chart.intent must be one of {list(CI.INTENTS)}')
    if chart.get('intent') != 'summary' and chart.get('domain') not in CI.DOMAINS and chart.get('domain') != NOTE_DOMAIN:
        raise ValueError(f'chart.domain must be one of {list(CI.DOMAINS)} or {NOTE_DOMAIN!r}')
    if chart.get('intent') == 'compare_values':
        import math
        concept = chart.get('concept') or {}
        concepts = concept.get('concepts') or [concept.get('value')]
        comparisons = chart.get('comparisons')
        if chart.get('domain') != 'lab' or not isinstance(comparisons, list) or not comparisons:
            raise ValueError('Measurement comparisons require lab concepts and explicit bounds')
        if any(not isinstance(p, dict) or p.get('concept') not in concepts or
               p.get('operator') not in ('<', '<=', '>', '>=') or
               type(p.get('value')) not in (int, float) or not math.isfinite(p['value']) for p in comparisons):
            raise ValueError('Invalid explicit measurement comparison')
        if len({p['concept'] for p in comparisons}) != len(comparisons):
            raise ValueError('Multiple bounds for one measurement require explicit boolean composition')
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
        'lab': lambda: ((f"{c['condition'].replace('_', ' ')} control (" + ' and '.join(CI.LAB_LABELS.get(m, m.replace('_', ' ')) for m in c['concepts']) + ')'
                         if c.get('condition') and len(c.get('concepts') or []) > 1 else
                         CI.LAB_LABELS.get(c.get('value'), (c.get('value') or 'laboratory result').replace('_', ' ')))
                        if c.get('kind') == 'lab' else 'latest laboratory results'),
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
                'question': spec.get('question', ''), **({'comparisons': chart['comparisons']} if chart.get('comparisons') else {})}
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
            prior_frame = (prior or {}).get('frame') or {}
            choose = lambda text, options, key: _so_choice(text, options, as_of=as_of, task_key=key, decide=decide, allow_live=allow_live, spend=spend)  # noqa: E731
            it = _intent_from_spec(spec)
            if it is not None and not note_preds and prior_frame.get('note') and CN.is_elliptical(question) and CN.follows_note_question(it, prior_frame['note']):
                it = None  # "and morphine?" after a note question about a drug: the same note question about the new drug, not the active-medication list
            if it is None and not note_preds and prior_frame.get('note') and CN.is_elliptical(question):
                note_preds = CN.build(question, as_of, lexicon=ch.lexicon, prior_predicates=prior_frame['note'], choose=choose)
                it = {'intent': 'status', 'domain': NOTE_DOMAIN, 'gaps': [], 'flags': ['inherited_from_prior'], 'question': question}
            if it is None and not note_preds:
                it = build_intent(stack, question, as_of, db, quotes=[subject.get('quote')], prior=prior, decide=decide, allow_live=allow_live, spend=spend)
                follows = CN.back_references(question, prior_frame.get('note'))  # "and her PHQ-9 that day?": the next question about the same note encounter
                if CN.is_note_question(question, it) or follows:
                    note_preds = CN.build(question, as_of, lexicon=ch.lexicon, prior_predicates=prior_frame.get('note') if follows else None, choose=choose)
                    if not note_preds and follows and not CN.is_note_question(question, it):
                        note_preds = None  # the back-reference carried no note frame of its own: the structured reading stands
                if note_preds:
                    it = {**it, 'domain': NOTE_DOMAIN, 'intent': it.get('intent') or 'status', 'gaps': []}
            if note_preds:
                it = it or {'intent': 'status', 'domain': NOTE_DOMAIN, 'gaps': [], 'flags': [], 'question': question}
                it = {**it, 'domain': NOTE_DOMAIN}
                core = CN.render(stack, note_preds, pid, as_of, ledger=ledger, nf=nf, yes_no=CN.asks_yes_no(CN.asking_and_context(question or '')[0]) or (
                    'inherited_from_prior' in it.get('flags', []) and bool(prior_frame.get('yes_no'))), allow_live=allow_live)
            elif it is not None and it.get('domain') == NOTE_DOMAIN:
                return unsupported('The question reads as a note question but no note predicate could be built from it.', question, started, spend, pid=pid, intent=it)
            elif CN.dangling_reference(question) and not it.get('window') and not prior_frame:
                return unsupported('The question points at "that visit / that day" but no earlier question in this conversation names one; say which visit or date.',
                                   question, started, spend, pid=pid, intent=it)
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
    keep = ('intent', 'domain', 'concept', 'window', 'ordinal', 'attribute', 'mode', 'flags', 'gaps', 'source', 'comparisons')
    return {k: it.get(k) for k in keep if k in it}


def finish(core, pid, subject, it, as_of, question, receipt, death, note_preds, started, spend):
    deceased = None
    headline = core['headline']
    if death:
        deceased = {'deceased': True, 'death_date': death['death_date'], 'source': death['death_source']}
        headline = f"{headline} [{pid} is deceased (death date {death['death_date']}); a named patient is answered even though the living-patient default would exclude them]"
    intent_clean = _clean_intent(it)
    if note_preds:
        intent_clean = {**(intent_clean or {}), 'note': [{k: v for k, v in p.items() if k not in ('_anchor', '_response')} for p in note_preds],
                        'yes_no': core.get('facts', {}).get('lead') is not None}
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
    if sub['status'] != 'found':
        msg = sub['message'] or 'No patient is named in the question.'
        out = unsupported(msg, question, started, spend)
        out['status'] = 'needs_clarification' if sub['status'] in ('ambiguous', 'unknown', 'multiple') else 'unsupported'
        out['clarification'] = msg
        return out
    spelled = sub['how'] in ('patient_id', 'member_id')
    spec = {'subject': {'kind': 'patient', 'patient_id': sub['patient_id'], 'quote': (sub['quote'] if spelled else None)}, 'question': question}
    out = answer(stack, spec, as_of=as_of, ledger=ledger, decide=decide, question=question, prior=prior, allow_live=allow_live, nf=nf, narrative_model=narrative_model)
    if conversation_id and out.get('subject'):
        PI.remember(conversation_id, out['subject'])
    return out


def _elliptical(question):
    q = (question or '').strip().lower()
    refers = re.match(r"^(?:and|what about|how about|also|then|the one|the previous|the prior)\b|^(?:is|was|are|were)\s+(?:that|it|this)\b", q) or re.search(
        r"\b(?:most recent|latest|last|previous|prior|first|earlier|next) one\b|\bthat one\b|\bthose\b|"
        r"^(?:date|result|value) of (?:the )?(?:most recent|latest|last|previous|prior)\b", q)
    return (len(q.split()) <= 12 and bool(refers)
            and not re.search(r'\b(?:patients|members|practice|practices|cohort|population|everyone|by (?:practice|payer|pcp)|group)\b', q))


# -- cascade rung ---------------------------------------------------------------------------------------------------------------------
def typed_chart(it, preds=None, question=None):
    """The typed chart request the graph executes, so the literal check reads what was understood (concept, intent, window,
    ordering, note predicates) instead of only the restated question (the same admission rule as every other plan)."""
    def plain(v):
        return json.loads(json.dumps(v, default=str))
    concept = it.get('concept') or {}
    out = {'intent': it.get('intent'), 'domain': it.get('domain'), 'mode': it.get('mode'), 'ordinal': it.get('ordinal'),
           'attribute': it.get('attribute'), 'window': plain(it.get('window')),
           'concept': plain({k: v for k, v in concept.items() if v is not None}) if isinstance(concept, dict) else plain(concept),
           'flags': list(it.get('flags') or [])}
    if it.get('comparisons'):
        out['comparisons'] = plain(it['comparisons'])
    if it.get('domain') == 'readmission':  # the chart reader's readmission is stay.readmitted_30d: say so, so another window is caught
        out['measure'] = {'field': 'stay.readmitted_30d', 'within_days': 30, 'window': '30 days after discharge_date', 'after': 'discharge_date'}
    if it.get('mode') in ('latest', 'first') or it.get('ordinal') or it.get('intent') in ('latest_value', 'last_event', 'compare_values'):
        order_field = 'end_date' if 'order_by_discharge' in (it.get('flags') or []) else 'event_date'
        out['order_by'] = [{'field': order_field, 'direction': 'asc' if it.get('mode') == 'first' else 'desc'}]
        out['limit'] = 1 + (int(it['ordinal']) if isinstance(it.get('ordinal'), int) else 0)
        if isinstance(it.get('ordinal'), int) and it['ordinal'] > 1:
            out['offset'] = it['ordinal'] - 1  # "the one before that": skip the latest 1
    if it.get('intent') == 'trend' and it.get('domain') == 'lab' and not it.get('window') and 'delta' not in (it.get('flags') or []):
        # the direction is read from the two most recent results (chart_queries.lab_trend): say so, so "compare the last two" is carried
        out['order_by'] = [{'field': 'event_date', 'direction': 'desc'}]
        out['limit'] = 2
    if preds:
        out['note_predicates'] = plain(preds)
        # what the wording restricted the reading to, stated at the top level so the literal check sees it without digging
        srcs = list(dict.fromkeys(s for p in preds for s in p.get('sources') or []))
        types = list(dict.fromkeys(t for p in preds for t in p.get('note_types') or []))
        days = list(dict.fromkeys(f"{p['window']['start']}" if p['window']['start'] == p['window']['end'] else f"{p['window']['start']}..{p['window']['end']}"
                                  for p in preds if p.get('window')))
        opens = list(dict.fromkeys(f"{p['frame']}:{p['concept']['class']}" for p in preds if p['concept'].get('open')))
        if srcs:
            out['note_sources'] = srcs
        if types:
            out['note_types'] = types
        if days:
            out['note_dates'] = days
        # every date the wording gives (the dated note a later question is anchored on, an event date): the window may start the day after it
        given = CN.note_dates(question or it.get('question') or '')
        if given:
            out['anchor_dates'] = given
        if any(p.get('_persist') for p in preds):
            out['asks_persistence'] = True
        if any(p.get('_count') for p in preds):
            out['asks_count'] = True
        if opens:
            out['open_concepts'] = opens
        if any(p.get('_response') for p in preds):
            out['asks_response'] = True
    return {k: v for k, v in out.items() if v not in (None, {}, [])}


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
        it = build_intent(stack, question, as_of, db, quotes=[sub['quote'] if sub['how'] in ('patient_id', 'member_id') else None], prior=prior,
                          decide=decide, allow_live=allow_live, spend=spend)
        prior_note = ((prior or {}).get('frame') or {}).get('note')
        follows = CN.back_references(question, prior_note)
        note = CN.is_note_question(question, it) or follows
        choose = lambda text, options, key: _so_choice(text, options, as_of=as_of, task_key=key, decide=decide, allow_live=allow_live, spend=spend)  # noqa: E731
        preds = CN.build(question, as_of, lexicon=lexicon_of(stack, db), prior_predicates=prior_note if follows else None, choose=choose) if note else None
        if note and not preds and follows and not CN.is_note_question(question, it):
            note = False  # a back-reference with no note frame of its own: the structured reading stands
    finally:
        db.close()
    if note and not preds:
        return None
    if not note and it.get('gaps'):
        return None
    # remembered here, in the request's own thread, so the next turn's "she" / "and the one before that" binds this patient
    PI.remember_current(PI.make_subject(sub['patient_id'], sub['quote'], frame=_clean_intent(it) if not note else None))
    params = {'patient_id': sub['patient_id'], 'question': question[:2000], 'subject_quote': sub['quote'] or sub['patient_id'], 'how': sub['how'],
              'chart': typed_chart(it, preds if note else None, question)}
    from .engine_suite import plan
    graph, info = plan(OP, params, f"Read one patient's chart: {sub['patient_id']}", question,
                       [f"patient {sub['patient_id']} ({sub['how']})", *( [f"intent {it.get('intent')} over {it.get('domain')}"] if not note else ['note frames'])])
    steps = [{'name': 'chart_reader.parse', 'receipt': {'cost_estimate': {'estimated_usd': spend.usd}, 'calls': spend.calls}}] if spend.calls else []
    components = ['chart_reader', 'patient_ids', 'decision_context'] + (['note_frames', 'system_one'] if note else []) + (['system_one'] if spend.calls else [])
    return graph, {'route': 'chart_reader', 'model': None, 'frontier_called': False, 'new_paid_api_calls': spend.calls, 'steps': steps,
                   'cost_class': 'free (grammar + SQL); System One ~$0.0002 per open slot or note passage', 'components': list(dict.fromkeys(components)),
                   'chart_reader': VERSION, 'intent': _clean_intent(it), 'subject': {'patient_id': sub['patient_id'], 'how': sub['how']},
                   'elapsed_seconds': round(time.perf_counter() - started, 3)}
