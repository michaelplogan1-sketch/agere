"""Immutable financial source-review queues and explicit program evidence bindings.

No payer contract is manufactured from service billing or care-team labels.
Portable requirement definitions are separate from source context adapters.
"""
from collections import Counter
from pathlib import Path
import hashlib,json,re,sqlite3,zlib
from ...contracts import digest
from . import financial_accounts as F
from .clinical_events import iso

VERSION='program-accountability/1'
LIMITS=[
 'Synthetic received evidence only. These tasks request source review, not patient outreach or a treatment change.',
 'Unbound means no verified input mapping is installed; it does not prove absence from all free text or external systems.',
 'Source context and accounted payments do not establish clinical eligibility, attribution, payer entitlement or realized savings.',
 'Resolving a financial discrepancy does not by itself resolve the independent contract prerequisites.',
]


def definitions():
    data=json.loads(Path(__file__).with_name('accountability_contracts.json').read_text())
    if data['schema']!='program-accountability-contracts/1':raise ValueError('Versioned accountability definitions required')
    result={}
    for spec in data['contracts']:
        if spec['id'] in result or not spec['version'] or not spec['requirements']:raise ValueError('Unique versioned contract required')
        seen=set()
        for r in spec['requirements']:
            if set(r)!={'id','scope','needed','binding'} or r['id'] in seen or r['scope'] not in ('contract','patient'):raise ValueError('Exact unique requirement contract required')
            seen.add(r['id'])
            b=r['binding']
            # Explicit record bindings can be installed with a new definition
            # version. Field presence is input availability, not semantic approval.
            if b is not None and (r['scope']!='patient' or not isinstance(b,dict) or set(b)!={'stream','fields'} or not isinstance(b['stream'],str)
                or not isinstance(b['fields'],list) or not b['fields'] or len(set(b['fields']))!=len(b['fields'])
                or any(not isinstance(f,str) or not f for f in b['fields'])):raise ValueError('Patient input binding requires a stream and distinct exact fields')
        result[spec['id']]={**spec,'hash':digest(spec)}
    return result


def definition(key):
    if not isinstance(key,str) or key not in definitions():raise ValueError('Registered accountability contract required')
    return definitions()[key]


def source_map():
    m=json.loads(Path(__file__).with_name('accountability_source_maps.json').read_text())
    if m['schema']!='accountability-source-map/1':raise ValueError('Registered accountability source adapter required')
    return m


def identity():return digest([F.identity(),*[hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in
    (__file__,Path(__file__).with_name('accountability_contracts.json'),Path(__file__).with_name('accountability_source_maps.json'))]])


def validate(p,asof):
    if not isinstance(p,dict) or set(p)!={'contract','available_by'}:raise ValueError('Exact accountability contract and receipt cutoff required')
    definition(p['contract']);F.validate({'available_by':p['available_by']},asof);return dict(p)


def prepare(path,p):
    f=F.prepare(path,{'available_by':p.get('available_by')});p=validate(p,f['base']['snapshot']['as_of'])
    spec=definition(p['contract']);register_definition(path,spec);base={'code_identity':identity(),'contract':spec,'parameters':p,'financial_base':f['base']}
    return {'base':base,'financial':f,'identity':digest([base,f['identity']])}


def receipt_id(prep,pid):return digest([prep['base'],F.receipt_id(prep['financial'],pid)])


def storage(path):
    db=sqlite3.connect(Path(path)/'program-accountability.sqlite3',timeout=60)
    db.executescript('''CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY,patient TEXT,payload BLOB NOT NULL);
      CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY,payload BLOB NOT NULL);
      CREATE TABLE IF NOT EXISTS definitions(contract TEXT,version TEXT,hash TEXT NOT NULL,PRIMARY KEY(contract,version));
      CREATE TRIGGER IF NOT EXISTS receipt_no_update BEFORE UPDATE ON receipts BEGIN SELECT RAISE(ABORT,'Immutable accountability receipt'); END;
      CREATE TRIGGER IF NOT EXISTS receipt_no_delete BEFORE DELETE ON receipts BEGIN SELECT RAISE(ABORT,'Immutable accountability receipt'); END;
      CREATE TRIGGER IF NOT EXISTS proposal_no_update BEFORE UPDATE ON proposals BEGIN SELECT RAISE(ABORT,'Immutable accountability proposal'); END;
      CREATE TRIGGER IF NOT EXISTS proposal_no_delete BEFORE DELETE ON proposals BEGIN SELECT RAISE(ABORT,'Immutable accountability proposal'); END;''')
    return db


