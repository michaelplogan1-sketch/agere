"""Current reconciliation contract: explicit references and complete claim accounting.

Reuses immutable source passes and the existing bounded excerpt gateway. Previous
answers and pinned context routes remain readable with their original engines.
"""
from .case_synthesis import input_token_total, token_count_calls
from copy import deepcopy
from pathlib import Path
import hashlib
import json

from ...contracts import digest
from . import linked_claim_reconciliation as L, linked_claim_gateway as G
from . import source_synthesis as S, case_synthesis as C, case_passes as P
from . import stable_reconciliation as R, synthesis_availability as A

VERSION = 'typed-reference-reconciliation/2'
REFERENCE_INSTRUCTIONS = ''' Output text is an object with text and unit_refs.
Write readable prose in text without any unit alias (neither uN nor pN:uM).
Put relevant source-unit names ONLY in unit_refs, using supplied exact names.
Use [] when a statement needs no unit reference. These links identify sources;
they do not establish a clinical interpretation. Do not guess an ambiguous
reference in original claim prose. Original prose is unchanged; original_pass
and the reference manifest scope its local aliases, not explicit cross-pass
mentions. Keep ambiguous source meaning unresolved. Findings still cite exact
excerpt_ids. Text limits: finding/unresolved/unanswered 1400 characters,
claim-review reason 200, review question 350; at most four unit_refs per text.
All source-pass claims receive one disposition. Uncited text and prior review
questions stay in the immutable parent; this narrower review cannot upgrade
source-unit coverage or remove its unresolved dispositions.'''


def identity():
    return digest([VERSION, L.identity(), hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])


def location(path, key):
    return C.receipt_path(path, key).parent.parent / 'reconciliation-v2' / (key + '.json')


def exists(path, key):
    return location(path, key).exists()


def output_schema(names):
    prose = C.obj({'text': C.STR, 'unit_refs': {'type': 'array', 'items': {'type': 'string', 'enum': names}}})
    schema = deepcopy(L.SCHEMA)
    finding = schema['properties']['findings']['items']['properties']
    finding['text'] = prose
    finding['unresolved']['items'] = prose
    schema['properties']['unanswered']['items'] = prose
    schema['properties']['review_steps']['items']['properties']['question'] = prose
    schema['properties']['claim_reviews']['items']['properties']['reason'] = prose
    return schema


def request(parent, row, refs, received):
    job = L.request(parent, row, refs, received)
    # Only units whose exact excerpts are in this request may be referenced.
    supplied = {v['unit'] for v in job['excerpt_pool'].values()}
    job['units'] = {k: v for k, v in job['units'].items() if k in supplied}
    job['excerpts'] = {k: v for k, v in job['excerpts'].items() if k in supplied}
    if not supplied:
        raise ValueError('No cited excerpts available for claim reconciliation')
    body = job['body']
    wire = json.loads(body['messages'][0]['content'])
    wire['reference_contract'] = VERSION
    wire['unit_reference_manifest'] = {'columns': ['name', 'source_pass', 'local_alias', 'source_unit_id'],
        'rows': [[refs[n][k] for k in ('name', 'source_pass', 'local_alias', 'source_unit_id')] for n in sorted(supplied)]}
    wire['retained_parent_scope'] = {'answer_id': parent['id'], 'answer_hash': digest(parent),
        'unresolved_source_units': len(parent['coverage'].get('unassessed_units', [])),
        'source_coverage_upgraded': False, 'prior_review_questions_submitted': False}
    body['messages'][0]['content'] = P.context.encode(wire).decode()
    body['system'] += REFERENCE_INSTRUCTIONS
    body['output_config']['format']['schema'] = output_schema(sorted(supplied))
    G.validate(body)
    return job


