"""Terminology review engine (foundation engine 'terminology_map'): how the received source labels map to the codes and
concepts the compiler queries.

Every distinct received label of one kind (lab names with their LOINC, condition labels with ICD-10/SNOMED, medication
names with their drug class) is read from the semantic layer and mapped by terminology.map_label (saved mappings and
deterministic exact matches only: free, no provider call in execution). Each row says what the data already carries
(data_concept), what the mapper finds (mapped_concept, method, codes) and a status:
  mapped      the data carries a concept
  recoverable the data carries none but the mapper maps the label (a messy label the compiler can now query)
  unmapped    neither; candidates from retrieval are listed for review (System One adjudicates them on the question path)
Questions: 'Which lab names in our data map to LOINC 4548-4?', 'Which source lab labels are unmapped?'.
"""
import re
import sqlite3
from pathlib import Path

from . import concepts, terminology

KINDS = ('lab', 'condition', 'medication')
STATUSES = ('all', 'mapped', 'recoverable', 'unmapped', 'not_mapped')
MAX_LABELS = 800
SOURCES = {  # kind -> (table, label column, source code column, data concept column)
    'lab': ('measurement', 'name', 'loinc', 'concept'),
    'condition': ('condition', 'label', 'icd10', 'concept'),
    'medication': ('medication', 'drug_name', 'rxnorm', 'drug_class'),
}
PRODUCT = {'population': 'optional', 'view': 'table',
           'use': 'TERMINOLOGY MAPPING REVIEW: which received lab / condition / medication labels map to which LOINC, ICD-10 or '
                  'drug-class codes the compiler queries, which labels are unmapped, and which messy labels the terminology mapper '
                  'recovers (saved System One adjudications + exact dictionary/library/brand matches).'}
CONTRACT = {'inputs': ['optional earlier_set'], 'parameters': {'kind': 'lab|condition|medication', 'code': '', 'status': 'all|mapped|recoverable|unmapped|not_mapped'},
            'meaning': 'Distinct received source labels of one kind with their source code, the concept the data carries, the concept and '
                       'codes the terminology mapper assigns (saved/deterministic, free) and a status: mapped / recoverable / unmapped. '
                       'code filters to one LOINC / ICD-10 prefix / drug class. Use for "which lab names map to LOINC X", '
                       '"which labels are unmapped", terminology coverage questions.'}
NATIVE_SCALARS = ('count',)


def parameter_schema(catalog, obj, string, array):
    return obj({'kind': string(KINDS, 'Which received labels to review'),
                'code': string(None, 'Optional LOINC (e.g. 4548-4), ICD-10 prefix (I50) or drug class (arni); empty = every label'),
                'status': string(STATUSES, 'all (default), mapped, recoverable, unmapped, not_mapped (= recoverable + unmapped)')})


def validate(p):
    if not isinstance(p, dict) or set(p) - {'kind', 'code', 'status'}: raise ValueError('terminology_map parameters: kind, code, status')
    kind = p.get('kind') or 'lab'
    if kind not in KINDS: raise ValueError(f'kind: one of {KINDS}')
    status = p.get('status') or 'all'
    if status not in STATUSES: raise ValueError(f'status: one of {STATUSES}')
    code = str(p.get('code') or '').strip()
    if len(code) > 40: raise ValueError('code: a single code')
    return {'kind': kind, 'code': code, 'status': status}


def _target(path):
    path = Path(path)
    return path if path.suffix == '.sqlite3' else path / 'semantic.sqlite3'


def _code_concept(kind, code):
    if not code: return None
    if kind == 'lab': return next((c for c, d in concepts.MEASUREMENTS.items() if code in d.get('loinc', [])), None)
    if kind == 'condition': return concepts.concept_for_code(code)
    return code.lower()


