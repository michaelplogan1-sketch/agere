"""A readmission chart review: what the record states about each admission, assigned to the right stay by code, with the
discharge instructions, imaging impressions and any advance-directive wording.

  SQL         the latest stay followed by a 30-day readmission, the readmission itself, and the documents from the
              first admission to a week after the readmission discharge
  code        finds every admission statement in those documents: a labelled field (principal diagnosis, reason for
              admission, chief complaint) or a sentence saying what the patient presented or was admitted with
  code        assigns each statement to a stay: by the admission date the document itself states, else by its date
  System One  judges the unlabelled sentences: does this say why the patient was admitted? A labelled field needs no model
  code        reports each statement as a quote, with its stay. Nothing is paraphrased and no cause is inferred.

The answer says what the record states for the readmission and for the stay before it. It does not judge whether the
readmission was avoidable or what caused it beyond what a document says.
"""
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from . import store

MAX_PASSAGES = 200
FIELD = re.compile(r'\b(Principal diagnosis|Principal problem(?: this admission)?|Admitting diagnosis|Admission diagnosis|Reason for admission|Reason for readmission|Chief complaint)\s*:\s*'
                   r'(.{2,160}?)(?=\s+(?:[A-Z][A-Za-z-]+(?: [a-z-]+){0,2}:)|\.\s|\.$|$)')
LOOSE = re.compile(r'\b(?:presented (?:to [^.]{0,40}?)?with|presenting with|admitted (?:with|for|due to|after|because)|readmitted (?:with|for|due to|after|because)|'
                   r'returned (?:to [^.]{0,30}?)?with|brought in (?:with|for|after))\b', re.I)
NOT_ADMITTED = re.compile(r'\btreated and released\b|\bdischarged (?:home )?from the (?:ed|emergency department)\b|\bnot admitted\b', re.I)  # a visit that did not become a stay
ADMITTED_ON = re.compile(r'\bAdmission date\s*:\s*(\d{4}-\d{2}-\d{2})', re.I)
FOLLOW_UP = re.compile(r'\bfol{1,3}ow[- ]?up\b', re.I)        # notes misspell it ("folow up"); a dropped or doubled l is still the instruction
DISPOSITION = re.compile(r'\bDisposition\s*:?\s+([^.]{2,80})', re.I)
IMPRESSION = re.compile(r'\bIMPRESSION\s*:\s*(.{3,400}?)(?=\s+(?:[A-Z][A-Z /-]{3,}:|Electronically|Signed|Dictated)|$)')
DIRECTIVE = re.compile(r'advance directive|\bpolst\b|\bmolst\b|\bdnr\b|\bdni\b|code status|full code|health ?care proxy|living will|goals of care|power of attorney|'
                       r'comfort (?:care|measures)|\bhospice\b|\bpalliative\b', re.I)
OPTIONS = {'yes': 'The passage says why the patient was admitted to hospital, or what problem they came in with.',
           'no': 'The passage does not say why the patient was admitted: it describes treatment, a result, a plan, or a visit that did not lead to an admission.'}
STAYS = ('readmission', 'stay before the readmission', 'between the two stays', 'another stay', 'whole record')
ADMISSION_KINDS = ('principal diagnosis', 'principal problem', 'principal problem this admission', 'admitting diagnosis', 'admission diagnosis', 'reason for admission',
                   'reason for readmission', 'chief complaint', 'statement')


def _day(text):
    try:
        return date.fromisoformat(str(text)[:10])
    except ValueError:
        return None


def which_stay(doc_text, event_date, index_admit, index_discharge, readmit_admit, readmit_discharge):
    """Code only. The stay a document is about: the admission date it states wins; otherwise its own date decides."""
    stated = ADMITTED_ON.search(doc_text)
    if stated:
        # A hospital episode can be several rows (a transfer, a second source's copy), so a summary may name an admission
        # date inside the episode rather than its first day.
        inside = lambda first, last: bool(first) and str(first)[:10] <= stated.group(1) <= str(last or first)[:10]
        return 'readmission' if inside(readmit_admit, readmit_discharge) else 'stay before the readmission' if inside(index_admit, index_discharge) else 'another stay'
    when, ra, ia = _day(event_date), _day(readmit_admit), _day(index_admit)
    if not when or not ra: return 'another stay'
    if ra <= when <= (_day(readmit_discharge) or ra) + timedelta(days=7): return 'readmission'
    if not ia: return 'another stay'
    if ia <= when <= (_day(index_discharge) or ia) + timedelta(days=3): return 'stay before the readmission'
    return 'between the two stays' if ia < when < ra else 'another stay'


