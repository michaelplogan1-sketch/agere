"""Reconcile saved source passes using persistent pass-qualified unit references.

Old answers and source-pass calls are reused unchanged. Only new excerpt
reconciliation requests are dispatched; no clinical authority is conferred.
"""
from .case_synthesis import input_token_total, token_count_calls
from pathlib import Path
import hashlib
import json
import re
from ...contracts import digest
from ..model_policy import claude_model
from . import case_passes as passes, case_synthesis as single

VERSION = 'stable-reference-reconciliation/1'
QUALIFIED = re.compile(r'(?<![\w:])p[0-9]+:u[0-9]+(?![\w:])', re.I)
BARE = re.compile(r'(?<![\w:])u[0-9]+(?![\w:])', re.I)
INSTRUCTIONS = ''' Every reference you write to a supplied unit, including in
finding prose, unresolved items, unanswered questions and review questions, MUST
use its exact pass-qualified name such as p2:u6. Never emit bare u6 or invent a
unit name. Each name has one stable source-unit identity across all groups.
Original source-pass prose is retained verbatim and may use local aliases; use
that pass's alias table to interpret local references. An explicit reference to
another pass or ambiguous old prose is not resolved by its surrounding pass.
Keep uncertain referents unresolved. Do not rewrite them as established facts.
Proposed review questions must be at most 500 characters. Include pass-qualified
unit references only when the question actually concerns those supplied units.
Source presence is not proof of meaning, coverage, consent or completed care.'''


def identity():
    return digest([VERSION, passes.code_identity(), hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])


def location(path, key):
    return single.receipt_path(path, key).parent.parent/'stable-reconciliation'/(key+'.json')


def exists(path, key): return location(path, key).exists()


def manifest(answer, row):
    by_id = {u['id']: u for u in row['model_context']['evidence']}; result = {}
    seen = set()
    for packet in answer['passes']:
        aliases = set()
        for review in packet['result']['unit_reviews']:
            alias = review['unit']; uid = review['source_unit_id']
            if not re.fullmatch(r'u[1-9][0-9]*', alias) or alias in aliases or uid in seen or uid not in by_id:
                raise ValueError('Saved source-pass unit manifest is inconsistent')
            unit = by_id[uid]; name = 'p'+str(packet['pass_number'])+':'+alias
            if name in result: raise ValueError('Duplicate pass-qualified reference')
            result[name] = {'name': name, 'source_pass': packet['pass_number'], 'local_alias': alias,
                'source_unit_id': uid, 'record_id': unit['source_ref'], 'field': unit['field'], 'kind': unit['kind']}
            aliases.add(alias); seen.add(uid)
    if seen != set(by_id): raise ValueError('Every original context unit needs exactly one pass-qualified reference')
    return result


def request(row, source_passes, links, reference_map):
    # Use the established lossless excerpt/source factoring, then replace every
    # structured reconciliation alias. Original model prose remains unmodified.
    body, old_units, old_excerpts = passes.reconciliation_request(row, source_passes, links)
    body['model'] = claude_model(body['model'])
    by_id = {v['source_unit_id']: name for name, v in reference_map.items()}
    rename = {name: by_id[v['unit']['id']] for name, v in old_units.items()}
    units = {rename[k]: value for k, value in old_units.items()}
    excerpts = {rename[k]: {**value, 'unit': rename[k]} for k, value in old_excerpts.items()}
    wire = json.loads(body['messages'][0]['content'])
    for row_value in wire['original_source_excerpts']['rows']: row_value[0] = rename[row_value[0]]
    for row_value in wire['pass_findings']['rows']:
        row_value[4] = [[rename[c[0]], c[1]] for c in row_value[4]]
    for pending in wire['pass_coverage_and_open_questions']:
        for review in pending['unit_reviews']['rows']: review[0] = rename[review[0]]
        pending['prose_reference_scope'] = 'Original pass '+str(pending['pass'])+'; ambiguous or cross-pass prose remains unresolved'
    selected = [reference_map[name] for name in units]
    wire['unit_reference_manifest'] = {'columns': ['name','source_unit_id','record_id','field','source_pass','local_alias'],
        'rows': [[r[k] for k in ['name','source_unit_id','record_id','field','source_pass','local_alias']] for r in selected]}
    wire['schema'] = VERSION
    body['system'] += INSTRUCTIONS
    body['messages'][0]['content'] = passes.context.encode(wire).decode()
    if passes.request_bytes(body) > passes.MAX_REQUEST_BYTES:
        raise ValueError('Stable reconciliation request bound exceeded')
    return body, units, excerpts


