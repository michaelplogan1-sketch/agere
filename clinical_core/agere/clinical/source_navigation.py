"""Additive, source-bound escalation from a completed focused document review.

Native typed references select additional received documents. Selection is not
interpretation, sufficiency, clinical truth, or a chart-wide absence judgment.
The existing reader judges only newly added documents; prior findings survive.
"""
import json
import uuid
from pathlib import Path

from ...contracts import digest
from . import record_context, received_reference_graph as references

VERSION = 'residual-evidence-navigation/1'


def identity():
    return digest([VERSION, Path(__file__).read_text(), references.identity()])


def _prior(tools, view):
    matches = [c for c in tools.native_calls if c.get('name') == 'review_documents'
               and isinstance(c.get('result'), dict)
               and c['result'].get('result_view') == view]
    if len(matches) != 1:
        raise ValueError('Navigation requires one executed native document-review result')
    call = matches[0]
    if (call['result'].get('error') or call['result'].get('source_scope') != 'focused'
            or call.get('input', {}).get('candidate_result_view')
            or call.get('input', {}).get('record_ids')
            or call.get('input', {}).get('navigation_id')):
        raise ValueError('Navigation starts from the original complete focused-review inventory')
    from .document_review import validate_questions
    questions = validate_questions(call.get('input', {}).get('questions'))
    if any(not isinstance(q, dict) for q in questions.values()):
        raise ValueError('Exact entity/relation/subject questions required')
    if call['result'].get('truncated') or call['result'].get('preview_only'):
        raise ValueError('Complete native review required, not a reading preview')
    rows = call['result'].get('rows')
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise ValueError('Complete native review rows unavailable')
    ids = [r['patient_id'] for r in rows]
    if len(set(ids)) != len(ids):
        raise ValueError('Native review population is not unique')
    return call, questions, rows


def _inventory(tools, call, questions, rows):
    coverage = {m['patient_id']: m for m in call['result']['coverage']}
    inventory = []
    selected = set()
    for row in rows:
        pid = row['patient_id']
        tools.in_scope(pid)
        metadata = coverage[pid].get('source_selection')
        if not isinstance(metadata, dict) or metadata.get('complete_chart_review') is not False:
            raise ValueError('Original focused source inventory unavailable')
        docs = {r[0]: {'record_id': r[0], 'date': str(r[4] or '')[:10],
                       'text': r[5]} for r in tools._texts(pid)}
        # Exact contents of every prior candidate, including unselected ones,
        # are prerequisites of a safe incremental selection.
        for source in metadata['sources']:
            d = docs.get(source['record_id'])
            if (d is None or d['date'] != source['date']
                    or digest(d['text']) != source['document_text_digest']):
                raise ValueError('Prior document source changed; prepare a current review')
        records, meta = record_context.received(tools.stack.path, pid)
        if str(meta['snapshot']['as_of'])[:10] != tools.as_of:
            raise ValueError('Navigation and original review snapshots differ')
        current = {r['record_id']: r for r in records}
        if len(current) != len(records):
            raise ValueError('Duplicate received source identities')
        fields = {}
        for field in questions:
            roots = sorted(s['record_id'] for s in metadata['sources']
                           if field in s['selected_fields'])
            if set(roots) - set(current):
                raise ValueError('Baseline roots cannot bind to received source records')
            linked = references.build(records, pid, tools.as_of, roots,
                                      maximum=len(records)) if roots else {
                'record_ids': [], 'edges': [], 'issues': []}
            # No heuristic date proximity or encounter inference. Only explicit
            # typed links establish adjacency. Unknown dates remain unexamined
            # or eligible for reading; they never mean outside the question.
            start = call['input'].get('date_from')
            end = call['input'].get('date_to')
            def stratum(d):
                day = d['date']
                return ('unknown_date' if not day else
                        'outside_window' if (start and day < start) or (end and day > end)
                        else 'in_window')
            units = []
            for rid, d in sorted(docs.items()):
                state = stratum(d)
                basis = ('baseline' if rid in roots else
                         'typed_reference' if rid in linked['record_ids'] else 'unexamined')
                added = basis == 'typed_reference' and state != 'outside_window'
                if added:
                    selected.add((pid, rid))
                units.append({'record_id': rid, 'date': d['date'],
                              'document_text_digest': digest(d['text']),
                              'stratum': state, 'selection': basis,
                              'interpretation': 'prior_review_retained' if basis == 'baseline'
                              else 'selected_requires_review' if added else 'unexamined'})
            fields[field] = {'baseline_record_ids': roots,
                             'reference_edges': linked['edges'],
                             'reference_issues': linked['issues'], 'units': units,
                             'linked_no_document': [
                                 {'record_id': rid, 'record_hash': digest(current[rid]),
                                  'source_table': current[rid]['source_table'],
                                  'status': 'received_structured_source_not_submitted_to_document_reader'}
                                 for rid in linked['record_ids'] if rid not in docs],
                             'selection_sufficiency': 'unassessed',
                             'absence_established': False}
        inventory.append({'patient_id': pid, 'fields': fields,
                          'received_source_digest': digest(records),
                          'document_inventory_digest': digest(docs)})
    return inventory, sorted(selected)


