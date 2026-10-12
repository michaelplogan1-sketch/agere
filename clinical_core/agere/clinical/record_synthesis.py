"""Record-scoped runner reusing the established pass, citation and gateway contracts.

Category-complete v1 answers keep their original implementation and replay identity.
This runner accepts only the separate, fully verified explicit-record contract.
"""
from collections import Counter
from pathlib import Path
import hashlib
import json
from ...contracts import digest
from . import record_context as context, case_passes as passes, case_synthesis as single

VERSION = 'record-source-synthesis/1'


def code_identity():
    return digest([VERSION, context.identity(), passes.code_identity(), hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])


def location(path, key):
    return single.receipt_path(path, key).parent.parent/'record-synthesis'/(key+'.json')


def exists(path, key): return location(path, key).exists()


def pass_request(packet, links, declaration):
    body, units = passes.pass_request(packet, links)
    wire = json.loads(body['messages'][0]['content']); wire['record_scope'] = declaration
    body['messages'][0]['content'] = passes.context.encode(wire).decode()
    return body, units


def plan(row, links, maximum_passes=8):
    if type(maximum_passes) is not int or not 1 <= maximum_passes <= 16:
        raise ValueError('Explicit source-pass cap must be between 1 and 16')
    packet = row['model_context']; declaration = row['missing_evidence']['record_scope']
    candidates = [{**u, **packet['sources'][u['source_ref']]} for u in packet['evidence']]
    bundle = passes.context_passes.partition(row['patient_id'], {**row['parameters'], 'max_bytes': 20000},
        candidates, {'snapshot': packet['snapshot']}, wire_size=lambda p: passes.request_bytes(pass_request(p, links, declaration)[0]))
    if not bundle['review_ready'] or len(bundle['passes']) > maximum_passes:
        raise ValueError('Complete record scope exceeds the indivisible-unit or source-pass cap; no calls made')
    locations = {}
    for p in bundle['passes']:
        for rid in p['sources']: locations.setdefault(rid, []).append(p['pass_number'])
    return bundle, {**links, 'record_passes': locations, 'cross_pass_edges': [e for e in links['edges']
        if e['scope'] == 'in_scope' and not set(locations[e['from']]) & set(locations[e['to']])]}


def prepare(path, context_id):
    row = context.verified(path, context_id); links = passes.dependencies(path, row)
    bundle, links = plan(row, links)
    key = digest([VERSION, code_identity(), digest(row), digest(links)])
    return row, bundle, links, key


def preview(path, context_id):
    row, bundle, links, _ = prepare(path, context_id)
    n = len(bundle['passes']); reconciliation = 0 if n == 1 else min(8, len(passes.source_components(row, links)))
    return {'schema': VERSION, 'context_id': context_id, 'patient_id': row['patient_id'],
        'source_units': bundle['counts']['source_units'], 'source_passes': n,
        'maximum_inference_calls': n+reconciliation, 'reservation_ceiling_usd': .5*(n+reconciliation),
        'request_bytes': [passes.request_bytes(pass_request(p, links, row['missing_evidence']['record_scope'])[0]) for p in bundle['passes']],
        'record_scope': row['record_scope'], 'dependencies': links, 'new_paid_api_calls': 0,
        'scope': 'All explicitly selected records; other records in the same categories remain excluded. Selection sufficiency is unassessed.'}


def inspect(path, key):
    saved = json.loads(location(path, key).read_text())
    row, _, links, expected = prepare(path, saved['context_id'])
    if key != saved['id'] or expected != key or digest(row) != saved['context_digest'] or saved['status'] != 'complete':
        raise ValueError('Record synthesis source or implementation changed; rebuild required')
    return saved


def progress(path, context_id):
    _, _, _, key = prepare(path, context_id)
    return json.loads(location(path, key).with_name(key+'-progress.json').read_text())