def statements(texts, stays, kinds=None):
    """-> [{'stay','kind','text','record_id','event_date','by_code'}]: every statement the review reports, each a direct quote.
    Inside the window (the stay before the readmission through a week after the readmission discharge): why the patient was
    admitted, follow-up instructions and disposition in a discharge summary, imaging impressions. In any document of the
    patient: advance directive, code status or goals-of-care wording. kinds: {record_id: (source, document_type)}."""
    index_admit, index_discharge, readmit_admit, readmit_discharge = stays
    start = _day(index_admit) or ((_day(readmit_admit) or date.min) - timedelta(days=30))
    # A discharge summary can arrive weeks late (one in this warehouse is dated 18 days after discharge), so documents are read for
    # 30 days after the readmission; one that names its own admission date is assigned by that date, an undated one only within a week.
    end = (_day(readmit_discharge) or _day(readmit_admit) or date.max) + timedelta(days=30)
    out = []
    def add(stay, kind, quote, rid, doc, by_code=True):
        out.append({'stay': stay, 'kind': kind, 'text': quote, 'record_id': rid, 'event_date': str(doc.get('event_date') or ''), 'by_code': by_code})
    for rid, doc in texts.items():
        text = ' '.join(doc['text'].split())
        source, doc_type = (kinds or {}).get(rid, ('', ''))
        for sentence in re.finditer(r'[^.]{8,320}\.?', text):
            if DIRECTIVE.search(sentence.group(0)): add('whole record', 'advance directive or code status', sentence.group(0).strip(), rid, doc)
        when = _day(doc.get('event_date'))
        if not when or not start <= when <= end: continue
        stay = which_stay(text, doc.get('event_date'), *stays)
        taken = []
        for m in FIELD.finditer(text):
            add(stay, m.group(1).lower(), f'{m.group(1)}: {m.group(2).strip()}', rid, doc); taken.append((m.start(), m.end()))
        if 'discharge' in str(doc_type).lower():
            for sentence in re.finditer(r'[^.]{8,320}\.?', text):
                if FOLLOW_UP.search(sentence.group(0)): add(stay, 'follow-up instruction', sentence.group(0).strip(), rid, doc)
            for m in DISPOSITION.finditer(text): add(stay, 'disposition', m.group(0).strip(), rid, doc)
        if 'radiology' in str(doc_type).lower() or 'imaging' in str(doc_type).lower():
            for m in IMPRESSION.finditer(doc['text'].replace('\n', ' ')): add(stay, 'imaging impression', 'IMPRESSION: ' + ' '.join(m.group(1).split()), rid, doc)
        if NOT_ADMITTED.search(text): continue  # the document itself says the visit did not lead to an admission
        for sentence in re.finditer(r'[^.]{12,320}\.?', text):
            if not LOOSE.search(sentence.group(0)) or any(a < sentence.end() and sentence.start() < b for a, b in taken): continue
            add(stay, 'statement', sentence.group(0).strip(), rid, doc, by_code=False)
    return out


def _judge(passage, s1_ledger):
    if passage['by_code']: return {**passage, 'label': 'yes', 'by': 'code', 'usd': 0.0}
    from . import methods
    try:
        label, p, usd = methods._system_one('Passage from a patient record: ' + passage['text'] + '\nDoes it say why the patient was admitted to hospital?', OPTIONS, s1_ledger)
    except Exception:
        label, p, usd = None, 0.0, 0.0
    if label: return {**passage, 'label': label, 'by': 'system1', 'usd': usd, 'confidence': round(p, 4)}
    try:  # System One left it open: the small model decides, yes or no only
        from . import models
        text, step = models._claude(models.SLM, 'Answer with exactly one JSON object: {"admitted_reason": true} if the passage says why the patient was admitted to hospital or '
                                    'what problem they came in with, else {"admitted_reason": false}.', 'Passage: ' + passage['text'])
        said = models._json(text).get('admitted_reason')
        if isinstance(said, bool): return {**passage, 'label': 'yes' if said else 'no', 'by': 'slm', 'usd': usd + step['usd']}
    except Exception:
        pass
    return {**passage, 'label': None, 'by': None, 'usd': usd, 'confidence': round(p, 4)}


def execute(question, sql, params, read, db, *, readers=('system1',), s1_ledger=None):
    """Reading mode 'admission_reasons'. -> (columns, rows, validation, steps, evidence), in the shape read.execute returns."""
    used = set(re.findall(r'(?<![:\w]):([a-z_][a-z0-9_]*)', sql))
    columns, selected = store.run(sql, {k: v for k, v in (params or {}).items() if k in used}, db)
    need = ('record_id', 'index_admit', 'index_discharge', 'readmit_admit', 'readmit_discharge')
    if any(c not in columns for c in need): raise store.RecipeError('An admission-reasons plan must select record_id and the dates of both stays')
    out_columns = ['stay', 'stated_as', 'passage', 'record_id', 'event_date']
    if not selected:
        checks = [{'name': 'readmission_on_record', 'ok': True, 'detail': 'no 30-day readmission with documents on record for this patient'}]
        return out_columns, [['(none)', '(none)', 'No 30-day readmission with documents is on record for this patient', '', '']], {'ok': True, 'checks': checks}, \
            [{'tier': 'read', 'outcome': 'no_documents_selected', 'tokens': 0, 'usd': 0.0}], []
    stays = tuple(str(selected[0][columns.index(c)] or '') for c in need[1:])
    ids = list(dict.fromkeys(r[columns.index('record_id')] for r in selected))
    kinds = {r[columns.index('record_id')]: (r[columns.index('source')], r[columns.index('document_type')]) for r in selected} if 'document_type' in columns else {}
    texts = {i: t for i, t in store.texts(ids, db).items() if t['text'].strip()}
    found = statements(texts, stays, kinds)
    if len(found) > MAX_PASSAGES: raise store.RecipeError(f'{len(found)} admission statements; this tier judges at most {MAX_PASSAGES} per question')
    started = time.monotonic()
    with ThreadPoolExecutor(8) as pool:
        judged = list(pool.map(lambda p: _judge(p, s1_ledger), found)) if found else []
    stated = sorted((j for j in judged if j['label'] == 'yes'), key=lambda j: (STAYS.index(j['stay']), j['event_date'], j['record_id']))
    undecided = [j for j in judged if not j['label']]
    rows = [[j['stay'], j['kind'], j['text'], j['record_id'], j['event_date']] for j in stated]
    if not any(j['kind'] == 'advance directive or code status' for j in stated):
        rows.append(['whole record', 'advance directive or code status',
                     f'No advance directive, code status or goals-of-care wording is in any of the {len(texts)} documents on record for this patient', '', ''])
    if not rows: rows = [['(none)', '(none)', f'{len(judged)} passages were read; none states why the patient was admitted', '', '']]
    corpus = {i: ' '.join(t['text'].split()) for i, t in texts.items()}
    checks = [{'name': 'documents_selected', 'ok': bool(ids), 'detail': f'{len(ids)} documents'},
              {'name': 'passages_are_document_words', 'ok': all(j['text'].split(': ', 1)[-1] in corpus[j['record_id']] or j['kind'] == 'disposition' and j['text'] in corpus[j['record_id']]
                                                                for j in judged), 'detail': f'{len(judged)} passages'},
              {'name': 'every_passage_decided', 'ok': not undecided, 'detail': f'{len(undecided)} undecided'}]
    coded, asked = sum(1 for j in judged if j['by'] == 'code'), [j for j in judged if not j['by_code']]
    steps = []
    if coded: steps.append({'tier': 'read', 'reader': 'code', 'calls': coded, 'decided': coded, 'tokens': 0, 'usd': 0.0, 'outcome': 'read', 'model': 'none (labelled field)'})
    if asked: steps.append({'tier': 'read', 'reader': 'system1 then small model', 'calls': len(asked), 'decided': sum(bool(j['label']) for j in asked), 'tokens': 0,
                            'usd': sum(j['usd'] for j in asked), 'outcome': 'read', 'model': 'system-one'})
    steps.append({'tier': 'read', 'outcome': 'statements_found_by_code', 'passages': len(judged), 'documents': len(ids), 'seconds': round(time.monotonic() - started, 2), 'tokens': 0, 'usd': 0.0})
    evidence = [{'record_id': j['record_id'], 'label': j['label'], 'by': j['by'], 'value': f"{j['stay']}: {j['kind']}" if j['label'] == 'yes' else None,
                 'quote': j['text'] if j['label'] == 'yes' else None} for j in judged]
    return out_columns, rows, {'ok': all(c['ok'] for c in checks), 'checks': checks}, steps, evidence


