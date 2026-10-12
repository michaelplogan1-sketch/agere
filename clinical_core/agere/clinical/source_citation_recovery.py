"""One explicit correction for a diagnosed noncontiguous structured quotation.

Same evidence, model, output allowance and original strict parser. Never repair
model text locally or retry an original/failed corrected key. Existing output
recovery remains byte-for-byte unchanged and owns its historical receipts.
"""
import copy
import hashlib
import json
import sys
from pathlib import Path
from ...contracts import digest
from . import source_output_recovery as O

VERSION = 'source-citation-recovery/1'
ERROR = 'Citation is absent or ambiguous in its exact source unit'
INSTRUCTION = (' Every citation.quote must be a single contiguous, unique substring of its unit text. '
               'Never join nonadjacent JSON fields into one quote. Use separate citations for separate '
               'fields, or include intervening text within the existing 400-character quote bound.')


def identity():
    return digest([VERSION, O.identity(), hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])


def location(path, key):
    return O.location(path, key).parent.parent/'source-citation-recovery'/(key+'.json')


def exists(path, key):
    return location(path, key).exists()


def diagnose(body, units, row, failure, raw):
    if (failure.get('status') != 'failed' or failure.get('request_hash') != O.request_key(body)
            or failure.get('error') != 'ValueError: '+ERROR
            or raw.get('model') != body['model'] or raw.get('stop_reason') != 'end_turn'):
        raise ValueError('No eligible completed response with a citation failure')
    try:
        O.parsed(raw, body, units, row)
    except ValueError as exc:
        if str(exc) != ERROR:
            raise ValueError('Failure no longer reproduces as an exact citation error') from exc
    else:
        raise ValueError('Saved response already validates; no new request justified')
    value = json.loads(''.join(c['text'] for c in raw['content'] if c.get('type') == 'text'))
    issues = []
    for finding in value['findings']:
        for index, citation in enumerate(finding['citations']):
            text = units[citation['unit']]['text']; quote = citation['quote']
            if text.count(quote) == 1:
                continue
            # Qualify only the observed structural error: individually exact JSON
            # fields concatenated across intervening content. No semantic repair.
            fields = json.loads('{'+quote+'}')
            fragments = [json.dumps(k)+':'+json.dumps(v, separators=(',', ':'), ensure_ascii=False)
                         for k, v in fields.items()]
            if len(fields) < 2 or quote in text or any(text.count(f) != 1 for f in fragments):
                raise ValueError('Citation error is not supported by exact nonadjacent fields')
            issues.append({'finding_id':finding['id'], 'citation_index':index,
                           'unit':citation['unit'], 'field_names':list(fields),
                           'diagnosis':'noncontiguous_exact_structured_fields'})
    if not issues:
        raise ValueError('No diagnosed noncontiguous quotation')
    corrected = copy.deepcopy(body)
    corrected['system'] += INSTRUCTION
    if O.passes.request_bytes(corrected) > 20000:
        raise ValueError('Citation correction exceeds existing request bound')
    return corrected, issues


def prepare(path, context_id):
    row, bundle, links, packets, original_id = O.M.prepare(path, context_id)
    source_out = O.M.location(path, original_id).parent/'provider'
    failures, corrected = [], []
    for number, (body, units) in enumerate(packets, 1):
        key = O.request_key(body); target = source_out/(key+'-failure.json')
        if target.exists():
            failure = json.loads(target.read_text())
            raw = json.loads((source_out/(key+'.json')).read_text())['response']
            revised, issues = diagnose(body, units, row, failure, raw)
            failures.append({'pass_number':number, 'failed_request_hash':key,
                'corrected_request_hash':O.request_key(revised), 'failure_digest':digest(failure),
                'raw_response_digest':digest(raw), 'diagnosis':issues})
            corrected.append((revised, units))
        else:
            corrected.append((body, units))
    if not failures:
        raise ValueError('No saved source citation failure to recover')
    binding = {'original_stage_id':original_id, 'context_id':context_id,
        'context_digest':digest(row), 'dependencies':links, 'failures':failures}
    return row, bundle, links, corrected, digest([VERSION, identity(), binding]), binding, source_out


def preview(path, request):
    if not isinstance(request, dict) or set(request) != {'context_id'}:
        raise ValueError('Exact source context required')
    from . import source_synthesis as S
    if S.stages(path, request['context_id'])[0] is not O.M:
        raise ValueError('Historical source engines retain their original recovery contracts')
    row, bundle, _, packets, key, binding, _ = prepare(path, request['context_id'])
    return {'schema':VERSION, 'id':key, **binding, 'patient_id':row['patient_id'],
        'source_units':len(bundle['unit_manifest']), 'source_passes':len(packets),
        'request_bytes':[O.passes.request_bytes(b) for b, _ in packets],
        'saved_answer_available':exists(path,key), 'new_paid_api_calls':0,
        'failed_requests_resent':0, 'clinical_validated':False,
        'scope':'Request contiguous exact quotations for the diagnosed failed pass. Same evidence, model and strict parser; preserve failed requests. Cross-pass relationships remain unassessed.'}


def assemble(*args):
    saved = O.assemble(*args)
    saved.update(schema=VERSION, code_identity=identity(),
                 limits=O.single.LIMITS+['Explicit citation correction; original failure preserved. Cross-pass reconciliation remains separate.'])
    saved.pop('source_stage_digest')
    saved['source_stage_digest'] = digest(saved)
    return saved