def synthesize(path, context_id, ledger, *, allow_live=False):
    row, bundle, links, key = prepare(path, context_id); target = location(path, key)
    if target.exists(): return {**inspect(path, key), 'cache_hit': True, 'new_paid_api_calls': 0, 'new_token_count_calls': 0}
    if not allow_live: raise ValueError('Live synthesis route disabled; no saved answer for this source version')
    packets = [pass_request(p, links, row['missing_evidence']['record_scope']) for p in bundle['passes']]
    def request_key(body): return digest({'contract': 'context-student/1', 'provider': 'claude', 'body': body})
    def available(body):
        old = ledger.db.execute('SELECT status FROM calls WHERE id=?', (request_key(body),)).fetchone()
        if old and old[0] != 'validated': raise ValueError('Prior synthesis pending/failed; no silent paid retry')
        return bool(old)
    recon_cap = 0 if len(packets) == 1 else min(8, len(passes.source_components(row, links)))
    if ledger.receipt()['remaining_reservation_usd'] < .5*(sum(not available(b) for b, _ in packets)+recon_cap):
        raise ValueError('Shared authorization cannot cover complete record scope; no calls made')
    row_hash = digest(row); out = target.parent/'provider'; out.mkdir(parents=True, exist_ok=True)
    saved_progress = {'schema': VERSION, 'id': key, 'context_id': context_id, 'status': 'pending', 'passes': [],
        'reconciliations': [], 'new_paid_api_calls': 0, 'new_token_count_calls': 0}
    progress_path = target.with_name(key+'-progress.json')
    def fresh():
        if digest(context.verified(path, context_id)) != row_hash: raise ValueError('Source scope changed during interpretation')
    def call(body, parse):
        if passes.request_bytes(body) > 20000: raise ValueError('Provider request exceeds gateway bound')
        available(body); fresh()
        tokens = single.token_check(body, out, allow_live=allow_live)
        saved_progress['new_token_count_calls'] += int(not tokens['provider_count_cached']); fresh()
        result, usage = single.send(body, 'claude', ledger, out, parse=parse)
        saved_progress['new_paid_api_calls'] += int(not usage.get('cache_hit', False))
        if usage.get('cache_hit'):
            from ..economics import usage_cost
            old = ledger.db.execute('SELECT response FROM calls WHERE id=?', (request_key(body),)).fetchone()
            raw = json.loads(old[0]); usage = {**usage, 'usage': raw.get('usage'), 'cost_estimate': usage_cost(raw['model'], raw.get('usage'))}
        fresh(); return result, usage, tokens
    try:
        for packet, (body, units) in zip(bundle['passes'], packets):
            result, usage, tokens = call(body, lambda raw: passes.parse_pass(raw, body, units, row))
            saved_progress['passes'].append({'pass_number': packet['pass_number'], 'source_units': [u['id'] for u in packet['evidence']],
                'result': passes._rename(result, 'p'+str(packet['pass_number'])+'_'), 'usage': usage, 'token_check': tokens, 'request_bytes': passes.request_bytes(body)})
            passes.atomic_write(progress_path, saved_progress)
        jobs = passes.reconciliation_jobs(row, saved_progress['passes'], links) if len(packets)>1 else []
        if ledger.receipt()['remaining_reservation_usd'] < .5*sum(not available(j['body']) for j in jobs):
            raise ValueError('Shared authorization cannot cover remaining reconciliation; progress retained')
        for i, job in enumerate(jobs, 1):
            body = job['body']; result, usage, tokens = call(body, lambda raw: passes.parse_reconciliation(raw, body, job['units'], job['row'], job['excerpts']))
            saved_progress['reconciliations'].append({'result': passes._rename(result, 'r'+str(i)+'_'), 'usage': usage, 'token_check': tokens,
                'source_records': job['source_records'], 'input_finding_ids': job['finding_ids'], 'request_bytes': passes.request_bytes(body)})
            passes.atomic_write(progress_path, saved_progress)
        source_passes = saved_progress['passes']; reconciled = saved_progress['reconciliations']; stages = source_passes+reconciled
        final = {k: [v for p in reconciled for v in p['result'][k]] for k in ('findings','unanswered','review_steps')}
        if len(reconciled)>1: final['unanswered'].append('Relationships between reconciliation groups remain unassessed; explicit links do not establish clinical independence.')
        reviews = [v for p in source_passes for v in p['result']['unit_reviews']]
        if sorted(v['source_unit_id'] for v in reviews) != sorted(bundle['unit_manifest']): raise ValueError('Source unit coverage mismatch')
        steps = [s for p in stages for s in p['result']['review_steps']]
        saved = {'schema': VERSION, 'id': key, 'status': 'complete', 'synthetic': True, 'patient_id': row['patient_id'],
            'question': row['parameters']['question'], 'context_id': context_id, 'context_digest': row_hash, 'code_identity': code_identity(),
            'source_scope': row['parameters']['streams'], 'record_scope': row['record_scope'], 'dependencies': links,
            'passes': source_passes, 'findings': [f for p in stages for f in p['result']['findings']],
            'unanswered': list(dict.fromkeys([u for p in stages for u in p['result']['unanswered']]+final['unanswered'])),
            'review_steps': [{**s,'id':digest([key,i,s]),'status':'proposed','execution_authorized':False} for i,s in enumerate(steps)],
            'reconciliation': {'result': final, 'groups': reconciled},
            'coverage': {'received_units': len(reviews), 'submitted_units': len(reviews), 'model_accounted_units': len(reviews),
                'unit_dispositions': dict(Counter(v['status'] for v in reviews)), 'unassessed_units': [v for v in reviews if v['status']=='unresolved'],
                'complete_declared_scope': True, 'complete_categories': False, 'complete_chart': False,
                'reconciliation_groups': len(reconciled), 'cross_group_relations_assessed': len(reconciled)<=1,
                'interpretation_accuracy_assessed': False, 'retrieval_recall_assessed': False},
            'provider': 'claude', 'model': body['model'], 'token_check': {'input_tokens': sum(p['token_check']['input_tokens'] for p in stages)},
            'usage': [p['usage'] for p in stages], 'new_paid_api_calls': saved_progress['new_paid_api_calls'],
            'new_token_count_calls': saved_progress['new_token_count_calls'], 'cache_hit': False,
            'clinical_state_established': False, 'clinical_execution_authorized': False, 'limits': single.LIMITS+[
                'Scope is explicit records, not all records in their categories. Selection sufficiency is unassessed.',
                'Separate passes and excerpt reconciliation do not establish semantic correctness.']}
        fresh(); passes.atomic_write(target, saved); saved_progress['status']='complete'; passes.atomic_write(progress_path,saved_progress)
        return saved
    except Exception as error:
        saved_progress.update(status='incomplete',error=str(error)); passes.atomic_write(progress_path,saved_progress); raise