def _file(tools, key):
    if not isinstance(key, str) or len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
        raise ValueError('Exact navigation receipt ID required')
    return Path(tools.stack.path) / 'source-navigations' / (key + '.json')


def prepare(tools, review_result_view):
    guard = digest(tools.continuation_identity())
    call, questions, rows = _prior(tools, review_result_view)
    inventory, selected = _inventory(tools, call, questions, rows)
    saved = {'schema': VERSION, 'implementation': identity(),
             'question': tools.question, 'as_of': tools.as_of,
             'continuation_identity': guard, 'prior_result_view': review_result_view,
             'prior_call_digest': digest(call), 'questions': questions,
             'patient_ids': [r['patient_id'] for r in rows],
             'inventory': inventory,
             'additional_documents': [{'patient_id': p, 'record_id': r} for p, r in selected],
             'new_paid_api_calls': 0, 'selection_sufficiency': 'unassessed',
             'absence_established': False, 'clinical_qualified': False,
             'status': 'additional_received_sources_require_review' if selected
             else 'no_linked_expansion_unexamined_sources_remain',
             'limits': ['Typed links select evidence; they do not establish clinical relationships or truth.',
                        'All baseline findings remain. Unvisited, unknown-date and unresolved-reference evidence stays explicit.',
                        'No probability, relevance cutoff, source sampling or automatic whole-chart reread is used.']}
    saved['id'] = digest(saved)
    if digest(tools.continuation_identity()) != guard:
        raise ValueError('Question sources or definitions changed during navigation')
    # Check actual source versions again; a source mutation cannot produce a
    # receipt just because the outer snapshot/config signature stayed stable.
    if _inventory(tools, call, questions, rows) != (inventory, selected):
        raise ValueError('Source changed during navigation')
    target = _file(tools, saved['id'])
    target.parent.mkdir(exist_ok=True)
    if target.exists() and json.loads(target.read_text()) != saved:
        raise ValueError('Immutable navigation receipt changed')
    temporary = target.with_suffix('.' + uuid.uuid4().hex + '.writing')
    temporary.write_text(json.dumps(saved, ensure_ascii=False, allow_nan=False))
    temporary.replace(target)
    return {**saved, 'review_next': {'name': 'review_documents', 'input': {'navigation_id': saved['id']}}
            if selected else None}


def load(tools, key):
    saved = json.loads(_file(tools, key).read_text())
    if (saved.get('id') != key or digest({k: v for k, v in saved.items() if k != 'id'}) != key
            or saved.get('implementation') != identity()
            or saved.get('question') != tools.question or saved.get('as_of') != tools.as_of
            or saved.get('continuation_identity') != digest(tools.continuation_identity())):
        raise ValueError('Navigation scope, source or implementation changed')
    call, questions, rows = _prior(tools, saved['prior_result_view'])
    inventory, selected = _inventory(tools, call, questions, rows)
    if (digest(call) != saved['prior_call_digest'] or inventory != saved['inventory']
            or [{'patient_id': p, 'record_id': r} for p, r in selected] != saved['additional_documents']):
        raise ValueError('Navigation source evidence changed; prepare a current review')
    return saved, call, questions, rows


