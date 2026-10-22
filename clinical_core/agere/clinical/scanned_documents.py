"""scanned_document_search: questions about what scanned, faxed and outside documents say, answered from OCR text with every
document cited by record, page, bbox (when the engine reported geometry) and character offsets (ocr_passages.py).

Parameters: terms (phrases, OCR-tolerant; match all or any per document), document_types, channels (fax / direct_message /
portal_upload), exam (the report's EXAM line, e.g. echocardiogram), measurement {name, op, value} read from the text
(lvef: 'LVEF 35-40%', 'ejection fraction of 30%'), period on the document's date of service, output documents | patients |
count_documents | count_patients. A measurement stated as a range is decided on the whole range: a range that straddles the
cut is listed as indeterminate and never counted as meeting it. Read-only; no model calls. Synthetic data only.
"""
import re

from . import ocr_passages as OP
from .foundation_engines import graph as one_node

VERSION = 'scanned-document-search/1'
OPERATION = 'scanned_document_search'
REVIEW = ('reviewed', 'unreviewed')
OUTPUTS = ('documents', 'patients', 'count_documents', 'count_patients')
CHANNELS = ('fax', 'direct_message', 'portal_upload')
DOCUMENT_TYPES = ('Radiology Report', 'Consult Note', 'Outside Records', 'Miscellaneous', 'Imaging - Outside', 'Procedure Report',
                  'Correspondence', 'Specialist Letter', 'Surgery/Procedure', 'Home Health Orders', 'Pathology')
OPS = {'<': lambda a, b: a < b, '<=': lambda a, b: a <= b, '>': lambda a, b: a > b, '>=': lambda a, b: a >= b}
MEASURES = {'lvef': {'label': 'left ventricular ejection fraction (%)',
                     'pattern': re.compile(r'\b(?:lvef|lv ejection fraction|left ventricular ejection fraction|ejection fraction|ef)\b'
                                           r'[^0-9%\n]{0,30}?(\d{1,2})(?:\s*(?:-|to|–)\s*(\d{1,2}))?\s*%', re.I),
                     'terms': ['ejection fraction', 'lvef']}}
NATIVE_SCALARS = ('count',)
PRODUCT = {'population': 'optional', 'view': 'table',
           'use': 'SCANNED / FAXED / OUTSIDE DOCUMENTS (OCR text): which patients or how many documents mention a finding, by document '
                  'type, channel (fax, direct message, portal) and exam (e.g. echocardiogram), or report a measurement (LVEF below/above '
                  'a cut), every document cited by record, page, bbox and character offsets.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'terms': ['nodule'], 'match': 'all|any', 'document_types': ['Radiology Report'], 'channels': ['fax'],
                           'exam': 'echocardiogram', 'review_status': '|'.join(('',) + REVIEW), 'measurement': {'name': 'lvef', 'op': '<', 'value': 40, 'unit': 'percent'},
                           'period': {'start': '', 'end': ''}, 'output': '|'.join(OUTPUTS)},
            'meaning': 'Search the OCR text of scanned, faxed and outside documents (front-office OCR, PDF text layers, ingest OCR '
                       'with Apple Vision boxes) with OCR-tolerant matching; returns documents or patients with page/bbox citations, '
                       'or their counts. A measurement range straddling the cut is indeterminate, never counted.'}


def parameter_schema(catalog, obj, string, array):
    return obj({'terms': array(string(None, 'phrase the document must contain (OCR-tolerant)')),
                'match': string(('all', 'any')), 'document_types': array(string(DOCUMENT_TYPES)), 'channels': array(string(CHANNELS)),
                'exam': string(None, 'word of the report EXAM line, e.g. echocardiogram; empty = any'),
                'review_status': string(('',) + REVIEW, 'clinician review state of the scanned document; empty = any'),
                'measurement': {'type': ['object', 'null'], 'properties': {'name': string(tuple(MEASURES)), 'op': string(tuple(OPS)),
                                'value': {'type': 'number'}, 'unit': string(('percent',))}},
                'period': obj({'start': {'type': 'string'}, 'end': {'type': 'string'}}), 'output': string(OUTPUTS)})


