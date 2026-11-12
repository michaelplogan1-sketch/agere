"""Additive, source-bound escalation from a completed focused document review.

Native typed references and explicitly selected source/type/month branches select additional received documents. Selection is not
interpretation, sufficiency, clinical truth, or a chart-wide absence judgment.
The existing reader judges only newly added documents; prior findings survive.
"""
import json
import uuid
from pathlib import Path

from ...contracts import digest
from . import record_context, received_reference_graph as references

VERSION = 'residual-evidence-navigation/2'


def identity():
    return digest([VERSION, Path(__file__).read_text(), references.identity()])


def _review_identity(call):
    # Replay can read the same source via cache with different elapsed time and
    # accounting. Reuse the existing replay equivalence contract; source hashes,
    # exact field meanings, findings, uncertainty and population remain bound.
    from .harness_improvement import comparison_result
    return digest([call['name'], call['input'], comparison_result(call['name'], call['result'], [])])


def _prior(tools, view):
    matches = [c for c in tools.native_calls if c.get('name') == 'review_documents'
               and isinstance(c.get('result'), dict)
               and c['result'].get('result_view') == view]
    if len(matches) != 1:
        raise ValueError('Navigation requires one executed native document-review result')
    call = matches[0]
    if call.get('input', {}).get('navigation_id'):
        saved, original, _, _ = load(tools, call['input']['navigation_id'])
        if (call['result'].get('navigation', {}).get('id') != saved['id']
                or not call['result'].get('navigation_coverage')):
            raise ValueError('Completed navigation producer required for continued expansion')
        # The completed result owns the latest findings; its immutable parent
        # owns the question, conditional field scope and original time window.
        call = {**call, 'input': original['input']}
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
    coverage = {m['patient_id']: m for m in (call['result'].get('navigation_coverage') or call['result']['coverage'])}
    inventory = []
    selected = set()
    for row in rows:
        pid = row['patient_id']
        tools.in_scope(pid)
        metadata = coverage[pid].get('source_selection')
        if not isinstance(metadata, dict) or metadata.get('complete_chart_review') is not False:
            raise ValueError('Original focused source inventory unavailable')
        docs = {r[0]: {'record_id': r[0], 'date': str(r[4] or '')[:10],
                       'text': r[5], 'source': str(r[1] or ''), 'stream': str(r[2] or ''),
                       'document_type': str(r[3] or '')} for r in tools._texts(pid)}
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
                received = current.get(rid)
                if received is None:
                    state = 'unbound_received_source'
                elif any(str(received.get(k) or '')[:10] > tools.as_of for k in ('available_date', 'event_date')):
                    state = 'not_received_by_cutoff'
                basis = ('baseline' if rid in roots else
                         'typed_reference' if rid in linked['record_ids'] else 'unexamined')
                added = basis == 'typed_reference' and state in ('in_window', 'unknown_date')
                if added:
                    selected.add((pid, rid))
                units.append({'record_id': rid, 'date': d['date'],
                              'source': d['source'], 'stream': d['stream'],
                              'document_type': d['document_type'], 'characters': len(d['text']),
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
                          'field_applicability': metadata.get('field_applicability', {}),
                          'received_source_digest': digest(records),
                          'document_inventory_digest': digest(docs)})
    return inventory, sorted(selected)


def _branches(inventory):
    """Complete native hierarchy; no generated topics, relevance pruning or text summaries.

    Group only eligible, previously unexamined sources. A group may span the
    original population; selection never adds a new patient. Content-bound IDs
    let a model choose several branches without receiving their document text.
    """
    groups = {}
    for patient in inventory:
        pid = patient['patient_id']
        for field, account in patient['fields'].items():
            if patient.get('field_applicability', {}).get(field) is False:
                continue
            for u in account['units']:
                if u['selection'] != 'unexamined' or u['stratum'] not in ('in_window', 'unknown_date'):
                    continue
                source = (u['source'], u['stream'])
                kind = (*source, u['document_type'])
                month = (*kind, u['date'][:7] if u['date'] else 'unknown_date')
                for key in (source, kind, month):
                    groups.setdefault(key, {})[(pid, u['record_id'])] = u
    identities = {key: digest([VERSION, 'branch', key,
        [(pid, rid, u['document_text_digest']) for (pid, rid), u in sorted(units.items())]])
        for key, units in groups.items()}
    branches, members = [], {}
    for key, units in sorted(groups.items()):
        ident = identities[key]; members[ident] = set(units)
        parent = identities.get(key[:-1]) if len(key) > 2 else None
        branches.append({'id': ident, 'parent_id': parent,
            'level': {2: 'source', 3: 'document_type', 4: 'month'}[len(key)],
            'source': key[0], 'stream': key[1],
            'document_type': key[2] if len(key) > 2 else None,
            'month': key[3] if len(key) > 3 else None,
            'documents': len(units), 'patients': len({pid for pid, _ in units}),
            'characters': sum(u['characters'] for u in units.values()),
            'clinical_relevance': 'unassessed'})
    return branches, members