def register_definition(path,spec):
    db=storage(path)
    try:
        with db:
            old=db.execute('SELECT hash FROM definitions WHERE contract=? AND version=?',(spec['id'],spec['version'])).fetchone()
            if old and old[0]!=spec['hash']:raise ValueError('Accountability definition version is immutable; install a new version')
            if old:return
            db.execute('INSERT OR IGNORE INTO definitions VALUES (?,?,?)',(spec['id'],spec['version'],spec['hash']))
    finally:db.close()


def read(db,table,key):
    if table not in ('receipts','proposals'):raise ValueError('Registered evidence table required')
    raw=db.execute('SELECT payload FROM '+table+' WHERE id=?',(key,)).fetchone()
    if raw is None:raise ValueError('Unknown accountability receipt')
    return json.loads(zlib.decompress(raw[0]))


def write(db,table,row):
    old=db.execute('SELECT payload FROM '+table+' WHERE id=?',(row['id'],)).fetchone()
    if old:
        if json.loads(zlib.decompress(old[0]))!=row:raise ValueError('Accountability identity collision')
        return
    values=(row['id'],row['patient_id'],zlib.compress(json.dumps(row,allow_nan=False).encode())) if table=='receipts' else (row['id'],zlib.compress(json.dumps(row,allow_nan=False).encode()))
    db.execute('INSERT INTO '+table+' VALUES ('+','.join('?' for _ in values)+')',values)


def project(pid,records,finance,spec,cutoff,m=None):
    m=m or source_map();eligible=[];excluded=[]
    for r in records:
        if r.get('patient_id')!=pid or r.get('synthetic') is not True or r.get('identity_status')!='matched':raise ValueError('Matched same-patient synthetic evidence required')
        reason=('unmapped_source_adapter' if r.get('_warehouse_source',{}).get('adapter')!=m['adapter'] else
            'unknown_receipt_date' if not iso(r.get('available_date')) else 'not_received_by_cutoff' if r['available_date']>cutoff else None)
        if reason:excluded.append({'source':F.ref(r),'reason':reason})
        else:eligible.append(r)
    context=[]
    for mapping in m['context']:
        for r in eligible:
            if r['source_table']==mapping['stream']:context.append({'source':F.ref(r),'fields':{f:r.get(f) for f in mapping['fields']},'meaning':mapping['meaning']})
    req=[]
    for requirement in spec['requirements']:
        b=requirement['binding'];sources=[];incomplete=[]
        if b:
            for r in eligible:
                if r['source_table']!=b['stream']:continue
                entry={'source':F.ref(r),'fields':{f:r.get(f) for f in b['fields']}}
                (sources if all(r.get(f) is not None and r.get(f)!='' for f in b['fields']) else incomplete).append(entry)
        status='unbound' if b is None else 'received_input_requires_review' if sources else 'missing_bound_input'
        req.append({**requirement,'status':status,'sources':sources,'incomplete_sources':incomplete,'semantic_validity_established':False})
    requests=[]
    for a in finance['accounts']:
        if a['status']=='reconciled_received_records':continue
        requests.append({'kind':'financial_account_review','account_id':a['account_id'],'reason':a['status'],
            'next_step':a['next_step'],'financial_receipt_id':finance['id'],'source_record_ids':a['source_record_ids'],
            'issue_reasons':sorted({i['reason'] for i in a['issues']}),
            'equation_differences':[e for e in a['equations'] if e['status']!='balanced'],
            'transaction_differences':[{k:c[k] for k in ('stream','status','difference_cents')} for c in a['cash_comparisons'] if c['status']!='agrees_with_received_transactions']})
    if finance['unassigned_sources']:
        requests.append({'kind':'financial_account_binding_review','reason':'unresolved_account_binding',
            'next_step':'Resolve explicit same-patient account links and receipt clocks for these source records.',
            'financial_receipt_id':finance['id'],'sources':finance['unassigned_sources']})
    for r in req:
        if r['scope']=='patient':requests.append({'kind':'program_input_review','requirement_id':r['id'],'reason':r['status'],'next_step':r['needed']})
    for r in requests:r['patient_id']=pid;r['id']=digest([spec['hash'],finance['id'],r])
    return {'schema':VERSION,'synthetic':True,'patient_id':pid,'contract':spec,'available_by':cutoff,
        'financial_receipt_id':finance['id'],'financial_receipt_hash':digest(finance),'financial_status_counts':finance['status_counts'],
        'source_context':context,'excluded_context_sources':excluded,'requirements':req,'requests':requests,
        'payment_readiness':'unresolved','clinical_eligibility_established':False,'payment_entitlement_established':False,'limits':LIMITS}