def validate(p):
    allowed = {'terms', 'match', 'document_types', 'channels', 'exam', 'review_status', 'measurement', 'period', 'output'}
    if not isinstance(p, dict) or set(p) - allowed: raise ValueError(f'{OPERATION} parameters: {sorted(allowed)}')
    terms = p.get('terms') or []
    if not isinstance(terms, list) or any(not isinstance(t, str) or len(OP.normalize(t).strip()) < 3 for t in terms):
        raise ValueError('terms: phrases of at least 3 characters')
    if p.get('match', 'all') not in ('all', 'any'): raise ValueError('match: all | any')
    types = p.get('document_types') or []
    if any(t not in DOCUMENT_TYPES for t in types): raise ValueError(f'document_types: {DOCUMENT_TYPES}')
    if any(c not in CHANNELS for c in p.get('channels') or []): raise ValueError(f'channels: {CHANNELS}')
    m = p.get('measurement') or None
    if m is not None:
        if not isinstance(m, dict) or set(m) - {'name', 'op', 'value', 'unit'} or m.get('name') not in MEASURES or m.get('op') not in OPS \
                or type(m.get('value')) not in (int, float) or m.get('unit', 'percent') != 'percent':
            raise ValueError(f'measurement: {{name: {tuple(MEASURES)}, op: {tuple(OPS)}, value: number, unit: percent}}')
        m = {**m, 'unit': 'percent'}
    period = p.get('period') or {}
    if not isinstance(period, dict) or set(period) - {'start', 'end'} or any(
            v and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(v)) for v in period.values()):
        raise ValueError('period: {start, end} as YYYY-MM-DD')
    if (p.get('review_status') or '') not in ('',) + REVIEW: raise ValueError(f'review_status: {REVIEW} or empty')
    if p.get('output', 'documents') not in OUTPUTS: raise ValueError(f'output: {OUTPUTS}')
    if not terms and not m and not p.get('exam') and not types and not p.get('channels') and not p.get('review_status'):
        raise ValueError('state what the documents must contain (terms, measurement, exam, type or channel)')
    return {'terms': terms, 'match': p.get('match', 'all'), 'document_types': types, 'channels': list(p.get('channels') or []),
            'exam': (p.get('exam') or '').strip(), 'review_status': p.get('review_status') or '', 'measurement': m, 'period': {'start': period.get('start', ''), 'end': period.get('end', '')},
            'output': p.get('output', 'documents')}


def readings(text, name):
    """[(low, high, matched text)] measurement statements in text."""
    out = []
    for mm in MEASURES[name]['pattern'].finditer(OP.normalize(text)):
        lo = int(mm.group(1)); hi = int(mm.group(2)) if mm.group(2) else lo
        out.append((min(lo, hi), max(lo, hi), mm.group(0)))
    return out


def decide(reading, m):
    lo, hi, _ = reading
    f = OPS[m['op']]
    if f(lo, m['value']) and f(hi, m['value']): return 'meets'
    if not f(lo, m['value']) and not f(hi, m['value']): return 'does_not_meet'
    return 'indeterminate'