# ---- every readmission in a year: what the charts state, counted by code ----

REASON_FIELD = re.compile(r'\b(?:Principal diagnosis|Principal problem(?: this admission)?|Admitting diagnosis|Admission diagnosis|Reason for (?:re)?admission)\s*:\s*(.{2,120}?)'
                          r'(?=\s+(?:[A-Z][A-Za-z-]+(?: [a-z-]+){0,2}:)|\.\s|\.$|$)')
REASON_ADMITTED = re.compile(r'\b(?:Admitted|Readmitted) (?:with|due to|because of) ([^;.]{3,120})')
REASON_PRESENTED = re.compile(r'\bpresented (?:to [^.]{0,40}?)?with ([^;.]{3,120})', re.I)
ICD = re.compile(r'^[A-Z]\d{2}(?:\.\w{1,4})?$', re.I)
# The three-character ICD-10 categories that appear as bare codes in discharge summaries, named so a reader need not look them up.
ICD_NAMES = {'I50': 'heart failure', 'J18': 'pneumonia', 'E87': 'fluid, electrolyte or acid-base disorder', 'A41': 'sepsis', 'I10': 'hypertension',
             'I25': 'chronic ischaemic heart disease', 'J44': 'COPD', 'N17': 'acute kidney failure', 'E11': 'type 2 diabetes', 'I21': 'acute myocardial infarction',
             'I48': 'atrial fibrillation', 'N39': 'urinary tract disorder', 'K92': 'gastrointestinal bleeding or other digestive disorder', 'J96': 'respiratory failure'}


def stated_reason(text):
    """Code only. The reason for admission a document states, in its own words, and how firmly it states it:
    -> (rank, words) with rank 0 a labelled field, 1 "Admitted with ...", 2 "presented with ..."; or None."""
    text = ' '.join(text.split())
    for rank, pattern in enumerate((REASON_FIELD, REASON_ADMITTED, REASON_PRESENTED)):
        m = pattern.search(text)
        if m: return rank, m.group(1).strip().rstrip('.')
    return None


GROUPS = {'heart_failure': 'Heart failure, fluid overload or congestion',
          'respiratory_infection': 'Pneumonia or another lung or airway infection',
          'copd_asthma': 'COPD or asthma flare',
          'sepsis_infection': 'Sepsis, or an infection outside the lungs (skin, urinary, bloodstream)',
          'kidney': 'Acute kidney injury or kidney failure',
          'diabetes': 'Diabetes, high or low blood glucose',
          'electrolyte': 'A fluid, sodium, potassium or other electrolyte problem that is not heart failure',
          'coronary': 'Chest pain, heart attack or coronary artery disease',
          'arrhythmia': 'An abnormal heart rhythm',
          'neurological': 'Stroke, seizure or another neurological problem',
          'injury': 'A fall, fracture or other injury',
          'gastrointestinal': 'A stomach, bowel, liver or bleeding problem',
          'mental_health_substance': 'A mental health problem, overdose or substance use',
          'blood_pressure': 'High or low blood pressure',
          'other': 'Something else, or too vague to place (for example "acute illness")'}
GROUP_NAMES = {'heart_failure': 'heart failure or fluid overload', 'respiratory_infection': 'pneumonia or other respiratory infection', 'copd_asthma': 'COPD or asthma',
               'sepsis_infection': 'sepsis or other infection', 'kidney': 'kidney injury', 'diabetes': 'diabetes or glucose', 'electrolyte': 'fluid or electrolyte problem',
               'coronary': 'chest pain or coronary disease', 'arrhythmia': 'arrhythmia', 'neurological': 'neurological', 'injury': 'fall, fracture or injury',
               'gastrointestinal': 'gastrointestinal', 'mental_health_substance': 'mental health, overdose or substance use', 'blood_pressure': 'blood pressure', 'other': 'other or unclear'}


def group_of(reason, s1_ledger=None):
    """System One places one stated reason in one problem group, from fixed choices. -> (group or None, usd). A bare diagnosis
    code is given with its category name so the choice is about words, not about decoding a code."""
    from . import methods
    words = _label(reason)
    try:
        label, _, usd = methods._system_one('A hospital record states this reason for an admission: "' + words + '". Which one group does it belong to?', GROUPS, s1_ledger)
    except Exception:
        label, usd = None, 0.0
    return label, usd


def _same_problem(a, b):
    """Code only. -> True, False, or None when one is a bare code and the other is words (they cannot be compared by code)."""
    code_a, code_b = bool(ICD.match(a)), bool(ICD.match(b))
    if code_a and code_b: return a[:3].upper() == b[:3].upper()
    if code_a != code_b: return None
    return a.lower() == b.lower()