def jobs(row, source_passes, links, reference_map, maximum=8):
    def build(ids):
        packet = row['model_context']; subrow = {**row, 'model_context': {**packet,
            'sources': {k:v for k,v in packet['sources'].items() if k in ids},
            'evidence': [u for u in packet['evidence'] if u['source_ref'] in ids]}}
        unit_ids = {u['id'] for u in subrow['model_context']['evidence']}; selected = []
        for p in source_passes:
            value = p['result']; findings = [f for f in value['findings'] if any(c['source']['record_id'] in ids for c in f['citations'])]
            fids = {f['id'] for f in findings}; reviews = [r for r in value['unit_reviews'] if r['source_unit_id'] in unit_ids]
            if reviews:
                selected.append({**p, 'result': {**value, 'findings': findings, 'unit_reviews': reviews,
                    'review_steps': [s for s in value['review_steps'] if set(s['finding_ids']) & fids]}})
        local_links = {**links, 'edges': [e for e in links['edges'] if e['from'] in ids or e['to'] in ids],
            'record_passes': {rid:v for rid,v in links['record_passes'].items() if rid in ids},
            'unresolved_references': [v for v in links['unresolved_references'] if v['source_record'] in ids]}
        body, units, excerpts = request(subrow, selected, local_links, reference_map)
        return {'body':body, 'units':units, 'excerpts':excerpts, 'row':subrow, 'source_records':sorted(ids),
            'finding_ids':[f['id'] for p in selected for f in p['result']['findings']]}
    result = []; pending = set()
    for component in passes.source_components(row, links, source_passes):
        try: build(pending | component)
        except ValueError as exc:
            if 'reconciliation request bound' not in str(exc) or not pending: raise
            result.append(build(pending)); pending = set(component); build(pending)
        else: pending |= component
    if pending: result.append(build(pending))
    if len(result) > maximum: raise ValueError('Stable reconciliation exceeds explicit request count bound')
    assigned = [f for j in result for f in j['finding_ids']]
    if len(assigned) != len(set(assigned)) or set(assigned) != {f['id'] for p in source_passes for f in p['result']['findings']}:
        raise ValueError('Every original finding must be retained in exactly one reconciliation group')
    return result


def parse(raw, job, reference_map):
    result = passes.parse_reconciliation(raw, job['body'], job['units'], job['row'], job['excerpts'])
    texts = result['unanswered']+[t for f in result['findings'] for t in [f['text'], *f['unresolved']]]+[s['question'] for s in result['review_steps']]
    for text in texts:
        if BARE.search(text): raise ValueError('Bare unit reference is ambiguous across source passes')
        if any(name not in job['units'] for name in QUALIFIED.findall(text)):
            raise ValueError('Pass-qualified reference was not supplied in this reconciliation group')
    for step in result['review_steps']:
        single.bounded_text(step['question'], 500)
        names = sorted(set(QUALIFIED.findall(step['question'])))
        step['model_question'] = step['question']
        step['evidence_references'] = [reference_map[name] for name in names]
        # This explicit, labeled annotation lets the existing exact-record needs
        # engine carry references into assessment/work without changing its v1 ID.
        records = sorted({r['record_id'] for r in step['evidence_references']})
        if records: step['question'] += ' [Exact source records from verified unit references: '+', '.join(records)+'.]'
        single.bounded_text(step['question'], 1500)
        step['reference_binding_authority'] = 'exact_transport_identity_not_clinical_resolution'
    return result


def prepare(path, answer_id):
    from . import source_synthesis
    answer = source_synthesis.inspect(path, answer_id)
    if answer.get('schema') == VERSION: raise ValueError('This answer already uses stable reconciliation references')
    if len(answer.get('passes', [])) < 2: raise ValueError('Saved multi-pass interpretation required')
    row = source_synthesis.context(path, answer['context_id']); refs = manifest(answer, row)
    planned = jobs(row, answer['passes'], answer['dependencies'], refs)
    key = digest([VERSION, identity(), answer['id'], digest(answer), digest(row), refs])
    return answer, row, refs, planned, key


def preview(path, answer_id):
    answer, row, refs, planned, key = prepare(path, answer_id)
    progress = location(path,key).with_name(key+'-progress.json')
    return {'schema':VERSION, 'parent_answer_id':answer_id, 'answer_id':key, 'patient_id':answer['patient_id'],
        'reused_source_passes':len(answer['passes']), 'source_units':len(refs), 'reference_manifest':refs,
        'reconciliation_groups':len(planned), 'maximum_inference_calls':len(planned),
        'request_bytes':[passes.request_bytes(j['body']) for j in planned], 'saved_answer_available':exists(path,key),
        'saved_progress':json.loads(progress.read_text()) if progress.exists() else None,
        'prior_unanswered_prose':'Preserved in parent answer; bare or mixed-pass references remain unbound until explicitly supported',
        'clinical_meaning_validated':False, 'new_paid_api_calls':0}


