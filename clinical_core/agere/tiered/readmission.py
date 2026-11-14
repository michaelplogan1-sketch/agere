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
FOLLOW_UP = re.compile(r'\bfollow[- ]?up\b', re.I)
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
        return 'readmission' if stated.group(1) == readmit_admit else 'stay before the readmission' if stated.group(1) == index_admit else 'another stay'
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


def patterns(question, sql, params, read, db, *, readers=(), s1_ledger=None):
    """Reading mode 'readmission_patterns': every readmission the SQL selects, read by code only. For each one, the reason the
    record states for the readmission and for the stay before it, whether the prior discharge summary gave a follow-up
    instruction, and whether an outpatient visit followed within 7 days. Code counts. No model reads anything.
    -> (columns, rows, validation, steps, evidence)."""
    used = set(re.findall(r'(?<![:\w]):([a-z_][a-z0-9_]*)', sql))
    columns, selected = store.run(sql, {k: v for k, v in (params or {}).items() if k in used}, db)
    need = ('patient_id', 'readmit_admit', 'index_admit', 'index_discharge', 'readmit_discharge', 'visit7', 'record_id', 'event_date', 'document_type')
    if any(c not in columns for c in need): raise store.RecipeError('A readmission-patterns plan must select the two stays, the follow-up flag and the documents')
    at = {c: columns.index(c) for c in need}
    started = time.monotonic()
    cases = {}
    for r in selected:
        case = cases.setdefault((r[at['patient_id']], r[at['readmit_admit']]), {'stays': tuple(str(r[at[c]] or '') for c in ('index_admit', 'index_discharge', 'readmit_admit', 'readmit_discharge')),
                                                                              'visit7': bool(r[at['visit7']]), 'docs': []})
        if r[at['record_id']]: case['docs'].append((r[at['record_id']], r[at['event_date']], r[at['document_type']]))
    ids = list(dict.fromkeys(d[0] for c in cases.values() for d in c['docs']))
    texts = store.texts(ids, db)
    from collections import Counter
    count, reasons, evidence, pairs = Counter(), Counter(), [], []
    for (pid, when), case in sorted(cases.items()):
        found = {'readmission': None, 'stay before the readmission': None}  # the firmest statement on record for each stay
        told = False
        for rid, day, doc_type in case['docs']:
            text = ' '.join((texts.get(rid) or {}).get('text', '').split())
            if not text: continue
            stay = which_stay(text, day, *case['stays'])
            if stay in found and not NOT_ADMITTED.search(text):
                got = stated_reason(text)
                if got and (found[stay] is None or got[0] < found[stay][0]): found[stay] = (got[0], got[1], rid)
            if stay == 'stay before the readmission' and 'discharge' in str(doc_type).lower() and FOLLOW_UP.search(text): told = True
        count['readmissions'] += 1
        a, b = found['readmission'], found['stay before the readmission']
        if a:
            count['a reason for the readmission is stated in the record'] += 1; reasons[_label(a[1])] += 1
            evidence.append({'record_id': a[2], 'label': 'yes', 'by': 'code', 'value': f'{pid} readmitted {when}', 'quote': a[1]})
        else: count['no document states why the patient was readmitted'] += 1
        pairs.append((a, b))
        if told: count['the prior discharge summary gives a follow-up instruction'] += 1
        if told and not case['visit7']: count['a follow-up instruction was given and no outpatient visit followed within 7 days'] += 1
        if not told and not case['visit7']: count['no follow-up instruction on record and no outpatient visit within 7 days'] += 1
    n = count['readmissions']
    order = ('readmissions', 'a reason for the readmission is stated in the record', 'no document states why the patient was readmitted',
             'the prior discharge summary gives a follow-up instruction', 'a follow-up instruction was given and no outpatient visit followed within 7 days',
             'no follow-up instruction on record and no outpatient visit within 7 days')
    rows = [[k, count[k], round(count[k] / n, 4) if n else None] for k in order]
    # Group the stated reasons: code quoted them, System One places each distinct wording in one fixed group, code counts.
    distinct = sorted({r[1] for pair in pairs for r in pair if r})
    with ThreadPoolExecutor(8) as pool:
        placed = dict(zip(distinct, pool.map(lambda r: group_of(r, s1_ledger), distinct))) if distinct else {}
    grouped, compare = Counter(), Counter()
    for a, b in pairs:
        ga = placed.get(a[1], (None, 0))[0] if a else None
        gb = placed.get(b[1], (None, 0))[0] if b else None
        if a: grouped[ga or 'not grouped'] += 1
        if a and b:
            compare['same problem group as the stay before it' if ga and gb and ga == gb and ga != 'other' else
                    'a different problem group from the stay before it' if ga and gb and ga != gb else 'the two reasons could not be compared (one is unclear or not grouped)'] += 1
    rows += [[k, compare[k], round(compare[k] / n, 4) if n else None] for k in ('same problem group as the stay before it', 'a different problem group from the stay before it',
                                                                                'the two reasons could not be compared (one is unclear or not grouped)')]
    rows += [[f"readmitted for: {GROUP_NAMES.get(g, g)}", k, round(k / n, 4)] for g, k in grouped.most_common()]
    checks = [{'name': 'readmissions_selected', 'ok': n > 0, 'detail': f'{n} readmissions, {len(ids)} documents'},
              {'name': 'counts_add_up', 'ok': count['a reason for the readmission is stated in the record'] + count['no document states why the patient was readmitted'] == n
               and sum(grouped.values()) == count['a reason for the readmission is stated in the record'], 'detail': 'stated + not stated = readmissions; groups add up to stated'}]
    steps = [{'tier': 'read', 'reader': 'code', 'calls': len(ids), 'decided': len(ids), 'tokens': 0, 'usd': 0.0, 'outcome': 'read', 'model': 'none (labelled fields and fixed wording)',
              'seconds': round(time.monotonic() - started, 2)}]
    if distinct: steps.append({'tier': 'read', 'reader': 'system1', 'calls': len(distinct), 'decided': sum(1 for g, _ in placed.values() if g), 'tokens': 0,
                               'usd': sum(u for _, u in placed.values()), 'outcome': 'grouped', 'model': 'system-one'})
    return ['finding', 'readmissions', 'share'], rows, {'ok': all(c['ok'] for c in checks), 'checks': checks}, steps, evidence[:200]