def execute(path, patient_ids, params, as_of):
    p = validate(params)
    m = p['measurement']
    search_terms = list(p['terms']) + (MEASURES[m['name']]['terms'] if m else [])
    hits = OP.search(path, search_terms, patient_ids=patient_ids, document_types=p['document_types'], channels=p['channels'],
                     exam=p['exam'] or None, review_status=p.get('review_status') or None, period=p['period'], as_of=as_of)
    docs = {}
    for h in hits:
        d = docs.setdefault(h['record_id'], {'record_id': h['record_id'], 'patient_id': h['patient_id'], 'document_id': h['document_id'],
                                            'document_type': h['document_type'], 'received_via': h['received_via'], 'exam': h['exam'],
                                            'event_date': h['event_date'], 'review_status': h.get('review_status'), 'matched': set(), 'spans': [], 'readings': []})
        d['matched'].update(t for t in h['matched'] if t in p['terms'])
        if m:
            for r in readings(h['text'], m['name']):
                d['readings'].append({'low': r[0], 'high': r[1], 'text': r[2], 'decision': decide(r, m), 'citation': h['citation'],
                                      'page': h['page'], 'bbox': h['bbox']})
        if (set(h['matched']) & set(p['terms'])) or (m and readings(h['text'], m['name'])):
            d['spans'].append(h)
    kept, indeterminate = [], []
    for d in docs.values():
        if p['terms'] and ((p['match'] == 'all' and d['matched'] != set(p['terms'])) or not d['matched']): continue
        if m:
            states = {r['decision'] for r in d['readings']}
            if not states: continue
            if 'meets' in states: d['decision'] = 'meets'
            elif 'indeterminate' in states: d['decision'] = 'indeterminate'; indeterminate.append(d); continue
            else: continue
        kept.append(d)
    rows = [{'patient_id': d['patient_id'], 'record_id': d['record_id'], 'document_id': d['document_id'],
             'document_type': d['document_type'], 'received_via': d['received_via'], 'event_date': d['event_date'],
             'exam': d['exam'], 'review_status': d['review_status'], 'matched_terms': sorted(d['matched']),
             'reading': next((f"{r['text']} ({r['low']}-{r['high']})" for r in d['readings'] if r['decision'] == 'meets'), None),
             'page': (d['spans'] or [{}])[0].get('page'), 'bbox': (d['spans'] or [{}])[0].get('bbox'),
             'citations': [s['citation'] for s in d['spans'][:3]],
             'quote': (d['spans'] or [{'text': ''}])[0]['text'][:300]} for d in sorted(kept, key=lambda d: (d['patient_id'] or '', d['event_date'] or ''))]
    return {'schema': VERSION, 'parameters': p, 'as_of': str(as_of)[:10], 'documents': rows,
            'patient_ids': sorted({r['patient_id'] for r in rows if r['patient_id']}),
            'indeterminate': [{'patient_id': d['patient_id'], 'record_id': d['record_id'],
                               'readings': [f"{r['text']} -> {r['decision']} ({r['citation']})" for r in
                                            sorted(d['readings'], key=lambda r: r['decision'] != 'indeterminate')]} for d in indeterminate],
            'spans_searched': len(hits), 'store': OP.VERSION}


def _describe(p):
    bits = []
    if p['channels']: bits.append('received by ' + '/'.join(c.replace('_', ' ') for c in p['channels']))
    if p['document_types']: bits.append('type ' + '/'.join(p['document_types']))
    if p['exam']: bits.append(f"exam {p['exam']}")
    if p.get('review_status'): bits.append(p['review_status'])
    if p['terms']: bits.append(('mentioning ' if p['match'] == 'all' else 'mentioning any of ') + ', '.join(repr(t) for t in p['terms']))
    m = p['measurement']
    if m: bits.append(f"{MEASURES[m['name']]['label']} {m['op']} {m['value']}")
    if p['period']['start'] or p['period']['end']: bits.append(f"dated {p['period']['start'] or '...'} to {p['period']['end'] or '...'}")
    return '; '.join(bits)