def _label(reason):
    return f"{reason.upper()} ({ICD_NAMES[reason[:3].upper()]})" if ICD.match(reason) and reason[:3].upper() in ICD_NAMES else reason.upper() if ICD.match(reason) else reason.lower()


FOLLOW_UP_FIELD = re.compile(r'\bFol{1,3}ow[- ]?up\s*:\s*[^.]{3,200}\.?', re.I)


def follow_up_instruction(text):
    """Code only. The follow-up instruction a discharge summary gives, quoted: a labelled Follow-up field when there is one,
    otherwise the first whole sentence that says follow up. A sentence is never cut in the middle of a word."""
    text = ' '.join(str(text or '').split())
    m = FOLLOW_UP_FIELD.search(text)
    if m: return m.group(0).strip()
    return next((x.strip() for x in re.split(r'(?<=[.!?])\s+', text) if 8 <= len(x) <= 400 and FOLLOW_UP.search(x)), None)


def _read_cases(sql, params, db):
    """Code only. Every readmission the SQL selects, with what its documents state. -> ([case], document ids, seconds).
    case: patient, readmission date, the two stays, whether an outpatient visit followed within 7 days, the firmest stated
    reason for the readmission and for the stay before it as (rank, words, record id) or None, and whether the prior
    discharge summary gave a follow-up instruction."""
    used = set(re.findall(r'(?<![:\w]):([a-z_][a-z0-9_]*)', sql))
    columns, selected = store.run(sql, {k: v for k, v in (params or {}).items() if k in used}, db)
    need = ('patient_id', 'readmit_admit', 'index_admit', 'index_discharge', 'readmit_discharge', 'visit7', 'record_id', 'event_date', 'document_type')
    if any(c not in columns for c in need): raise store.RecipeError('A readmission plan must select the two stays, the follow-up flag and the documents')
    at = {c: columns.index(c) for c in need}
    started = time.monotonic()
    found = {}
    for r in selected:
        case = found.setdefault((r[at['patient_id']], r[at['readmit_admit']]), {'stays': tuple(str(r[at[c]] or '') for c in ('index_admit', 'index_discharge', 'readmit_admit', 'readmit_discharge')),
                                                                              'visit7': bool(r[at['visit7']]), 'docs': []})
        if r[at['record_id']]: case['docs'].append((r[at['record_id']], r[at['event_date']], r[at['document_type']]))
    ids = list(dict.fromkeys(d[0] for c in found.values() for d in c['docs']))
    texts = store.texts(ids, db)
    out = []
    for (pid, when), case in sorted(found.items()):
        firmest = {'readmission': None, 'stay before the readmission': None}
        told = None
        for rid, day, doc_type in case['docs']:
            text = ' '.join((texts.get(rid) or {}).get('text', '').split())
            if not text: continue
            stay = which_stay(text, day, *case['stays'])
            if stay in firmest and not NOT_ADMITTED.search(text):
                got = stated_reason(text)
                # "Presented with ..." describes an arrival. In a document dated after that stay ended it is a later arrival, not this stay's reason.
                ended = case['stays'][3] if stay == 'readmission' else case['stays'][1]
                if got and got[0] == 2 and not ADMITTED_ON.search(text) and ended and str(day)[:10] > ended[:10]: got = None
                if got and (firmest[stay] is None or got[0] < firmest[stay][0]): firmest[stay] = (got[0], got[1], rid)
            if stay == 'stay before the readmission' and 'discharge' in str(doc_type).lower() and told is None:
                said = follow_up_instruction(text)
                if said: told = (said, rid)
        out.append({'patient': pid, 'when': when, 'stays': case['stays'], 'visit7': case['visit7'], 'reason': firmest['readmission'],
                    'prior': firmest['stay before the readmission'], 'told': told})
    return out, ids, round(time.monotonic() - started, 2)


def _grouped(cases, s1_ledger):
    """System One places each distinct stated reason in one fixed problem group. -> {words: (group or None, usd)}."""
    distinct = sorted({r[1] for c in cases for r in (c['reason'], c['prior']) if r})
    with ThreadPoolExecutor(8) as pool:
        return dict(zip(distinct, pool.map(lambda r: group_of(r, s1_ledger), distinct))) if distinct else {}


def _compared(case, placed):
    a, b = case['reason'], case['prior']
    if not a or not b: return None
    ga, gb = placed.get(a[1], (None, 0))[0], placed.get(b[1], (None, 0))[0]
    return 'same' if ga and gb and ga == gb and ga != 'other' else 'different' if ga and gb and ga != gb else 'not comparable'


ONLY = {'instruction_no_visit': lambda c, cmp: bool(c['told']) and not c['visit7'], 'no_stated_reason': lambda c, cmp: not c['reason'],
        'same_problem': lambda c, cmp: cmp == 'same', 'no_instruction_no_visit': lambda c, cmp: not c['told'] and not c['visit7']}