def _select(inventory, linked, branch_ids):
    branches, members = _branches(inventory)
    if not isinstance(branch_ids, list) or any(not isinstance(b, str) for b in branch_ids):
        raise ValueError('Use exact branch IDs from the current navigation inventory')
    if set(branch_ids) - members.keys():
        raise ValueError('Selected source branch is absent or changed; inspect a current navigation inventory')
    added = set().union(*(members[b] for b in branch_ids)) if branch_ids else set()
    for patient in inventory:
        for field, account in patient['fields'].items():
            if patient.get('field_applicability', {}).get(field) is False:
                continue
            for unit in account['units']:
                if ((patient['patient_id'], unit['record_id']) in added
                        and unit['selection'] == 'unexamined' and unit['stratum'] in ('in_window', 'unknown_date')):
                    unit.update(selection='explicit_source_branch', interpretation='selected_requires_review')
    for branch in branches:
        selected = members[branch['id']] & added
        branch['selected_documents'] = len(selected)
        branch['selection'] = ('selected' if selected == members[branch['id']] else
                               'partially_selected' if selected else 'unexamined')
    return branches, sorted(set(linked) | added)


def actor_reading(saved):
    """Send the complete branch choices, not a duplicate per-field source ledger.

    The immutable native receipt retains every source and exact membership.
    This is lossless execution bookkeeping reduction, not evidence sampling or
    a model-generated summary of medical contents.
    """
    from collections import Counter
    inventory = saved['inventory']
    units = [u for p in inventory for f in p['fields'].values() for u in f['units']]
    shown = {k: v for k, v in saved.items()
             if k not in ('inventory', 'patient_ids', 'additional_documents')}
    # Broken references and structured linked sources affect the next step.
    # Keep their exact source identities, rather than hiding them behind totals.
    shown['reference_issues'] = [{'patient_id': p['patient_id'], 'field': field, **issue}
        for p in inventory for field, account in p['fields'].items()
        for issue in account['reference_issues']]
    shown['linked_structured_sources'] = [{'patient_id': p['patient_id'], 'field': field, **source}
        for p in inventory for field, account in p['fields'].items()
        for source in account['linked_no_document']]
    shown['native_inventory'] = {
        'navigation_id': saved['id'], 'inventory_digest': digest(inventory),
        'patients': len(saved['patient_ids']),
        'documents': len({(p['patient_id'], u['record_id']) for p in inventory
                          for f in p['fields'].values() for u in f['units']}),
        'additional_documents': len(saved['additional_documents']),
        'field_document_states': dict(Counter(u['selection'] for u in units)),
        'field_document_time_strata': dict(Counter(u['stratum'] for u in units)),
        'meaning': 'Exact inventory and membership retained in the immutable native receipt. Every selectable branch is shown. Counts describe source selection, not clinical findings or complete review.'}
    return shown


def _continuation_coverage(saved):
    """Carry source versions and completed reading scope into another expansion.

    A document processed with unresolved findings still counts as processed;
    its unresolved findings survive in the result, never becoming a negative.
    """
    coverage = []
    additional = {(d['patient_id'], d['record_id']) for d in saved['additional_documents']}
    for patient in saved['inventory']:
        sources = {}
        for field, account in patient['fields'].items():
            applicable = patient.get('field_applicability', {}).get(field) is not False
            for unit in account['units']:
                item = sources.setdefault(unit['record_id'], {
                    'record_id': unit['record_id'], 'date': unit['date'],
                    'document_text_digest': unit['document_text_digest'], 'selected_fields': []})
                if (unit['selection'] == 'baseline' or
                        (applicable and (patient['patient_id'], unit['record_id']) in additional)):
                    item['selected_fields'].append(field)
        coverage.append({'patient_id': patient['patient_id'], 'source_selection': {
            'complete_chart_review': False, 'field_applicability': patient.get('field_applicability', {}),
            'sources': list(sources.values())}})
    return coverage