def answer(report, plan, question):
    p, rows, ids = report['parameters'], report['documents'], report['patient_ids']
    partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
    method = (f"Scanned/faxed/outside document OCR text ({report['store']}): {_describe(p)}. Each document is cited by record, page, "
              'bbox when the OCR engine reported geometry, and character offsets.')
    lim = [method, 'OCR text is machine-read: misspellings are matched tolerantly, and a finding the OCR lost cannot be found.']
    if report['indeterminate']:
        lim.append(f"{len(report['indeterminate'])} document(s) state a range straddling the cut and are not counted: "
                   + '; '.join(f"{d['record_id']} ({d['readings'][0]})" for d in report['indeterminate'][:3]))
    status = 'partial' if partial else 'answered'
    if p['output'] in ('count_documents', 'count_patients'):
        n = len(rows) if p['output'] == 'count_documents' else len(ids)
        unit = 'documents' if p['output'] == 'count_documents' else 'patients'
        return {'status': status, 'answer_type': 'count', 'count': n, 'value': n, 'headline': f'{n} {unit}', 'table': rows,
                'patient_ids': ids, 'method': method, 'limitations': lim, 'citations': [c for r in rows[:20] for c in r['citations'][:1]]}
    if p['output'] == 'patients':
        by = {}
        for r in rows: by.setdefault(r['patient_id'], []).append(r)
        table = [{'patient_id': pid, 'documents': len(rs), 'first_document': rs[0]['record_id'], 'event_date': rs[0]['event_date'],
                  'reading': rs[0]['reading'], 'citation': rs[0]['citations'][0] if rs[0]['citations'] else None,
                  'quote': rs[0]['quote']} for pid, rs in by.items()]
        return {'status': status, 'answer_type': 'table', 'table': table, 'count': len(table), 'patient_ids': ids,
                'value': f'{len(table)} patients', 'headline': f'{len(table)} patients', 'method': method, 'limitations': lim, 'membership_role': 'selected'}
    return {'status': status, 'answer_type': 'table', 'table': rows, 'count': len(rows), 'patient_ids': ids,
            'value': f'{len(rows)} documents', 'headline': f'{len(rows)} documents', 'method': method, 'limitations': lim, 'membership_role': 'contributing'}


# -- exact routing ----------------------------------------------------------------------------------------------------
_DOC_WORDS = r'(?:documents?|reports?|records?|letters?|notes?|faxes|pdfs?)'
_CORPUS = re.compile(r'\b(scanned|faxed|fax(?:ed)?|outside|ocr(?:\'d|ed)?|portal[- ]uploaded|direct[- ]message)\b', re.I)
_TYPES = [(r'radiology reports?|imaging reports?', 'Radiology Report'), (r'consult(?:ation)? notes?|consults?', 'Consult Note'),
          (r'procedure reports?', 'Procedure Report'), (r'pathology reports?', 'Pathology'), (r'specialist letters?', 'Specialist Letter'),
          (r'outside records?', 'Outside Records'), (r'home health orders?', 'Home Health Orders')]
_EXAMS = [(r'echo(?:cardiogram)?s?(?: reports?)?', 'echocardiogram'), (r'ct(?: scans?)?(?: reports?)?', 'ct '),
          (r'mri(?: reports?)?', 'mri'), (r'mammograms?(?: reports?)?', 'mammogra'), (r'x-?rays?(?: reports?)?', 'xray')]
_MEASURE_Q = re.compile(r'\b(?:an? |the )?(?:lvef|ef|ejection fraction)\s+(below|under|less than|lower than|at or below|at most|no more than|'
                        r'above|over|greater than|more than|at least|at or above)\s+(\d{1,2})\b', re.I)
_OPS_Q = {'below': '<', 'under': '<', 'less than': '<', 'lower than': '<', 'at or below': '<=', 'at most': '<=', 'no more than': '<=',
          'above': '>', 'over': '>', 'greater than': '>', 'more than': '>', 'at least': '>=', 'at or above': '>='}
_CONTENT = re.compile(r'\b(?:mention(?:ing|s|ed)?|show(?:ing|s)?|say(?:ing|s)?|stat(?:ing|es)|documenting|reporting|describ(?:ing|es)|containing|contain|with)\s+', re.I)