def case_list(question, sql, params, read, db, *, readers=(), s1_ledger=None):
    """Reading mode 'readmission_cases': one row per readmission, the worklist behind each line of the committee view.
    read['only'] keeps the readmissions one line counts. Code reads and quotes; System One only names the problem group."""
    cases, ids, seconds = _read_cases(sql, params, db)
    placed = _grouped(cases, s1_ledger)
    keep = ONLY.get((read or {}).get('only'))
    rows, evidence = [], []
    for c in cases:
        cmp = _compared(c, placed)
        if keep and not keep(c, cmp): continue
        group = placed.get(c['reason'][1], (None, 0))[0] if c['reason'] else None
        days = (_day(c['when']) - _day(c['stays'][1])).days if _day(c['when']) and _day(c['stays'][1]) else None
        rows.append([c['patient'], c['stays'][1], c['when'], days, c['reason'][1] if c['reason'] else 'not stated in any document',
                     GROUP_NAMES.get(group, 'not grouped') if c['reason'] else '', {'same': 'same problem group', 'different': 'different problem group',
                                                                                    'not comparable': 'not comparable', None: 'no stated reason to compare'}[cmp],
                     c['told'][0] if c['told'] else 'none on record', 'yes' if c['visit7'] else 'no'])
        if c['reason']: evidence.append({'record_id': c['reason'][2], 'label': 'yes', 'by': 'code', 'value': f"{c['patient']} readmitted {c['when']}", 'quote': c['reason'][1]})
    checks = [{'name': 'readmissions_selected', 'ok': bool(cases), 'detail': f'{len(cases)} readmissions read, {len(rows)} listed, {len(ids)} documents'},
              {'name': 'one_row_per_readmission', 'ok': len({(r[0], r[2]) for r in rows}) == len(rows), 'detail': f'{len(rows)} rows'}]
    steps = [{'tier': 'read', 'reader': 'code', 'calls': len(ids), 'decided': len(ids), 'tokens': 0, 'usd': 0.0, 'outcome': 'read', 'model': 'none (labelled fields and fixed wording)', 'seconds': seconds}]
    if placed: steps.append({'tier': 'read', 'reader': 'system1', 'calls': len(placed), 'decided': sum(1 for g, _ in placed.values() if g), 'tokens': 0,
                             'usd': sum(u for _, u in placed.values()), 'outcome': 'grouped', 'model': 'system-one'})
    columns = ['patient_id', 'prior_discharge_date', 'readmission_date', 'days_between', 'stated_reason', 'problem_group', 'compared_with_prior_stay',
               'follow_up_instruction', 'outpatient_visit_within_7_days']
    if not rows: rows = [['(none)', '', '', None, f'none of the {len(cases)} readmissions read meets this description', '', '', '', '']]
    return columns, rows, {'ok': all(c['ok'] for c in checks), 'checks': checks}, steps, evidence[:200]


def patterns(question, sql, params, read, db, *, readers=(), s1_ledger=None):
    """Reading mode 'readmission_patterns': every readmission the SQL selects, read by code only. For each one, the reason the
    record states for the readmission and for the stay before it, whether the prior discharge summary gave a follow-up
    instruction, and whether an outpatient visit followed within 7 days. Code counts; System One only names the problem group.
    -> (columns, rows, validation, steps, evidence)."""
    from collections import Counter
    cases, ids, seconds = _read_cases(sql, params, db)
    placed = _grouped(cases, s1_ledger)
    count, grouped, compare, evidence = Counter(), Counter(), Counter(), []
    for c in cases:
        count['readmissions'] += 1
        if c['reason']:
            count['a reason for the readmission is stated in the record'] += 1
            grouped[placed.get(c['reason'][1], (None, 0))[0] or 'not grouped'] += 1
            evidence.append({'record_id': c['reason'][2], 'label': 'yes', 'by': 'code', 'value': f"{c['patient']} readmitted {c['when']}", 'quote': c['reason'][1]})
        else: count['no document states why the patient was readmitted'] += 1
        cmp = _compared(c, placed)
        if cmp: compare[{'same': 'same problem group as the stay before it', 'different': 'a different problem group from the stay before it',
                         'not comparable': 'the two reasons could not be compared (one is unclear or not grouped)'}[cmp]] += 1
        if c['told']: count['the prior discharge summary gives a follow-up instruction'] += 1
        if c['told'] and not c['visit7']: count['a follow-up instruction was given and no outpatient visit followed within 7 days'] += 1
        if not c['told'] and not c['visit7']: count['no follow-up instruction on record and no outpatient visit within 7 days'] += 1
    n = count['readmissions']
    order = ('readmissions', 'a reason for the readmission is stated in the record', 'no document states why the patient was readmitted',
             'the prior discharge summary gives a follow-up instruction', 'a follow-up instruction was given and no outpatient visit followed within 7 days',
             'no follow-up instruction on record and no outpatient visit within 7 days')
    rows = [[k, count[k], round(count[k] / n, 4) if n else None] for k in order]
    rows += [[k, compare[k], round(compare[k] / n, 4) if n else None] for k in ('same problem group as the stay before it', 'a different problem group from the stay before it',
                                                                                'the two reasons could not be compared (one is unclear or not grouped)')]
    rows += [[f"readmitted for: {GROUP_NAMES.get(g, g)}", k, round(k / n, 4)] for g, k in grouped.most_common()]
    distinct = list(placed)
    started = time.monotonic() - seconds
    checks = [{'name': 'readmissions_selected', 'ok': n > 0, 'detail': f'{n} readmissions, {len(ids)} documents'},
              {'name': 'counts_add_up', 'ok': count['a reason for the readmission is stated in the record'] + count['no document states why the patient was readmitted'] == n
               and sum(grouped.values()) == count['a reason for the readmission is stated in the record'], 'detail': 'stated + not stated = readmissions; groups add up to stated'}]
    steps = [{'tier': 'read', 'reader': 'code', 'calls': len(ids), 'decided': len(ids), 'tokens': 0, 'usd': 0.0, 'outcome': 'read', 'model': 'none (labelled fields and fixed wording)',
              'seconds': round(time.monotonic() - started, 2)}]
    if distinct: steps.append({'tier': 'read', 'reader': 'system1', 'calls': len(distinct), 'decided': sum(1 for g, _ in placed.values() if g), 'tokens': 0,
                               'usd': sum(u for _, u in placed.values()), 'outcome': 'grouped', 'model': 'system-one'})
    return ['finding', 'readmissions', 'share'], rows, {'ok': all(c['ok'] for c in checks), 'checks': checks}, steps, evidence[:200]


# ---- what the notes say got in the way between discharge and readmission ----

BARRIER_WORDS = re.compile(r"\b(?:because|unable to|could not|couldn't|cannot|can't|did not|didn't|not (?:taken|taking|filled|picked up|increased|started|restarted|scheduled|reached)|"
                           r"missed|no[- ]show|declin\w+|refus\w+|afford\w*|cost|transport\w*|ran out|running out|out of (?:medication|medicine|pills|tablets)|"
                           r"need(?:s|ed)? confirmation|not confirmed|unverified|could not be (?:clarified|confirmed|verified)|unclear)\b", re.I)