def _file(tools, key):
    if not isinstance(key, str) or len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
        raise ValueError('Exact navigation receipt ID required')
    return Path(tools.stack.path) / 'source-navigations' / (key + '.json')


def prepare(tools, review_result_view, branch_ids=None, basis_navigation_id=None):
    guard = digest(tools.continuation_identity())
    call, questions, rows = _prior(tools, review_result_view)
    inventory, selected = _inventory(tools, call, questions, rows)
    if branch_ids is not None:
        if not isinstance(branch_ids, list) or any(not isinstance(b, str) for b in branch_ids):
            raise ValueError('Use a list of exact source branch IDs')
        if not basis_navigation_id:
            raise ValueError('Branch selection requires its basis_navigation_id from the inspected inventory')
        basis, _, _, _ = load(tools, basis_navigation_id)
        if basis['prior_result_view'] != review_result_view:
            raise ValueError('Source branches belong to a different review result')
        # Planning can refine several branches before the one additional read.
        branch_ids = sorted(set(basis.get('selected_branch_ids', [])) | set(branch_ids))
    elif basis_navigation_id is not None:
        raise ValueError('Supply branch_ids with basis_navigation_id')
    branch_ids = branch_ids or []
    branches, selected = _select(inventory, selected, branch_ids)
    saved = {'schema': VERSION, 'implementation': identity(),
             'question': tools.question, 'as_of': tools.as_of,
             'continuation_identity': guard, 'prior_result_view': review_result_view,
             'prior_call_digest': _review_identity(call), 'questions': questions,
             'patient_ids': [r['patient_id'] for r in rows],
             'inventory': inventory, 'source_branches': branches,
             'selected_branch_ids': branch_ids, 'basis_navigation_id': basis_navigation_id,
             'branch_selection_basis': 'explicit_investigator_selection_not_qualified_relevance',
             'additional_documents': [{'patient_id': p, 'record_id': r} for p, r in selected],
             'new_paid_api_calls': 0, 'selection_sufficiency': 'unassessed',
             'absence_established': False, 'clinical_qualified': False,
             'status': 'additional_received_sources_require_review' if selected
             else 'unexamined_source_branches_available' if branches
             else 'no_linked_expansion_unexamined_sources_remain',
             'limits': ['Typed links and explicit source branches select evidence; they do not establish clinical relationships or truth.',
                        'All baseline findings remain. Unvisited, unknown-date and unresolved-reference evidence stays explicit.',
                        'No probability, relevance cutoff, source sampling or automatic whole-chart reread is used.']}
    saved['id'] = digest(saved)
    if digest(tools.continuation_identity()) != guard:
        raise ValueError('Question sources or definitions changed during navigation')
    # Check actual source versions again; a source mutation cannot produce a
    # receipt just because the outer snapshot/config signature stayed stable.
    current, linked = _inventory(tools, call, questions, rows)
    current_branches, current_selected = _select(current, linked, branch_ids)
    if (current, current_selected, current_branches) != (inventory, selected, branches):
        raise ValueError('Source changed during navigation')
    target = _file(tools, saved['id'])
    target.parent.mkdir(exist_ok=True)
    if target.exists() and json.loads(target.read_text()) != saved:
        raise ValueError('Immutable navigation receipt changed')
    temporary = target.with_suffix('.' + uuid.uuid4().hex + '.writing')
    temporary.write_text(json.dumps(saved, ensure_ascii=False, allow_nan=False))
    temporary.replace(target)
    return {**saved, 'branch_selection': {'name': 'plan_evidence_expansion',
            'input': {'review_result_view': review_result_view, 'basis_navigation_id': saved['id']},
            'instruction': 'Supply branch_ids from source_branches; source/type/month branches can be combined. Unselected evidence remains unexamined.'},
            'review_next': {'name': 'review_documents', 'input': {'navigation_id': saved['id']}}
            if selected else None}


def _navigation_meaning(saved):
    # Runtime identities are addresses. Every source, field, population,
    # branch, selected document and prior reading must still match exactly.
    return {k: v for k, v in saved.items()
            if k not in {'id', 'implementation', 'continuation_identity'}}