def execute(path, patient_ids, params, as_of):
    p = validate(params)
    table, label, source_code, data_concept = SOURCES[p['kind']]
    target = _target(path)
    db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    try:
        cols = {r[1] for r in db.execute(f'PRAGMA table_info({table})')}
        code_sql = source_code if source_code in cols else 'NULL'
        where, args = '', []
        if patient_ids is not None:
            where = 'WHERE patient_id IN (SELECT value FROM json_each(?))'
            import json
            args = [json.dumps(list(patient_ids))]
        rows = db.execute(f'SELECT {label}, {code_sql}, {data_concept}, count(*), count(DISTINCT patient_id) FROM {table} {where} '
                          f'GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT {MAX_LABELS}', args).fetchall()
    finally:
        db.close()
    store = terminology.store_path(target.parent)
    want = _code_concept(p['kind'], p['code'])
    out = []
    for name, code, have, records, patients in rows:
        if name is None: continue
        m = terminology.map_label(name, p['kind'], store=store, adjudicate_with=False)
        concept = m.get('concept')
        status = 'mapped' if have else ('recoverable' if concept else 'unmapped')
        if p['status'] == 'not_mapped' and status == 'mapped': continue
        if p['status'] not in ('all', 'not_mapped') and status != p['status']: continue
        if p['code']:
            hit = (str(code or '').upper().startswith(p['code'].upper()) or (want and (have == want or concept == want))
                   or (p['kind'] == 'medication' and p['code'].lower() in (m.get('classes') or [])))
            if not hit: continue
        out.append({'label': name, 'source_code': code, 'data_concept': have, 'mapped_concept': concept, 'method': m.get('method'),
                    'codes': ', '.join((m.get('codes') or {}).get('codes') or []), 'status': status, 'records': records, 'patients': patients,
                    'candidates': ', '.join(f"{c['concept']} ({c['score']:.2f})" for c in (m.get('candidates') or [])[:3])})
    return {'kind': p['kind'], 'code': p['code'], 'status': p['status'], 'rows': out, 'labels_read': len(rows),
            'concept_for_code': want, 'capped': len(rows) >= MAX_LABELS, 'version': terminology.VERSION}


def answer(report, plan, question):
    n = len(report['rows'])
    what = f"{report['kind']} labels" + (f" mapping to {report['code']}" + (f" ({report['concept_for_code']})" if report['concept_for_code'] else '') if report['code'] else '')
    if report['status'] != 'all': what += f" with status {report['status']}"
    limits = [f"Terminology mapping ({report['version']}): saved System One adjudications and exact library / dictionary / brand matches; "
              'labels the mapper cannot decide stay unmapped with their retrieval candidates.']
    if report['capped']: limits.append(f'Only the {MAX_LABELS} most frequent distinct labels were reviewed.')
    return {'status': 'answered', 'answer_type': 'table', 'table': report['rows'], 'count': n, 'patient_ids': [],
            'value': f"{n} {what} (of {report['labels_read']} distinct received labels).", 'limitations': limits,
            'membership_role': 'contributing'}


_KIND = r'(?P<kind>lab(?:oratory)?(?: test)?|condition|diagnosis|problem|medication|drug)'
_CODE_Q = re.compile(r'^(?:which|what)\s+(?:source\s+|received\s+)?' + _KIND + r'\s+(?:names|labels|strings)(?:\s+in\s+(?:our|the)\s+data)?\s+'
                     r'(?:map|mapped|are mapped|correspond)\s+to\s+(?:loinc|icd-?10(?:-cm)?|the)?\s*(?:code\s+)?(?P<code>[A-Za-z0-9][\w.\-]*)$', re.I)
_UNMAPPED_Q = re.compile(r'^(?:which|what|list(?:\s+the)?)\s+(?:source\s+|received\s+)?' + _KIND + r'\s+(?:names|labels|strings)'
                         r'(?:\s+in\s+(?:our|the)\s+data)?\s+(?:are|remain)\s+(?P<status>unmapped|not mapped|mapped)$', re.I)


def _kind(word):
    w = word.lower()
    return 'lab' if w.startswith('lab') else 'medication' if w in ('medication', 'drug') else 'condition'


def local_question(question, as_of=None):
    from .foundation_engines import graph
    q = ' '.join(question.strip().rstrip('?.').split())
    m = _CODE_Q.match(q)
    if m:
        params = {'kind': _kind(m['kind']), 'code': m['code'], 'status': 'all'}
        return graph(question, 'terminology_map', params, 'Received labels mapped to one code (terminology mapping)')
    m = _UNMAPPED_Q.match(q)
    if m:
        status = 'mapped' if m['status'].lower() == 'mapped' else 'not_mapped'
        return graph(question, 'terminology_map', {'kind': _kind(m['kind']), 'code': '', 'status': status},
                     'Received labels and their mapping status (terminology mapping)')
    return None