def inspect(path, key):
    saved = json.loads(location(path,key).read_text())
    if (saved.get('id') != key or saved.get('code_identity') != identity() or saved.get('status') != 'complete'
            or saved.get('source_stage_digest') != digest({k:v for k,v in saved.items() if k != 'source_stage_digest'})):
        raise ValueError('Citation recovery receipt integrity changed')
    row,bundle,links,packets,expected,binding,source_out = prepare(path,saved['context_id'])
    if expected != key or len(saved['passes']) != len(packets):
        raise ValueError('Citation recovery dependencies or pass coverage changed')
    for packet,stage,(body,units) in zip(bundle['passes'],saved['passes'],packets):
        request = O.request_key(body)
        origin = source_out if stage['reused_original_request'] else location(path,key).parent/'provider'
        raw = json.loads((origin/(request+'.json')).read_text())['response']
        expected_result = O.passes._rename(O.parsed(raw,body,units,row),'p'+str(packet['pass_number'])+'_')
        if (stage['pass_number'] != packet['pass_number']
                or stage['source_units'] != [u['id'] for u in packet['evidence']]
                or stage['usage']['request_hash'] != request or stage['result'] != expected_result):
            raise ValueError('Citation recovery differs from original-parser validated output')
    counts = {k:saved[k] for k in ('new_paid_api_calls','new_token_count_calls','reused_source_passes')}
    if assemble(row,bundle,links,packets,key,binding,saved['passes'],counts) != saved:
        raise ValueError('Recovered answer no longer matches its exact passes')
    return saved


def saved_for_context(path, context_id):
    try:
        key = prepare(path,context_id)[4]
    except (ValueError,FileNotFoundError):
        return None
    return inspect(path,key) if exists(path,key) else None


def run(path, request, ledger, *, allow_live=False):
    if not isinstance(request,dict) or set(request) != {'context_id','recovery_id'}:
        raise ValueError('Exact context and inspected recovery identity required')
    plan = preview(path,{'context_id':request['context_id']})
    if request['recovery_id'] != plan['id']:
        raise ValueError('Recovery plan changed; inspect exact current failure')
    row,bundle,links,packets,key,binding,source_out = prepare(path,request['context_id'])
    if exists(path,key):
        return {**inspect(path,key),'cache_hit':True,'new_paid_api_calls':0,'new_token_count_calls':0}
    if not allow_live:
        raise ValueError('Live citation recovery disabled')
    for failure in binding['failures']:
        old = ledger.db.execute('SELECT status FROM calls WHERE id=?',(failure['failed_request_hash'],)).fetchone()
        if not old or old[0] != 'failed':
            raise ValueError('Original request must remain failed in the shared ledger')
    def available(body):
        old = ledger.db.execute('SELECT status,response FROM calls WHERE id=?',(O.request_key(body),)).fetchone()
        if old and old[0] != 'validated':
            raise ValueError('Corrected request pending/failed; no silent retry')
        return old
    for body,_ in packets:
        available(body)
    def fresh():
        if prepare(path,request['context_id'])[4] != key:
            raise ValueError('Recovery source or failure inputs changed')
    out = location(path,key).parent/'provider';out.mkdir(parents=True,exist_ok=True)
    progress = location(path,key).with_name(key+'-progress.json')
    state = {'id':key,'context_id':request['context_id'],'status':'pending','passes':[]}
    counts = {'new_paid_api_calls':0,'new_token_count_calls':0,'reused_source_passes':0}
    originals = O.M.prepare(path,request['context_id'])[3]
    try:
        for packet,(body,units),(original,_) in zip(bundle['passes'],packets,originals):
            fresh(); old = available(body)
            reused = bool(old and body == original and (source_out/(O.request_key(body)+'.json')).exists())
            origin = source_out if reused else out
            tokens = O.single.token_check(body,origin,allow_live=False if reused else allow_live)
            counts['new_token_count_calls'] += int(not tokens['provider_count_cached'])
            fresh()
            result,usage = O.single.send(body,'claude',ledger,origin,parse=lambda raw:O.parsed(raw,body,units,row))
            counts['new_paid_api_calls'] += int(not usage.get('cache_hit',False))
            counts['reused_source_passes'] += int(reused)
            if usage.get('cache_hit'):
                from ..economics import usage_cost
                raw = json.loads(old[1]);usage = {**usage,'usage':raw.get('usage'),'cost_estimate':usage_cost(raw['model'],raw.get('usage'))}
            fresh();wire = json.loads(body['messages'][0]['content'])
            state['passes'].append({'pass_number':packet['pass_number'],'source_units':[u['id'] for u in packet['evidence']],
                'result':O.passes._rename(result,'p'+str(packet['pass_number'])+'_'),
                'canonical_identity':wire['canonical_identity'],'source_availability':wire['received_source_availability'],
                'usage':usage,'token_check':tokens,'request_bytes':O.passes.request_bytes(body),'reused_original_request':reused})
            O.passes.atomic_write(progress,{**state,**counts})
        saved = assemble(row,bundle,links,packets,key,binding,state['passes'],counts)
        fresh();O.passes.atomic_write(location(path,key),saved)
        state['status'] = 'complete';O.passes.atomic_write(progress,{**state,**counts})
        return inspect(path,key)
    except Exception as exc:
        O.passes.atomic_write(progress,{**state,**counts,'status':'incomplete','error':str(exc)})
        raise


def recovery_engine(path, context_id):
    """Existing explicit recovery surface; never choose a correction by retry."""
    try:
        O.prepare(path,context_id)
    except (ValueError,FileNotFoundError):
        return sys.modules[__name__]
    return O