def review(tools, key):
    saved, prior, questions, rows = load(tools, key)
    if not saved['additional_documents']:
        raise ValueError('No new linked documents to review; unresolved does not mean absent')
    # Re-enter the existing guarded tool boundary. Existing exact source cache,
    # failed-key protection, source judgments and all-attempt accounting apply.
    result = tools._call({'name': 'review_documents', 'input': {
        'patient_ids': saved['patient_ids'], 'questions': questions,
        'record_ids': sorted({r['record_id'] for r in saved['additional_documents']}),
        **({'field_patient_queries': prior['input']['field_patient_queries']}
           if prior['input'].get('field_patient_queries') else {}),
        'source_scope': 'focused'}})
    load(tools, key)
    new = {r['patient_id']: r for r in result['rows']}
    combined = []
    def union(a, b):
        values = list(a)
        seen = {digest(v) for v in values}
        for value in b:
            if digest(value) not in seen:
                values.append(value); seen.add(digest(value))
        return values
    def decoded(row, field, default):
        value = row.get(field)
        return json.loads(value) if isinstance(value, str) else default if value is None else value
    for old in rows:
        added = new[old['patient_id']]
        findings = json.loads(old['review_json'])
        for field, values in json.loads(added['review_json']).items():
            findings[field] = union(findings.get(field, []), values)
        has_additions = any(d['patient_id'] == old['patient_id'] for d in saved['additional_documents'])
        row = {**old, 'review_json': json.dumps(findings),
               'navigation_review_status': added['review_status'],
               'additional_documents_reviewed': added['documents_reviewed'],
               'navigation_review_limitations': added['review_limitations']}
        if has_additions:
            # A successful new reading cannot clear prior uncertainty; a failed
            # new reading must also reach the standard downstream status fields.
            clean = {'reviewed', 'reviewed_candidates'}
            if old['review_status'] not in clean or added['review_status'] not in clean:
                row['review_status'] = 'needs_review'
            row['review_limitations'] = json.dumps(union(decoded(old, 'review_limitations', []),
                                                        decoded(added, 'review_limitations', [])))
            row['review_unresolved_json'] = json.dumps(union(decoded(old, 'review_unresolved_json', []),
                                                            decoded(added, 'review_unresolved_json', [])))
            row['evidence_scope_json'] = json.dumps({
                'schema': VERSION, 'navigation_id': key,
                'prior_scope': decoded(old, 'evidence_scope_json', None),
                'additional_record_ids': [d['record_id'] for d in saved['additional_documents']
                                          if d['patient_id'] == old['patient_id']],
                'complete_chart_review': False, 'absence_established': False})
        # Rebuild the existing derived evidence views from conserved native
        # assertions. Leaving them at the old values would hide new evidence.
        row['source_findings_json'] = json.dumps([{'field': f, **v}
            for f, values in findings.items() for v in values])
        if 'source_clinicians_json' in old or 'source_clinicians_json' in added:
            row['source_clinicians_json'] = json.dumps(union(decoded(old, 'source_clinicians_json', []),
                                                            decoded(added, 'source_clinicians_json', [])))
        states = [decoded(r, 'documentary_state_json', None) for r in (old, added)]
        if any(states):
            from .assertion_reconciliation import reconcile
            assertions = union((states[0] or {}).get('assertions', []), (states[1] or {}).get('assertions', []))
            row['documentary_state_json'] = json.dumps({**(states[0] or states[1]),
                'assertions': assertions, 'groups': reconcile(old['patient_id'], assertions, tools.as_of),
                'current_clinical_state_established': False})
        combined.append(row)
    output = {**result, 'rows': combined,
            'columns': list(dict.fromkeys(k for row in combined for k in row)),
            'coverage': prior['result']['coverage'] + result['coverage'],
            'navigation': {'id': key, 'prior_result_view': saved['prior_result_view'],
                           'prior_findings_preserved': True, 'absence_established': False,
                           'selection_sufficiency': 'unassessed'},
            'method': 'Additive typed-reference evidence review; prior findings and uncertainties retained. Unexamined sources remain unresolved.'}
    # The ordinary consumer verifies rows against document_reviews as well as
    # native result lineage. Retain the combined producer without duplicating
    # any provider receipt, charge or source judgment.
    tools.document_reviews.append({'questions': questions, 'scope': saved['patient_ids'],
        'manifest': output['coverage'], 'rows': combined, 'provider_receipts': [],
        'typed_checks': [], 'source_coverage': [], 'source_scope': 'focused',
        'navigation': output['navigation'], 'new_paid_api_calls': 0})
    return output
