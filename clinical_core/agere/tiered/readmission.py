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
    end = (_day(readmit_discharge) or _day(readmit_admit) or date.max) + timedelta(days=7)
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
