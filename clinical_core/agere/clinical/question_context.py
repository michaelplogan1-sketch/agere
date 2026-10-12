"""Question-led source-scope proposals using existing retrieval and typed links.

This is candidate discovery, not proof of evidence sufficiency. The user does not
need source-table names; exact selected and unselected categories remain visible.
"""
from collections import Counter, deque
from pathlib import Path
import hashlib
import json

from ...contracts import digest
from . import context_builder as context, evidence_threads, warehouse_interpretation as native
from . import record_context

VERSION = 'question-source-scope/2'


def select_sources(records, roots, as_of):
    ix = native.index(records); edges = []; neighbors = {r['record_id']: set() for r in records}
    if not roots or set(roots)-set(ix['records']):
        raise ValueError('Received same-patient retrieval roots required')
    for r in records:
        statement = native.statement(r, as_of, ix)
        if not statement: continue
        for link in statement['linked_sources']:
            source, target = r['record_id'], link['target']['record_id']
            neighbors[source].add(target); neighbors[target].add(source)
            edges.append({'from': source, 'to': target, 'field': link['field']})
    selected = set(roots); pending = deque(sorted(roots))
    while pending:
        for other in sorted(neighbors[pending.popleft()]):
            if other not in selected: selected.add(other); pending.append(other)
    categories = sorted({ix['records'][key]['source_table'] for key in selected})
    return {'retrieved_record_ids': sorted(set(roots)), 'connected_record_ids': sorted(selected),
            'reference_edges': [e for e in edges if e['from'] in selected and e['to'] in selected],
            'selected_categories': categories,
            'category_reasons': {s: {'retrieved_records': sorted(k for k in roots if ix['records'][k]['source_table'] == s),
                'linked_records': sorted(k for k in selected-set(roots) if ix['records'][k]['source_table'] == s)} for s in categories}}


def prepare(path, patient, question, retriever):
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 1500:
        raise ValueError('Bounded source-review question required')
    db, meta = evidence_threads.connect(path)
    try:
        records = evidence_threads.checked_records(db, patient, meta['snapshot']['as_of'])
    finally: db.close()
    retrieval = retriever.search(question, patient=patient, limit=8)
    by_id = {r['record_id']: r for r in records}
    roots = set()
    for hit in retrieval['evidence']:
        s = hit['source']; r = by_id.get(s['record_id'])
        if r is None or s['patient_id'] != patient or s['record_hash'] != digest(r):
            raise ValueError('Retrieved source changed or belongs to another patient; refresh retrieval before scoping')
        roots.add(s['record_id'])
    inventory = Counter(r['source_table'] for r in records)
    proposal = select_sources(records, roots, meta['snapshot']['as_of']) if roots else {
        'retrieved_record_ids': [], 'connected_record_ids': [], 'reference_edges': [],
        'selected_categories': [], 'category_reasons': {}}
    selected = set(proposal['selected_categories'])
    receipt = {'schema': VERSION, 'synthetic': True, 'patient_id': patient, 'question': question,
        **proposal, 'snapshot': meta['snapshot'], 'patient_source_fingerprint': digest(records),
        'retrieval': {'method': retrieval['method'], 'evidence': retrieval['evidence'],
            'candidate_passages': retrieval['candidate_passages'], 'cache_hit': retrieval['cache_hit'],
            'elapsed_seconds': retrieval['elapsed_seconds'], 'complete_evidence_guarantee': False},
        'records_in_selected_categories': sum(inventory[s] for s in selected),
        'unselected_categories': [{'source': s, 'received_records': n} for s, n in sorted(inventory.items()) if s not in selected],
        'scope_basis': 'Eight ranked source passages and their complete same-patient typed-reference components; other records in those categories remain excluded.',
        'scope_sufficiency': 'unassessed', 'question_fully_answerable': False,
        'missingness': 'A retrieval miss, an unselected source, an unresolved interpretation and care not received are different possibilities.',
        'clinical_state_established': False, 'new_paid_api_calls': 0,
        'implementation': digest([VERSION, hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), native.identity()])}
    receipt['context_id'] = None
    if not selected:
        receipt.update(status='no_retrieved_evidence', limitation='No retrieved roots; this does not establish absence of care or source evidence.')
    elif len(selected) > 30:
        receipt.update(status='scope_exceeds_category_bound', limitation='Complete source closure exceeds the context category limit; no categories silently dropped.')
    else:
        row = record_context.create(path, patient, question, proposal['connected_record_ids'])
        receipt.update(context_id=row['id'], context_budget=row['budget'], context_omissions=row['omissions'],
            record_scope=row['record_scope'], selected_record_count=len(proposal['connected_record_ids']),
            status='ready_for_source_review' if row['dispatch']['source_review_allowed'] else 'context_incomplete',
            limitation='Only explicitly selected connected records are in scope; selection sufficiency and interpretation accuracy are unassessed.')
    # Check both the roots and reference closure against source changes during retrieval/assembly.
    db, _ = evidence_threads.connect(path)
    try:
        if digest(evidence_threads.checked_records(db, patient, meta['snapshot']['as_of'])) != receipt['patient_source_fingerprint']:
            raise ValueError('Source scope changed while planning; prepare again')
    finally: db.close()
    receipt['id'] = digest(receipt)
    out = Path(path)/'question-context'; out.mkdir(exist_ok=True)
    (out/(receipt['id']+'.json')).write_text(json.dumps(receipt, indent=2))
    return receipt