def revalidate_recovery(tools, previous, current):
    """Keep an owned saved address only after executing the same plan today.

    Called by explicit checkpoint recovery after its source/context and action
    provenance checks. The current native result proves navigation equivalence,
    never an answer verdict. Aliases live only in this guarded Tools instance;
    immutable historical receipts are not rewritten or made generally current.
    """
    if not isinstance(previous, dict) or not isinstance(current, dict):return None
    old = {k: v for k, v in previous.items() if k not in {'branch_selection', 'review_next'}}
    new = {k: v for k, v in current.items() if k not in {'branch_selection', 'review_next'}}
    if (old.get('schema') != VERSION or new.get('schema') != VERSION
            or old.get('id') == new.get('id') or _navigation_meaning(old) != _navigation_meaning(new)):
        return None
    for saved in (old, new):
        if (digest({k: v for k, v in saved.items() if k != 'id'}) != saved.get('id')
                or json.loads(_file(tools, saved['id']).read_text()) != saved):return None
    active, _, _, _ = load(tools, new['id'])
    if active != new:return None
    aliases = dict(getattr(tools, '_recovered_navigation_addresses', {}))
    if new['id'] in aliases:return None  # no alias chains
    proof = {'schema': 'native-navigation-revalidation/1', 'saved_id': old['id'],
             'current_id': new['id'], 'current_guard': digest(tools.continuation_identity()),
             'navigation_meaning_digest': digest(_navigation_meaning(new)),
             'native_reexecuted': True, 'prior_verdict_accepted': False}
    aliases[old['id']] = proof
    tools._recovered_navigation_addresses = aliases
    return proof


def load(tools, key):
    saved = json.loads(_file(tools, key).read_text())
    if saved.get('id') != key or digest({k: v for k, v in saved.items() if k != 'id'}) != key:
        raise ValueError('Navigation receipt identity changed')
    aliases = getattr(tools, '_recovered_navigation_addresses', {})
    proof = aliases.get(key)
    if proof is not None:
        if (proof['current_id'] in aliases or proof['current_guard'] != digest(tools.continuation_identity())
                or proof['navigation_meaning_digest'] != digest(_navigation_meaning(saved))):
            raise ValueError('Recovered navigation scope or meaning changed')
        current, call, questions, rows = load(tools, proof['current_id'])
        if _navigation_meaning(current) != _navigation_meaning(saved):
            raise ValueError('Recovered navigation evidence changed')
        return saved, call, questions, rows
    if (saved.get('implementation') != identity()
            or saved.get('question') != tools.question or saved.get('as_of') != tools.as_of
            or saved.get('continuation_identity') != digest(tools.continuation_identity())):
        raise ValueError('Navigation scope, source or implementation changed')
    call, questions, rows = _prior(tools, saved['prior_result_view'])
    inventory, selected = _inventory(tools, call, questions, rows)
    branches, selected = _select(inventory, selected, saved.get('selected_branch_ids', []))
    if (branches != saved.get('source_branches') or _review_identity(call) != saved['prior_call_digest'] or inventory != saved['inventory']
            or [{'patient_id': p, 'record_id': r} for p, r in selected] != saved['additional_documents']):
        raise ValueError('Navigation source evidence changed; prepare a current review')
    return saved, call, questions, rows


def review(tools, key):
    saved, prior, questions, rows = load(tools, key)
    if not saved['additional_documents']:
        raise ValueError('No new selected documents to review; unresolved does not mean absent')
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
    continuation = _continuation_coverage(saved)
    processed = {m['patient_id']: [s for s in m['source_selection']['sources'] if s['selected_fields']] for m in continuation}
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
               'documents_reviewed': len({(s['date'], s['document_text_digest']) for s in processed[old['patient_id']]}),
               'source_records_processed': len(processed[old['patient_id']]),
               'document_reading_attempts': old.get('document_reading_attempts', old.get('documents_reviewed', 0)) + added['documents_reviewed'],
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
            'navigation_coverage': continuation,
            'navigation': {'id': key, 'prior_result_view': saved['prior_result_view'],
                           'prior_findings_preserved': True, 'absence_established': False,
                           'selected_branch_ids': saved['selected_branch_ids'],
                           'selection_sufficiency': 'unassessed'},
            'method': 'Additive typed-reference and explicit source-branch evidence review; documents_reviewed counts unique dated text contents across passes, source_records_processed counts source identities, and document_reading_attempts retains repeated reading units. Prior findings and uncertainties retained. Unexamined sources remain unresolved.'}
    # The ordinary consumer verifies rows against document_reviews as well as
    # native result lineage. Retain the combined producer without duplicating
    # any provider receipt, charge or source judgment.
    tools.document_reviews.append({'questions': questions, 'scope': saved['patient_ids'],
        'manifest': output['coverage'], 'rows': combined, 'provider_receipts': [],
        'typed_checks': [], 'source_coverage': [], 'source_scope': 'focused',
        'navigation': output['navigation'], 'new_paid_api_calls': 0})
    return output