def inspect(path, key):
    saved = json.loads(location(path,key).read_text())
    if saved['id'] != key or saved['code_identity'] != identity() or saved['status'] != 'complete': raise ValueError('Stable reconciliation identity changed')
    if saved.get('result_digest') != digest({k:v for k,v in saved.items() if k!='result_digest'}): raise ValueError('Stable reconciliation result integrity changed')
    _, row, _, _, expected = prepare(path, saved['parent_answer_id'])
    if expected != key or digest(row) != saved['context_digest']: raise ValueError('Stable reconciliation source/parent changed')
    return saved


def synthesize(path, answer_id, ledger, *, allow_live=False):
    from . import source_synthesis
    parent, row, refs, planned, key = prepare(path, answer_id); target = location(path,key)
    if target.exists(): return {**inspect(path,key),'cache_hit':True,'new_paid_api_calls':0,'new_token_count_calls':0}
    if not allow_live: raise ValueError('Live stable reconciliation disabled; no saved result')
    def request_key(body): return digest({'contract':'context-student/1','provider':'claude','body':body})
    def cached(body):
        old = ledger.db.execute('SELECT status FROM calls WHERE id=?',(request_key(body),)).fetchone()
        if old and old[0] != 'validated': raise ValueError('Prior reconciliation pending/failed; no silent paid retry')
        return bool(old)
    pending = sum(not cached(j['body']) for j in planned); budget = ledger.receipt()['remaining_reservation_usd']
    if budget is not None and budget < .5*pending: raise ValueError('Authorized ledger cannot cover planned reconciliation')
    def fresh():
        if digest(source_synthesis.inspect(path,answer_id)) != digest(parent) or digest(source_synthesis.context(path,parent['context_id'])) != digest(row):
            raise ValueError('Source or saved interpretation changed during reconciliation')
    out=target.parent/'provider';out.mkdir(parents=True,exist_ok=True)
    progress={'schema':VERSION,'id':key,'parent_answer_id':answer_id,'status':'pending','groups':[],'new_paid_api_calls':0,'new_token_count_calls':0}
    progress_target=target.with_name(key+'-progress.json')
    try:
        for i, job in enumerate(planned,1):
            body=job['body'];fresh();cached(body);tokens=single.token_check(body,out,allow_live=allow_live)
            progress['new_token_count_calls']+=token_count_calls(tokens);fresh()
            result,usage=single.send(body,'claude',ledger,out,parse=lambda raw:parse(raw,job,refs))
            progress['new_paid_api_calls']+=int(not usage.get('cache_hit',False))
            if usage.get('cache_hit'):
                from ..economics import usage_cost
                raw=json.loads(ledger.db.execute('SELECT response FROM calls WHERE id=?',(request_key(body),)).fetchone()[0])
                usage={**usage,'usage':raw.get('usage'),'cost_estimate':usage_cost(raw['model'],raw.get('usage'))}
            fresh();progress['groups'].append({'result':passes._rename(result,'r'+str(i)+'_'),'source_records':job['source_records'],
                'input_finding_ids':job['finding_ids'],'usage':usage,'token_check':tokens,'request_bytes':passes.request_bytes(body)})
            passes.atomic_write(progress_target,progress)
        groups=progress['groups'];source_passes=parent['passes'];stages=source_passes+groups
        final={k:[v for g in groups for v in g['result'][k]] for k in ('findings','unanswered','review_steps')}
        if len(groups)>1:final['unanswered'].append('Relations between reconciliation groups remain unassessed; documentary grouping does not establish clinical independence.')
        saved={**parent,'schema':VERSION,'id':key,'parent_answer_id':answer_id,'parent_answer_hash':digest(parent),
            'code_identity':identity(),'context_digest':digest(row),'status':'complete','reference_manifest':refs,
            'findings':[f for p in stages for f in p['result']['findings']],
            'review_steps':[{**s,'id':digest([key,i,s]),'status':'proposed','execution_authorized':False}
                            for i,s in enumerate(s for p in stages for s in p['result']['review_steps'])],
            'unanswered':list(dict.fromkeys([u for p in source_passes for u in p['result']['unanswered']]+final['unanswered'])),
            'reconciliation':{'result':final,'groups':groups},'usage':[p['usage'] for p in stages],
            'token_check':{'input_tokens':input_token_total(p['token_check'] for p in stages)},
            'coverage':{**parent['coverage'],'reconciliation_groups':len(groups),'cross_group_relations_assessed':len(groups)<=1},
            'new_paid_api_calls':progress['new_paid_api_calls'],'new_token_count_calls':progress['new_token_count_calls'],'cache_hit':False,
            'reused_source_passes':len(source_passes),'limits':parent['limits']+['Pass-qualified references verify transport identity, not semantic or clinical correctness. Previous reconciliation remains in the immutable parent answer.']}
        saved['result_digest']=digest(saved)
        fresh();passes.atomic_write(target,saved);progress['status']='complete';passes.atomic_write(progress_target,progress)
        return saved
    except Exception as exc:
        progress.update(status='incomplete',error=str(exc));passes.atomic_write(progress_target,progress);raise