BARRIERS = {'visit_missed': 'A visit, appointment or follow-up was missed, not scheduled or could not be arranged',
            'regimen_unclear': 'The medication list, directions or what the patient actually takes could not be confirmed, so a change was not made',
            'cost_or_supply': 'Cost, insurance coverage, or running out of a medication or supply',
            'transport_access': 'Transport, distance, language or another access problem',
            'patient_declined': 'The patient declined, refused or chose not to do something that was advised',
            'side_effect': 'A side effect or intolerance stopped or limited a treatment',
            'clinical_limit': 'A lab value, vital sign or clinical state limited a treatment',
            'not_a_barrier': 'The passage does not describe anything getting in the way of care'}
BARRIER_NAMES = {'visit_missed': 'a visit missed or not arranged', 'regimen_unclear': 'medication list or directions could not be confirmed', 'cost_or_supply': 'cost or supply',
                 'transport_access': 'transport or access', 'patient_declined': 'patient declined', 'side_effect': 'side effect or intolerance', 'clinical_limit': 'a lab or vital sign limit'}
MAX_SENTENCES = 12


def barrier_sentences(text):
    """Code only. The sentences of a note that use wording for something getting in the way. A full stop inside a dose (0.35 MG) does not end a sentence."""
    return [x for x in re.split(r'(?<=[.!?])\s+', ' '.join(str(text or '').split())) if 15 <= len(x) <= 400 and BARRIER_WORDS.search(x)]


def barrier_kind(sentence, s1_ledger=None):
    """System One places one sentence in one fixed kind of barrier, or says it is not one. -> (kind or None, usd)."""
    from . import methods
    try:
        label, _, usd = methods._system_one('Sentence from a note written between a hospital discharge and a readmission: "' + sentence + '"\nWhat kind of barrier to care does it describe?', BARRIERS, s1_ledger)
    except Exception:
        label, usd = None, 0.0
    return label, usd