def parse(raw, job, refs):
    value = json.loads(''.join(v['text'] for v in raw.get('content', []) if v.get('type') == 'text'))
    if not isinstance(value, dict) or set(value) != set(L.SCHEMA['properties']):
        raise ValueError('Explicit reconciliation fields required')
    core = deepcopy(value)
    bindings = []

    def render(item, pointer, maximum):
        if not isinstance(item, dict) or set(item) != {'text', 'unit_refs'}:
            raise ValueError('Text and explicit unit references required')
        text = C.bounded_text(item['text'], maximum)
        if R.BARE.search(text) or R.QUALIFIED.search(text):
            raise ValueError('Unit references must be explicit fields, not generated prose')
        names = item['unit_refs']
        if (not isinstance(names, list) or len(names) > 4
                or any(not isinstance(n, str) or n not in job['units'] for n in names)
                or len(names) != len(set(names))):
            raise ValueError('Unknown, unsupplied or duplicate explicit unit reference')
        bindings.append({'field': pointer, 'text': text, 'unit_refs': names,
                         'evidence_references': [refs[n] for n in names]})
        return text + (' [Source units: ' + ', '.join(names) + ']' if names else '')

    for field in ('findings', 'claim_reviews', 'unanswered', 'review_steps'):
        if not isinstance(core[field], list):
            raise ValueError('Reconciliation lists required')
    for i, finding in enumerate(core['findings']):
        finding['text'] = render(finding['text'], f'/findings/{i}/text', 1400)
        if not isinstance(finding['unresolved'], list): raise ValueError('Unresolved list required')
        finding['unresolved'] = [render(v, f'/findings/{i}/unresolved/{j}', 1400) for j, v in enumerate(finding['unresolved'])]
    for i, review in enumerate(core['claim_reviews']):
        review['reason'] = render(review['reason'], f'/claim_reviews/{i}/reason', 200)
    core['unanswered'] = [render(v, f'/unanswered/{i}', 1400) for i, v in enumerate(core['unanswered'])]
    for i, step in enumerate(core['review_steps']):
        step['question'] = render(step['question'], f'/review_steps/{i}/question', 350)
    # The established strict parser verifies model/stop, citations, exact quotes,
    # all claim dispositions, conflicts and review dependencies after rendering.
    result = L.parse({**raw, 'content': [{'type': 'text', 'text': json.dumps(core)}]}, job, refs)
    return {**result, 'reference_bindings': bindings}


def prepare(path, parent_id):
    parent = S.inspect(path, parent_id)
    if parent.get('schema') == VERSION or len(parent.get('passes', [])) < 2:
        raise ValueError('Original multi-pass source-stage receipt required')
    row = S.context(path, parent['context_id'])
    refs = R.manifest(parent, row)
    received = A.snapshot(path, row)
    if received['dependency'] != parent['dependencies'].get('availability_dependency'):
        raise ValueError('Received-source availability changed')
    job = request(parent, row, refs, received)
    key = digest([VERSION, identity(), parent_id, digest(parent), digest(row), job['body']])
    return parent, row, refs, job, key


def preview(path, parent_id):
    parent, row, refs, job, key = prepare(path, parent_id)
    return {'schema': VERSION, 'parent_answer_id': parent_id, 'answer_id': key,
        'context_id': parent['context_id'], 'patient_id': row['patient_id'],
        'source_units': len(refs), 'input_claims': len(job['finding_ids']),
        'cited_excerpts': len(job['excerpt_pool']), 'request_bytes': P.request_bytes(job['body']),
        'maximum_request_bytes': G.maximum_request_bytes(), 'maximum_inference_calls': 1,
        'saved_answer_available': exists(path, key), 'new_paid_api_calls': 0,
        'scope': 'All saved source claims and their exact cited excerpts; original full units and review questions remain in the parent. No source coverage upgrade.',
        'clinical_validated': False}


def inspect(path, key):
    saved = json.loads(location(path, key).read_text())
    if (saved.get('id') != key or saved.get('code_identity') != identity() or saved.get('status') != 'complete'
            or saved.get('result_digest') != digest({k: v for k, v in saved.items() if k != 'result_digest'})):
        raise ValueError('Reconciliation integrity changed')
    parent, row, _, _, expected = prepare(path, saved['parent_answer_id'])
    if expected != key or digest(parent) != saved['parent_answer_hash'] or digest(row) != saved['context_digest']:
        raise ValueError('Reconciliation source or parent changed')
    return saved


