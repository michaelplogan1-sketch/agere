"""Immutable, cohort-scoped evidence-review proposals, with stale-source checks."""
from collections import Counter
import hashlib,json,re,sqlite3,zlib
from pathlib import Path
from ...contracts import digest
from . import evidence_requirements as requirements,evidence_resolution as resolution

VERSION='evidence-acquisition-queue/1'


def identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),resolution.identity()])


def storage(path):
    db=sqlite3.connect(Path(path)/'evidence-acquisition.sqlite3',timeout=30)
    db.execute('CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY,payload BLOB NOT NULL)')
    return db


def prepare(path,contracts,patients,*,selection=None):
    if not isinstance(contracts,list) or not 1<=len(contracts)<=8 or any(not isinstance(c,str) for c in contracts) or len(set(contracts))!=len(contracts):
        raise ValueError('One to eight distinct registered documentary contracts required')
    if not isinstance(patients,list) or any(not isinstance(p,str) for p in patients):raise ValueError('Explicit patient membership required')
    patients=sorted(set(patients));contracts=sorted(contracts);references=[];requests={};counts=Counter();proofs=[]
    meta=json.loads((Path(path)/'run.json').read_text())
    if meta.get('synthetic') is not True:raise ValueError('Synthetic source required')
    db=sqlite3.connect((Path(path)/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
    try:known={x[0] for x in db.execute('SELECT id FROM patients')}
    finally:db.close()
    if set(patients)-known:raise ValueError('Patient absent from current source roster')
    prepared={c:requirements.snapshot(path,{'contract':c,'filter':'all'}) for c in contracts}
    code=requirements.code_identity();interpretation_code=requirements.interpretation.code_identity()
    for contract in contracts:
        _,_,summary=requirements.execute(path,patients,{'contract':contract,'filter':'all'},prepared=prepared[contract],preview_limit=0)
        counts.update(summary['processing_counts']);db=requirements.storage(path)
        try:
            for pid in patients:
                key=requirements.receipt_id(prepared[contract],pid,code=code,interpretation_code=interpretation_code)
                raw=db.execute('SELECT payload FROM receipts WHERE id=? AND patient=?',(key,pid)).fetchone()
                if raw is None:raise ValueError('Evaluated source receipt missing')
                row=json.loads(zlib.decompress(raw[0]));references.append({'contract':contract,'patient_id':pid,'receipt_id':key})
                if not row['acquisition']:continue
                projected=resolution.project(row)
                proofs.append({k:projected[k] for k in ('patient_id','requirement_receipt_id','contract_id','resolution')})
                for request in projected['requests']:
                    item=requests.setdefault(request['id'],{k:v for k,v in request.items() if k not in ('requirement_receipt_id','bindings','binding_impacts','can_resolve_contract_alone','required_to_resolve_contract')})
                    item.setdefault('requirements',[]).append({'contract_id':contract,'receipt_id':key,'binding_impacts':request['binding_impacts'],
                        'can_resolve_contract_alone':request['can_resolve_contract_alone'],'required_to_resolve_contract':request['required_to_resolve_contract']})
        finally:db.close()
    # Fail if a source or review changed while preparing, rather than silently
    # mixing source versions from the separate contract passes.
    for contract,snap in prepared.items():
        if requirements.snapshot(path,{'contract':contract,'filter':'all'})['identity']!=snap['identity']:
            raise ValueError('Source interpretation changed while preparing acquisition work; rerun')
    items=list(requests.values())
    for item in items:
        item['can_resolve_any_contract_alone']=any(x['can_resolve_contract_alone'] for x in item['requirements'])
    items.sort(key=lambda x:(not x['can_resolve_any_contract_alone'],x['patient_id'],x['kind'],x['id']))
    key=digest([identity(),meta['snapshot'],references,selection])
    result={'schema':VERSION,'id':key,'synthetic':True,'code_identity':identity(),'source_snapshot':meta['snapshot'],
        'contracts':contracts,'patients_evaluated':len(patients),'population_digest':digest(patients),'selection':selection,
        'references':references,'items':items,'conditional_resolution':proofs,
        'counts':{'requests':len(items),'patients_with_requests':len({x['patient_id'] for x in items}),
                  'request_kinds':dict(Counter(x['kind'] for x in items)),
                  'requests_that_can_resolve_a_contract_alone':sum(x['can_resolve_any_contract_alone'] for x in items),
                  'patient_contracts_evaluated':len(references)},
        'new_paid_api_calls':0,'requests_sent':0,'clinical_execution_authorized':False,
        'limits':['Internal evidence acquisition proposals, not a care-gap or outreach list.',
                  'Queue membership covers only the explicitly selected population; unresolved cohort membership remains outside this set.',
                  'Conditional logic is not a probability, cost estimate or clinical priority.',
                  'Refreshing reuses unchanged patient receipts; prior proposals remain immutable and may become stale.']}
    db=storage(path)
    try:
        old=db.execute('SELECT payload FROM proposals WHERE id=?',(key,)).fetchone()
        if old and json.loads(zlib.decompress(old[0]))!=result:raise ValueError('Acquisition proposal identity collision')
        if not old:
            with db:db.execute('INSERT INTO proposals VALUES (?,?)',(key,zlib.compress(json.dumps(result,allow_nan=False).encode())))
    finally:db.close()
    return {**summary_view(result),'cache_hit':old is not None,'requirement_processing_counts':dict(counts)}


def summary_view(row):
    return {k:v for k,v in row.items() if k not in ('references','items','conditional_resolution')}


def load(path,key,*,verify=True):
    if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{64}',key):raise ValueError('Exact acquisition proposal ID required')
    db=storage(path)
    try:raw=db.execute('SELECT payload FROM proposals WHERE id=?',(key,)).fetchone()
    finally:db.close()
    if raw is None:raise ValueError('Unknown acquisition proposal')
    row=json.loads(zlib.decompress(raw[0]))
    if row['code_identity']!=identity():raise ValueError('Acquisition logic changed; prepare a new proposal')
    if verify:
        code=requirements.code_identity();interpretation_code=requirements.interpretation.code_identity()
        prepared={c:requirements.snapshot(path,{'contract':c,'filter':'all'}) for c in row['contracts']}
        for ref in row['references']:
            key=requirements.receipt_id(prepared[ref['contract']],ref['patient_id'],code=code,interpretation_code=interpretation_code)
            if key!=ref['receipt_id']:raise ValueError('Acquisition source or review changed; refresh this population proposal')
    return row


def list_items(path,key,*,offset=0,limit=20):
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=50:raise ValueError('Bounded acquisition page required')
    row=load(path,key)
    return {**summary_view(row),'offset':offset,'items':row['items'][offset:offset+limit],
            'next_offset':offset+limit if offset+limit<len(row['items']) else None}


def inspect_item(path,key,item_id):
    row=load(path,key);item=next((x for x in row['items'] if x['id']==item_id),None)
    if item is None:raise ValueError('Unknown acquisition item')
    # Deep inspection rechecks exact received source hashes and linked records.
    evidence=[requirements.inspect(path,x['receipt_id']) for x in item['requirements']]
    return {'schema':VERSION,'synthetic':True,'proposal_id':row['id'],'item':item,'requirements':evidence,
        'conditional_resolution':[p for p in row['conditional_resolution'] if p['requirement_receipt_id'] in {x['receipt_id'] for x in item['requirements']}],
        'requests_sent':0,'clinical_execution_authorized':False}