def execute(path,patient_ids,p,*,prepared=None,selection=None):
    prep=prepared or prepare(path,p)
    if validate(p,prep['financial']['base']['snapshot']['as_of'])!=prep['base']['parameters']:raise ValueError('Accountability preparation mismatch')
    ids=sorted(set(patient_ids));fp=prep['financial'];spec=prep['base']['contract']
    financial=F.execute(path,ids,{'available_by':p['available_by']},prepared=fp,preview_limit=0)
    db=storage(path);fdb=F.storage(path);src=sqlite3.connect((Path(path)/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
    refs=[];counts=Counter();processing=Counter();preview=[]
    streams=sorted({x['stream'] for x in source_map()['context']}|{r['binding']['stream'] for r in spec['requirements'] if r['binding']})
    try:
        with db:
            for pid in ids:
                key=receipt_id(prep,pid);old=db.execute('SELECT payload FROM receipts WHERE id=?',(key,)).fetchone()
                if old:row=json.loads(zlib.decompress(old[0]));processing['patient_cache_hits']+=1
                else:
                    finance=json.loads(zlib.decompress(fdb.execute('SELECT payload FROM receipts WHERE id=?',(F.receipt_id(fp,pid),)).fetchone()[0]))
                    records=[json.loads(r[0]) for r in src.execute('SELECT payload FROM records WHERE patient=? AND stream IN ('+','.join('?' for _ in streams)+') ORDER BY id',[pid,*streams])]
                    row=project(pid,records,finance,spec,p['available_by']);row.update(id=key,base=prep['base'])
                    write(db,'receipts',row);processing['patients_computed']+=1
                refs.append({'patient_id':pid,'receipt_id':key,'receipt_hash':digest(row)})
                counts.update(r['kind'] for r in row['requests'])
                if len(preview)<6:preview.append({'patient_id':pid,'id':key,'requests':len(row['requests']),'financial_status_counts':row['financial_status_counts']})
            # Do not reopen the receipt database while its large write transaction
            # may hold an exclusive cache-spill lock. Recheck input dependencies
            # through their separate source index, without schema registration.
            fresh=F.prepare(path,{'available_by':p['available_by']})
            if fresh['identity']!=fp['identity'] or identity()!=prep['base']['code_identity'] or definition(p['contract'])!=spec:
                raise ValueError('Accountability inputs changed during assembly')
            global_requests=[{'id':digest([prep['base'],r]),'kind':'contract_input_review','requirement_id':r['id'],
                'reason':'unbound','next_step':r['needed'],'scope':'contract','affected_patients':len(ids)} for r in spec['requirements'] if r['scope']=='contract'] if ids else []
            counts.update(r['kind'] for r in global_requests)
            proposal={'schema':VERSION,'synthetic':True,'base':prep['base'],'references':refs,'contract_requests':global_requests,
                'patients_evaluated':len(ids),'population_digest':digest(ids),'selection':selection,'request_counts':dict(counts),
                'requests':sum(counts.values()),'patients':preview,'requests_sent':0,'new_paid_api_calls':0,'limits':LIMITS}
            proposal['id']=digest(proposal);write(db,'proposals',proposal)
        return {**summary(proposal),'processing_counts':dict(processing),'financial_processing_counts':financial['processing_counts']}
    finally:src.close();fdb.close();db.close()


def summary(row):return {k:v for k,v in row.items() if k!='references'}


def load(path,key,*,verify=True):
    if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{64}',key):raise ValueError('Exact accountability proposal ID required')
    db=storage(path)
    try:row=read(db,'proposals',key)
    finally:db.close()
    if digest({k:v for k,v in row.items() if k!='id'})!=key:raise ValueError('Accountability proposal integrity mismatch')
    if verify:
        prep=prepare(path,row['base']['parameters'])
        if prep['base']!=row['base'] or any(receipt_id(prep,r['patient_id'])!=r['receipt_id'] for r in row['references']):raise ValueError('Accountability sources or definitions changed; reassess the selected population')
    return row


def inspect(path,key,*,patient=None,offset=0,limit=20):
    row=load(path,key,verify=patient is None);db=storage(path)
    try:
        if patient is not None:
            contexts(path,key,[patient])
            ref=next((r for r in row['references'] if r['patient_id']==patient),None)
            if ref is None:raise ValueError('Patient outside accountability population')
            result=read(db,'receipts',ref['receipt_id'])
            if digest(result)!=ref['receipt_hash']:raise ValueError('Accountability receipt changed')
            finance=F.inspect(path,result['financial_receipt_id'])
            if digest(finance)!=result['financial_receipt_hash']:raise ValueError('Financial receipt changed')
            return {**result,'proposal_id':key,'financial_evidence':finance}
        if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=50:raise ValueError('Bounded accountability page required')
        items=list(row['contract_requests'])
        for ref in row['references']:items.extend(read(db,'receipts',ref['receipt_id'])['requests'])
        return {**summary(row),'items':items[offset:offset+limit],'offset':offset,'next_offset':offset+limit if offset+limit<len(items) else None}
    finally:db.close()


def contexts(path,key,patients):
    proposal=load(path,key,verify=False);refs={r['patient_id']:r for r in proposal['references']}
    if set(patients)-set(refs):raise ValueError('Patient outside accountability population; context must cover the entire selected work population')
    prep=prepare(path,proposal['base']['parameters'])
    if prep['base']!=proposal['base'] or any(receipt_id(prep,pid)!=refs[pid]['receipt_id'] for pid in patients):raise ValueError('Accountability sources or definitions changed; reassess selected patients')
    db=storage(path)
    try:
        for pid in patients:
            if digest(read(db,'receipts',refs[pid]['receipt_id']))!=refs[pid]['receipt_hash']:raise ValueError('Accountability patient receipt changed')
    finally:db.close()
    return {pid:{'schema':VERSION,'patient_id':pid,'proposal_id':key,'receipt_id':refs[pid]['receipt_id'],
        'receipt_hash':refs[pid]['receipt_hash'],'authority':'financial_and_contract_source_review_only'} for pid in sorted(set(patients))}


def verify_contexts(path,values):
    grouped={}
    for ctx in values:
        if not isinstance(ctx,dict) or set(ctx)!={'schema','patient_id','proposal_id','receipt_id','receipt_hash','authority'} or ctx['schema']!=VERSION or ctx['authority']!='financial_and_contract_source_review_only':raise ValueError('Exact accountability work context required')
        grouped.setdefault(ctx['proposal_id'],[]).append(ctx)
    for key,group in grouped.items():
        expected=contexts(path,key,[c['patient_id'] for c in group])
        if any(c!=expected[c['patient_id']] for c in group):raise ValueError('Accountability patient receipt mismatch')