def synthesize(path, parent_id, ledger, *, allow_live=False):
    parent, row, refs, job, key = prepare(path, parent_id)
    target = location(path, key)
    if target.exists(): return {**inspect(path, key), 'cache_hit': True, 'new_paid_api_calls': 0, 'new_token_count_calls': 0}
    if not allow_live: raise ValueError('Live synthesis route disabled; typed reconciliation pending')
    old = ledger.db.execute('SELECT status FROM calls WHERE id=?', (G.request_key(job['body']),)).fetchone()
    if old and old[0] != 'validated': raise ValueError('Prior reconciliation pending/failed; no silent retry')
    out = target.parent / 'provider'; out.mkdir(parents=True, exist_ok=True)
    progress = {'schema': VERSION, 'id': key, 'parent_answer_id': parent_id, 'status': 'pending'}
    progress_path = target.with_name(key + '-progress.json')

    def fresh():
        if digest(S.inspect(path, parent_id)) != digest(parent) or digest(S.context(path, parent['context_id'])) != digest(row):
            raise ValueError('Reconciliation source changed during inference')

    try:
        fresh(); tokens = C.token_check(job['body'], out, allow_live=allow_live); fresh()
        result, usage = G.send(job['body'], ledger, out, parse=lambda raw: parse(raw, job, refs)); fresh()
        final = P._rename(result, 'r1_')
        for review in final['claim_reviews']: review['finding_ids'] = ['r1_' + k for k in review['finding_ids']]
        group = {'result': final, 'source_records': job['source_records'], 'input_finding_ids': job['finding_ids'],
            'usage': usage, 'token_check': tokens, 'request_bytes': P.request_bytes(job['body'])}
        saved = {k: v for k, v in parent.items() if k not in ('result_digest', 'source_stage_digest')}
        saved.update(schema=VERSION, id=key, parent_answer_id=parent_id, parent_answer_hash=digest(parent),
            context_digest=digest(row), code_identity=identity(), status='complete',
            findings=final['findings'], retained_source_findings=job['original_findings'],
            claim_reviews=final['claim_reviews'], reference_bindings=final['reference_bindings'],
            review_steps=[{**s, 'id': digest([key, i, s]), 'status': 'proposed', 'execution_authorized': False}
                          for i, s in enumerate(final['review_steps'])],
            unanswered=final['unanswered'], reference_manifest=refs, reconciliation={'result': final, 'groups': [group]},
            coverage={**parent['coverage'], 'reconciliation_groups': 1, 'cross_group_relations_assessed': True,
                      'all_source_claims_accounted': True, 'source_meaning_revalidated': False, 'source_coverage_upgraded': False},
            claim_scope={'input_claims': len(job['finding_ids']), 'cited_excerpts': len(job['excerpt_pool']),
                         'original_claims_unchanged': True, 'historical_review_questions_submitted': False,
                         'complete_source_units_submitted': False},
            usage=[p['usage'] for p in parent['passes']] + [usage],
            token_check={'input_tokens': input_token_total([p['token_check'] for p in parent['passes']] + [tokens])},
            new_paid_api_calls=int(not usage.get('cache_hit', False)), new_token_count_calls=token_count_calls(tokens),
            cache_hit=False, clinical_execution_authorized=False,
            limits=parent['limits'] + ['Explicit references verify supplied source identities, not clinical meaning. Claim/excerpt review does not reassess full units or resolve original unassessed source content.'])
        saved['result_digest'] = digest(saved)
        fresh(); P.atomic_write(target, saved)
        progress.update(status='complete', new_paid_api_calls=saved['new_paid_api_calls']); P.atomic_write(progress_path, progress)
        return saved
    except Exception as exc:
        progress.update(status='incomplete', error=str(exc)); P.atomic_write(progress_path, progress); raise