def local_question(question, as_of=None):
    """Exact routing for plain document questions: '<how many|which patients|which documents> ... <scanned|faxed|outside> ...
    <type/exam> ... <mention|showing> <phrase or EF cut> [in YYYY]'. Anything else (relative dates, negation, comparisons) declines."""
    q = ' '.join((question or '').strip().split())
    low = q.lower()
    if not _CORPUS.search(q) or not re.search(_DOC_WORDS + r'|\becho', low): return None
    if '%' in low or re.search(r'\b(?:not|no|without|never|last|past|recent|since|before|after|between|by|per|each|rate|percent|share)\b', low): return None
    if re.match(r'^\s*how many\s+patients\b', low): output = 'count_patients'
    elif re.match(r'^\s*how many\b', low): output = 'count_documents'
    elif re.match(r'^\s*(?:which|list|show|find)\s+(?:the\s+)?(?:patients|members|people)\b', low): output = 'patients'
    elif re.match(r'^\s*(?:which|list|show|find)\b', low): output = 'documents'
    else: return None
    period = {'start': '', 'end': ''}
    y = re.search(r'\b(?:in|during)\s+(20\d\d)\b', q)
    if y:
        period = {'start': f'{y.group(1)}-01-01', 'end': f'{y.group(1)}-12-31'}
        q = q[:y.start()].rstrip() + q[y.end():]
    elif re.search(r'\b(?:19|20)\d\d\b', q): return None
    channels = []
    if re.search(r'\bfax(?:ed|es)?\b', low): channels.append('fax')
    if re.search(r'\bportal\b', low): channels.append('portal_upload')
    if re.search(r'\bdirect[- ]message', low): channels.append('direct_message')
    types = [t for rx, t in _TYPES if re.search(rf'\b{rx}\b', low)]
    exam = next((e for rx, e in _EXAMS if re.search(rf'\b{rx}\b', low)), '')
    review = 'unreviewed' if re.search(r'\bunreviewed\b', low) else ''
    measurement, terms = None, []
    mq = _MEASURE_Q.search(q)
    if mq:
        measurement = {'name': 'lvef', 'op': _OPS_Q[mq.group(1).lower()], 'value': int(mq.group(2)), 'unit': 'percent'}
        if re.search(r'\b(?:and|or)\b\s*\w', q[mq.end():]): return None  # a second condition this routing does not parse
    elif review and re.search(r'\b(?:are|is|were|was|remain|still)\s+(?:still\s+)?unreviewed\s*[?.]?\s*$', low):
        terms, match = [], 'all'  # a review-state question: the filters are the whole definition
    else:
        found = list(_CONTENT.finditer(q))
        if not found: return None
        phrase = re.sub(r'^(?:an?|the|any)\s+', '', q[found[-1].end():].strip().rstrip('?.').strip(), flags=re.I)
        parts = [x.strip() for x in re.split(r'\s+(?:and|or)\s+', phrase) if x.strip()]
        if not parts or any(len(x) < 3 or len(x.split()) > 4 for x in parts): return None
        terms = [re.sub(r'^(?:an?|the)\s+', '', x, flags=re.I).lower() for x in parts]
        exam_rx = next((rx for rx, e in _EXAMS if e == exam), None)
        if exam_rx:  # 'a mammogram BI-RADS 4': the exam is already a filter; the finding is the term
            terms = [re.sub(rf'^{exam_rx}\s+', '', t) for t in terms]
            if any(len(t) < 3 for t in terms): return None
        match = 'any' if re.search(r'\s+or\s+', phrase) else 'all'
    if exam and exam.strip() in ('ct', 'xray', 'mri') and not types: types = []
    params = {'terms': terms, 'match': 'all' if mq else match, 'document_types': types, 'channels': channels, 'exam': exam.strip(), 'review_status': review,
              'measurement': measurement, 'period': period, 'output': output}
    params = validate(params)
    corpus = 'faxed' if channels == ['fax'] else 'scanned/outside'
    return one_node(question, OPERATION, params, f'{corpus} document OCR search with page citations ({VERSION})')