def barriers(question, sql, params, read, db, *, readers=(), s1_ledger=None):
    """Reading mode 'readmission_barriers': for every readmission the SQL selects, the sentences in notes dated after the prior
    discharge and up to the readmission that say something got in the way. Code finds the sentences; System One names the kind
    of barrier from fixed choices; code counts readmissions. -> (columns, rows, validation, steps, evidence)."""
    from collections import Counter
    used = set(re.findall(r'(?<![:\w]):([a-z_][a-z0-9_]*)', sql))
    columns, selected = store.run(sql, {k: v for k, v in (params or {}).items() if k in used}, db)
    need = ('patient_id', 'readmit_admit', 'index_discharge', 'record_id', 'event_date')
    if any(c not in columns for c in need): raise store.RecipeError('A readmission-barriers plan must select the two stays and the documents')
    at = {c: columns.index(c) for c in need}
    started = time.monotonic()
    cases = {}
    for r in selected:
        case = cases.setdefault((r[at['patient_id']], r[at['readmit_admit']]), [])
        if r[at['record_id']] and str(r[at['index_discharge']]) < str(r[at['event_date']]) <= str(r[at['readmit_admit']]): case.append(r[at['record_id']])
    ids = list(dict.fromkeys(i for docs in cases.values() for i in docs))
    texts = store.texts(ids, db)
    found = {}
    for key, docs in cases.items():
        seen = []
        for rid in docs:
            for sentence in barrier_sentences((texts.get(rid) or {}).get('text', '')):
                if len(seen) < MAX_SENTENCES and sentence not in [x[0] for x in seen]: seen.append((sentence, rid))
        found[key] = seen
    distinct = sorted({sentence for seen in found.values() for sentence, _ in seen})
    with ThreadPoolExecutor(8) as pool:
        placed = dict(zip(distinct, pool.map(lambda x: barrier_kind(x, s1_ledger), distinct))) if distinct else {}
    count, quotes, evidence = Counter(), {}, []
    for (pid, when), seen in sorted(found.items()):
        count['readmissions'] += 1
        kinds = set()
        for sentence, rid in seen:
            kind = placed.get(sentence, (None, 0))[0]
            if kind and kind != 'not_a_barrier':
                kinds.add(kind); quotes.setdefault(kind, sentence)
                evidence.append({'record_id': rid, 'label': 'yes', 'by': 'system1', 'value': f'{pid} readmitted {when}: {BARRIER_NAMES[kind]}', 'quote': sentence})
        if kinds: count['a note between the stays documents something that got in the way'] += 1
        elif not cases[(pid, when)]: count['no note on record between the stays'] += 1
        else: count['notes on record between the stays, none documents a barrier'] += 1
        for kind in kinds: count[kind] += 1
    n = count['readmissions']
    rows = [[k, count[k], round(count[k] / n, 4) if n else None, ''] for k in ('readmissions', 'a note between the stays documents something that got in the way',
                                                                               'notes on record between the stays, none documents a barrier', 'no note on record between the stays')]
    rows += [[f'documented barrier: {BARRIER_NAMES[k]}', count[k], round(count[k] / n, 4), quotes[k]] for k in sorted(BARRIER_NAMES, key=lambda k: -count[k]) if count[k]]
    undecided = sum(1 for kind, _ in placed.values() if not kind)
    checks = [{'name': 'readmissions_selected', 'ok': n > 0, 'detail': f'{n} readmissions, {len(ids)} notes between the stays, {len(distinct)} candidate sentences'},
              {'name': 'counts_add_up', 'ok': sum(count[k] for k in ('a note between the stays documents something that got in the way', 'notes on record between the stays, none documents a barrier',
                                                                     'no note on record between the stays')) == n, 'detail': 'the three outcomes add up to the readmissions'},
              {'name': 'every_sentence_decided', 'ok': undecided <= max(2, len(distinct) // 10), 'detail': f'{undecided} of {len(distinct)} sentences left undecided'}]
    steps = [{'tier': 'read', 'reader': 'code', 'calls': len(ids), 'decided': len(ids), 'tokens': 0, 'usd': 0.0, 'outcome': 'read', 'model': 'none (fixed wording)', 'seconds': round(time.monotonic() - started, 2)}]
    if distinct: steps.append({'tier': 'read', 'reader': 'system1', 'calls': len(distinct), 'decided': len(distinct) - undecided, 'tokens': 0, 'usd': sum(u for _, u in placed.values()),
                               'outcome': 'kind_of_barrier', 'model': 'system-one'})
    return ['finding', 'readmissions', 'share', 'example_from_a_note'], rows, {'ok': all(c['ok'] for c in checks), 'checks': checks}, steps, evidence[:200]


# ---- what else is on record that drives a return: illness burden, social need, the emergency department's threshold,
# ---- a facility sending the patient back, and whether the patient could manage at home. Markers on record, not causes.

SERIOUS = ('heart_failure', 'heart_failure_reduced_ef', 'chronic_kidney_disease', 'copd', 'cancer', 'dementia')
ED_LOW_YIELD = re.compile(r'workup unremarkable[^.]*\.\s*Admitted', re.I)
FACILITY = re.compile(r'\b(?:snf|skilled nursing|nursing (?:home|facility)|irf|inpatient rehab\w*|rehab\w* facility|long[- ]term care)\b', re.I)
HOME_HEALTH = re.compile(r'\bhome[ _]health\b', re.I)
DRIVERS = ('advanced illness', 'social need', 'emergency department threshold', 'sent back from a facility', 'self-care at home')
# Definitions reviewed and confirmed by Mike (clinician, product owner) on 2026-11-14: "They're right".
DRIVER_DEFINITIONS = {
    'advanced illness': 'heart failure, kidney disease, COPD, cancer or dementia on record; or five or more chronic conditions (a disease and its subtype counted once); or three or more admissions in the prior 12 months; or age 85 and over',
    'social need': 'a positive social-need screen; or no insurance; or low food access or an isolation, transport, housing or financial-stress score of 8 or more out of 10 from the outside social-data vendor',
    'emergency department threshold': 'the emergency note for this admission says the workup was unremarkable and the patient was admitted',
    'sent back from a facility': 'the stay before ended in discharge to a skilled nursing or rehabilitation facility (admission-discharge feed or the discharge summary)',
    'self-care at home': 'the stay before ended in discharge with home health; or dementia on record; or age 75 and over in a one-person household; or low estimated health literacy'}


def person_markers(db, pid, as_of, admit, why):
    """Code only. Fills why[...] with the illness, social and self-care markers on record for one patient by a date."""
    one = lambda q, *a: db.execute(q, a).fetchone()
    person = one('SELECT age_latest, coverage_type FROM patient WHERE patient_id = ?', pid) or (None, None)
    age = int(person[0]) if str(person[0] or '').isdigit() else None
    # A chronic condition is one the warehouse flags as chronic anywhere. Some rows carry the condition with the flag left blank
    # (kidney disease and COPD for one readmitted patient), so the condition decides, not the flag on that row.
    chronic = {r[0] for r in db.execute("SELECT DISTINCT concept FROM condition WHERE patient_id = ? AND concept IS NOT NULL AND (chronic = 1 OR chronic IS NULL) AND COALESCE(event_date, '') <= ? "
                                        "AND concept IN (SELECT DISTINCT concept FROM condition WHERE chronic = 1)", (pid, as_of))}
    from .recipes import _STAYS                                   # whole hospital stays, the same count the recipes give
    prior = one("WITH " + _STAYS.rstrip(', ') + " SELECT COUNT(*) FROM st WHERE patient_id = ? AND admit_date < ? AND admit_date >= date(?, '-365 day')", pid, admit, admit)[0]
    serious = sorted(chronic & set(SERIOUS))
    if serious: why['advanced illness'].append(', '.join(x.replace('_', ' ') for x in serious))
    distinct = len(chronic - ({'type_2_diabetes', 'type_1_diabetes'} if 'diabetes' in chronic else set()) - ({'heart_failure_reduced_ef'} if 'heart_failure' in chronic else set()))
    if distinct >= 5: why['advanced illness'].append(f'{distinct} chronic conditions')   # a disease and its subtype are one condition
    if prior >= 3: why['advanced illness'].append(f'{prior} admissions in the prior 12 months')
    if age is not None and age >= 85: why['advanced illness'].append(f'age {age}')
    needs = [r[0] for r in db.execute('SELECT DISTINCT need FROM social_need WHERE patient_id = ? AND positive = 1 AND event_date <= ?', (pid, as_of))]
    if needs: why['social need'].append('positive screen: ' + ', '.join(n.replace('_', ' ') for n in sorted(needs)))
    if str(person[1] or '').lower() == 'uninsured': why['social need'].append('no insurance')
    vendor = one('SELECT social_isolation_score, transportation_risk_score, housing_instability_score, financial_stress_score, food_access_low, est_household_size, health_literacy_estimate '
                 'FROM sdoh_vendor WHERE patient_id = ? ORDER BY delivery_date DESC LIMIT 1', pid)
    if vendor:
        for name, score in zip(('isolation', 'transport', 'housing', 'financial stress'), vendor[:4]):
            if score is not None and score >= 8: why['social need'].append(f'{name} score {int(score)} of 10')
        if vendor[4] in (1, '1'): why['social need'].append('low food access')
        if vendor[5] in (1, '1') and age is not None and age >= 75: why['self-care at home'].append(f'age {age}, one-person household')
        if str(vendor[6] or '').lower() == 'low': why['self-care at home'].append('low estimated health literacy')
    if 'dementia' in chronic: why['self-care at home'].append('dementia')


def destination(db, pid, discharge, summaries=()):
    """Code only. Where a stay discharged the patient to: the admission-discharge feed first, else the summary's Disposition line."""
    went = db.execute('SELECT discharge_disposition FROM adt_visit WHERE patient_id = ? AND discharge_date = ? AND discharge_disposition IS NOT NULL LIMIT 1', (pid, discharge)).fetchone()
    went = str(went[0]) if went else ''
    for text in summaries:
        if went: break
        m = DISPOSITION.search(' '.join(text.split()))
        if m: went = m.group(1)
    went = went.split(' Follow')[0].strip().lower().replace('_', ' ')
    return {'home today': 'home', 'snf': 'skilled nursing facility', 'irf': 'rehabilitation facility'}.get(went, went) or 'not recorded'


def _driver_cases(sql, params, db):
    """Code only. Every readmission the SQL selects with the five driver markers and what set each. -> ([case], ids, seconds)."""
    cases, ids, seconds = _read_cases(sql, params, db)
    used = set(re.findall(r'(?<![:\w]):([a-z_][a-z0-9_]*)', sql))
    columns, selected = store.run(sql, {k: v for k, v in (params or {}).items() if k in used}, db)
    at = {c: columns.index(c) for c in ('patient_id', 'readmit_admit', 'record_id', 'event_date', 'document_type')}
    docs = {}
    for r in selected:
        if r[at['record_id']]: docs.setdefault((r[at['patient_id']], r[at['readmit_admit']]), []).append((r[at['record_id']], r[at['event_date']], str(r[at['document_type']] or '')))
    texts = store.texts(ids, db)
    one = lambda q, *a: db.execute(q, a).fetchone()
    started = time.monotonic()
    for c in cases:
        pid, (ia, idis, ra, rdis) = c['patient'], c['stays']
        why = {d: [] for d in DRIVERS}
        person_markers(db, pid, ra, ia or ra, why)
        went = one('SELECT discharge_disposition FROM adt_visit WHERE patient_id = ? AND discharge_date = ? AND discharge_disposition IS NOT NULL LIMIT 1', pid, idis)
        went = str(went[0]) if went else ''
        later = one("SELECT MIN(admit_date) FROM stay WHERE patient_id = ? AND type = 'inpatient' AND admit_date > ?", pid, rdis or ra)[0]
        for rid, day, kind in docs.get((pid, c['when']), []):
            text = ' '.join((texts.get(rid) or {}).get('text', '').split())
            stay = which_stay(text, day, ia, idis, ra, rdis)
            # An emergency note describes an arrival, so it belongs to the latest admission before it was written: once the
            # patient has been admitted again, a later note is about that stay, not this one.
            during = bool(ADMITTED_ON.search(text)) or not later or str(day)[:10] < later
            if stay == 'readmission' and during and ED_LOW_YIELD.search(text) and not why['emergency department threshold']:
                why['emergency department threshold'].append(ED_LOW_YIELD.search(text).group(0))
            if stay == 'stay before the readmission' and 'discharge' in kind.lower() and not went:
                m = DISPOSITION.search(text)
                if m: went = m.group(1)
        went = went.split(' Follow')[0].strip().lower().replace('_', ' ')
        went = {'home today': 'home', 'snf': 'skilled nursing facility', 'irf': 'rehabilitation facility'}.get(went, went) or 'not recorded'
        if FACILITY.search(went): why['sent back from a facility'].append('discharged to a ' + went)
        elif HOME_HEALTH.search(went): why['self-care at home'].append('discharged with home health')
        c['why'], c['went'] = why, went
    return cases, ids, round(seconds + time.monotonic() - started, 2)


def drivers(question, sql, params, read, db, *, readers=(), s1_ledger=None):
    """Reading modes 'readmission_drivers' (the count for each driver) and 'readmission_driver_cases' (one row per readmission).
    Code only: queries and fixed wording. -> (columns, rows, validation, steps, evidence)."""
    from collections import Counter
    cases, ids, seconds = _driver_cases(sql, params, db)
    n, count = len(cases), Counter()
    for c in cases:
        hit = [d for d in DRIVERS if c['why'][d]]
        for d in hit: count[d] += 1
        count['none of the five on record' if not hit else 'two or more of the five' if len(hit) > 1 else 'exactly one of the five'] += 1
        if not c['visit7'] and not hit: count['no visit within 7 days and none of the five on record'] += 1
        count['prior discharge to: ' + c['went']] += 1
    checks = [{'name': 'readmissions_selected', 'ok': n > 0, 'detail': f'{n} readmissions, {len(ids)} documents'},
              {'name': 'counts_add_up', 'ok': count['none of the five on record'] + count['exactly one of the five'] + count['two or more of the five'] == n, 'detail': 'none, one and two-or-more add up to the readmissions'}]
    steps = [{'tier': 'read', 'reader': 'code', 'calls': len(ids), 'decided': len(ids), 'tokens': 0, 'usd': 0.0, 'outcome': 'read', 'model': 'none (queries and fixed wording)', 'seconds': seconds}]
    if (read or {}).get('mode') == 'readmission_driver_cases':
        rows = [[c['patient'], c['stays'][1], c['when'], c['went']] + ['; '.join(c['why'][d]) or 'no' for d in DRIVERS] for c in cases]
        return ['patient_id', 'prior_discharge_date', 'readmission_date', 'prior_discharge_to'] + [d.replace(' ', '_').replace('-', '_') for d in DRIVERS], rows, {'ok': all(x['ok'] for x in checks), 'checks': checks}, steps, []
    share = lambda k: round(count[k] / n, 4) if n else None
    rows = [['readmissions', n, 1.0, '']] + [[d, count[d], share(d), DRIVER_DEFINITIONS[d]] for d in DRIVERS]
    rows += [[k, count[k], share(k), ''] for k in ('none of the five on record', 'exactly one of the five', 'two or more of the five', 'no visit within 7 days and none of the five on record')]
    rows += [[k, v, share(k), ''] for k, v in sorted(count.items(), key=lambda kv: -kv[1]) if k.startswith('prior discharge to: ')]
    return ['finding', 'readmissions', 'share', 'how_it_is_marked'], rows, {'ok': all(x['ok'] for x in checks), 'checks': checks}, steps, []