def context_route(path, context_id):
    """Explicit current route; no edits or runtime patches to historical engines."""
    from . import availability_reconciliation as old, availability_source_stage, meaning_source_stage
    from .meaning_source_dispatch import pinned
    if context_id in pinned(path): return None
    stage, reconciler = S.stages(path, context_id)
    if stage not in (availability_source_stage, meaning_source_stage) or reconciler is not old: return None
    row, bundle, links, packets, parent_id = stage.prepare(path, context_id)
    if stage.exists(path, parent_id):
        parent = stage.inspect(path, parent_id)
        if len(parent['passes']) > 1:
            try: previous = old.preview(path, parent_id)
            except ValueError as exc:
                if 'reconciliation request bound' not in str(exc): raise
            else:
                if previous['saved_answer_available']: return None
                progress = previous.get('saved_progress')
                if progress and progress.get('status') == 'pending':
                    raise ValueError('Historical reconciliation is pending; no parallel replacement')
    return stage, row, bundle, links, packets, parent_id


def preview_context(path, context_id):
    route = context_route(path, context_id)
    if route is None: return S.preview(path, context_id)
    stage, row, bundle, links, packets, parent_id = route
    completed=S.saved_source_stage(path,context_id) if stage.VERSION=='meaning-source-stage/1' else None
    parent=completed['answer'] if completed else None
    if parent is not None:parent_id=parent['id']
    saved_parent = parent is not None or stage.exists(path, parent_id)
    review = preview(path, parent_id) if saved_parent and len(packets) > 1 else None
    count = (0 if saved_parent else len(packets)) + int(len(packets) > 1 and not (review or {}).get('saved_answer_available'))
    return {'schema': VERSION, 'context_id': context_id, 'patient_id': row['patient_id'],
        'source_units': bundle['counts']['source_units'], 'source_passes': len(packets),
        'reused_source_passes': len(packets) if saved_parent else 0,
        'maximum_inference_calls': count, 'reservation_ceiling_usd': .5 * count,
        'request_bytes': [P.request_bytes(body) for body, _ in packets],
        'dependencies': links, 'record_scope': row.get('record_scope'), 'new_paid_api_calls': 0,
        'stable_reconciliation': review, 'reference_mode': 'explicit_typed_reference_fields',
        'scope': 'Full declared source-unit passes, then bounded saved-claim/excerpt review with explicit references. Unassessed source content and retrieval limits remain.',
        'reconciliation_preflight': 'measured' if review else 'Claim-dependent size is checked after source passes, before reconciliation dispatch.'}


def synthesize_context(path, context_id, ledger, *, allow_live=False):
    route = context_route(path, context_id)
    if route is None: return S.synthesize(path, context_id, ledger, allow_live=allow_live)
    stage = route[0]
    completed=S.saved_source_stage(path,context_id) if stage.VERSION=='meaning-source-stage/1' else None
    parent=({**completed['answer'],'new_paid_api_calls':0,'new_token_count_calls':0,'cache_hit':True}
            if completed and completed['answer'] is not None else
            stage.synthesize(path, context_id, ledger, allow_live=allow_live))
    if len(parent['passes']) == 1: return parent
    result = synthesize(path, parent['id'], ledger, allow_live=allow_live)
    return {**result, 'new_paid_api_calls': parent['new_paid_api_calls'] + result['new_paid_api_calls'],
        'new_token_count_calls': parent['new_token_count_calls'] + result['new_token_count_calls'],
        'cache_hit': parent['cache_hit'] and result['cache_hit']}


def progress_context(path, context_id):
    route = context_route(path, context_id)
    if route is None: return S.progress(path, context_id)
    completed=S.saved_source_stage(path,context_id) if route[0].VERSION=='meaning-source-stage/1' else None
    stage=completed['answer'] if completed and completed['answer'] is not None else route[0].progress(path, context_id)
    if stage['status'] != 'complete' or len(stage['passes']) == 1: return stage
    planned = preview(path, stage['id'])
    target = location(path, planned['answer_id']).with_name(planned['answer_id'] + '-progress.json')
    return {'source_stage': stage, 'stable_reconciliation': planned,
        'reconciliation_progress': json.loads(target.read_text()) if target.exists() else None,
        'status': 'complete' if planned['saved_answer_available'] else 'source_passes_complete_reconciliation_pending'}
