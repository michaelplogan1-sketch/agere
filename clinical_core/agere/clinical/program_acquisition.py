"""Quoted-rule review queues in the existing evidence-acquisition store."""
from collections import Counter,defaultdict
from pathlib import Path
import hashlib,json,re,zlib
from ...contracts import digest
from . import program_documents as D,program_resolution as P,evidence_acquisition as E

VERSION='quoted-program-acquisition/1'


def identity():return digest([VERSION,P.identity(),hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])


def storage(path):
 db=E.storage(path)
 for name in ('program_proposals','program_resolution_rows'):
  db.execute(f'CREATE TABLE IF NOT EXISTS {name}(id TEXT PRIMARY KEY,payload BLOB NOT NULL)')
  for op in ('UPDATE','DELETE'):db.execute(f'CREATE TRIGGER IF NOT EXISTS immutable_{name}_{op} BEFORE {op} ON {name} BEGIN SELECT RAISE(ABORT,"Immutable program acquisition evidence"); END;')
 return db


def validate(p):
 if not isinstance(p,dict) or set(p)!={'package'}:raise ValueError('Explicit imported package required')
 D.validate({'package':p['package'],'filter':'all'});return dict(p)


def dependency(path,parameters):
 p=validate(parameters);prep=D.prepare(path,{'package':p['package'],'filter':'all'})
 return {'document':prep,'parameters':p,'identity':digest([identity(),prep['identity']])}


def decode(db,table,key):
 if table not in ('program_proposals','program_resolution_rows'):raise ValueError('Unsupported acquisition table')
 raw=db.execute(f'SELECT payload FROM {table} WHERE id=?',(key,)).fetchone()
 if raw is None:raise ValueError('Acquisition receipt unavailable')
 return json.loads(zlib.decompress(raw[0]))


def save(db,table,key,row):
 if table not in ('program_proposals','program_resolution_rows'):raise ValueError('Unsupported acquisition table')
 db.execute(f'INSERT OR IGNORE INTO {table} VALUES (?,?)',(key,zlib.compress(json.dumps(row,allow_nan=False).encode())))


def summary(row):return {k:v for k,v in row.items() if k not in ('references','items')}


def prepare(path,patients,parameters,*,prepared=None,selection=None):
 p=validate(parameters);prep=prepared or dependency(path,p)
 if prep['parameters']!=p:raise ValueError('Acquisition preparation mismatch')
 ids=sorted(set(patients));dp=prep['document'];code=identity();projection_identity=P.identity()
 D.execute(path,ids,{'package':p['package'],'filter':'all'},prepared=dp,preview_limit=0)
 doc=D.storage(path);db=storage(path);requests={};refs=[];processing=Counter();statuses=Counter();resolution_statuses=Counter()
 try:
  with db:
   for pid in ids:
    key=D.receipt_id(dp,pid);projection_id=digest([projection_identity,key]);old=db.execute('SELECT payload FROM program_resolution_rows WHERE id=?',(projection_id,)).fetchone()
    if old:row=json.loads(zlib.decompress(old[0]));processing['patient_projection_cache_hits']+=1
    else:
     raw=doc.execute('SELECT payload FROM receipts WHERE id=? AND patient=?',(key,pid)).fetchone()
     if raw is None:raise ValueError('Evaluated program receipt missing')
     source=json.loads(zlib.decompress(raw[0]));row=P.project(dp['package'],source);save(db,'program_resolution_rows',projection_id,row);processing['patient_projections_computed']+=1
    statuses[row['status']]+=1;resolution_statuses[row['resolution']['status']]+=1
    request_ids=[]
    for need in row['requests']:
     # Specification defects are one shared task with explicit patient impact,
     # never thousands of purported patient-level care gaps.
     item=requests.setdefault(need['id'],{k:v for k,v in need.items() if k not in ('relevant_to_unresolved_predicate','input_can_resolve_rule_alone')})
     item.setdefault('patients_with_dependency',[]).append(pid)
     if need['relevant_to_unresolved_predicate']:item.setdefault('patients_with_conditional_impact',[]).append(pid)
     if need['input_can_resolve_rule_alone']:item.setdefault('patients_with_single_input_resolution',[]).append(pid)
     request_ids.append(need['id'])
    refs.append({'patient_id':pid,'program_receipt_id':key,'program_receipt_hash':row['program_receipt_hash'],'projection_id':projection_id,'projection_hash':digest(row),'request_ids':sorted(request_ids)})
  items=sorted(requests.values(),key=lambda r:(r['scope']!='specification',r['patient_id'] or '',r['kind'],r['id']))
  for item in items:
   for field in ('patients_with_dependency','patients_with_conditional_impact','patients_with_single_input_resolution'):item[field]=sorted(set(item.get(field,[])))
  key=digest([code,p,dp['base'],refs,selection]);result={'schema':VERSION,'id':key,'synthetic':True,'code_identity':code,'package':D.summary(dp['package']),
   'patients_evaluated':len(ids),'population_digest':digest(ids),'selection':selection,'snapshot':dp['base']['snapshot'],'review':dp['base']['review'],
   'status_counts':dict(statuses),'resolution_status_counts':dict(resolution_statuses),'references':refs,'items':items,
   'counts':{'requests':len(items),'specification_requests':sum(i['scope']=='specification' for i in items),'patient_requests':sum(i['scope']=='patient' for i in items),
    'patients_with_conditional_requests':len({pid for i in items for pid in i['patients_with_conditional_impact']}),'request_kinds':dict(Counter(i['kind'] for i in items))},
   'new_paid_api_calls':0,'requests_sent':0,'clinical_execution_authorized':False,'limits':P.LIMITS}
  fresh=D.prepare(path,{'package':p['package'],'filter':'all'})
  if any(D.receipt_id(fresh,ref['patient_id'])!=ref['program_receipt_id'] for ref in refs):raise ValueError('Program evidence/review changed while preparing acquisition')
  prior=db.execute('SELECT payload FROM program_proposals WHERE id=?',(key,)).fetchone()
  if prior and json.loads(zlib.decompress(prior[0]))!=result:raise ValueError('Acquisition proposal identity collision')
  with db:save(db,'program_proposals',key,result)
  return {**summary(result),'cache_hit':bool(prior),'processing_counts':dict(processing)}
 finally:db.close();doc.close()


def load(path,key,*,patients=None):
 if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{64}',key):raise ValueError('Exact program acquisition ID required')
 db=storage(path)
 try:row=decode(db,'program_proposals',key)
 finally:db.close()
 if row['code_identity']!=identity():raise ValueError('Program acquisition logic changed; refresh proposal')
 refs={r['patient_id']:r for r in row['references']};ids=set(refs) if patients is None else set(patients)
 if ids-set(refs):raise ValueError('Patient outside acquisition population')
 fresh=D.prepare(path,{'package':row['package']['id'],'filter':'all'})
 if any(D.receipt_id(fresh,pid)!=refs[pid]['program_receipt_id'] for pid in ids):raise ValueError('Program acquisition source or review changed; refresh proposal')
 return row


def list_items(path,key,offset=0,limit=20):
 if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=50:raise ValueError('Bounded acquisition page required')
 row=load(path,key)
 return {**summary(row),'offset':offset,'items':row['items'][offset:offset+limit],'next_offset':offset+limit if offset+limit<len(row['items']) else None}


def inspect_patient(path,key,patient):
 row=load(path,key,patients=[patient]);ref=next(r for r in row['references'] if r['patient_id']==patient);db=storage(path)
 try:projection=decode(db,'program_resolution_rows',ref['projection_id'])
 finally:db.close()
 receipt=D.inspect(path,ref['program_receipt_id'])
 if digest(projection)!=ref['projection_hash'] or digest(receipt)!=ref['program_receipt_hash']:raise ValueError('Acquisition source receipt changed')
 return {'schema':VERSION,'synthetic':True,'proposal_id':key,'resolution':projection,'program_receipt':receipt,'limits':P.LIMITS}


def contexts(path,key,patients):
 row=load(path,key,patients=patients);refs={r['patient_id']:r for r in row['references']}
 return {pid:{'patient_id':pid,'proposal_id':key,'reference':refs[pid],'authority':'internal_evidence_acquisition_only'} for pid in sorted(set(patients))}


def verify_contexts(path,contexts):
 groups=defaultdict(list)
 for ctx in contexts:
  if not isinstance(ctx,dict) or set(ctx)!={'patient_id','proposal_id','reference','authority'} or ctx['authority']!='internal_evidence_acquisition_only':raise ValueError('Exact program acquisition context required')
  groups[ctx['proposal_id']].append(ctx)
 for key,items in groups.items():
  row=load(path,key,patients=[c['patient_id'] for c in items]);refs={r['patient_id']:r for r in row['references']}
  if any(c['reference']!=refs[c['patient_id']] for c in items):raise ValueError('Acquisition patient context changed')


def question(package,program=None):
 return 'Prepare evidence requests for imported program package '+package+' across all synthetic patients.'+(' Attach them to review work using '+program+'.' if program else '')


def local_question(text):
 from .cohorts import empty
 m=re.fullmatch(r'Prepare evidence requests for imported program package ([a-f0-9]{64}) across all synthetic patients\.(?: Attach them to review work using ([a-z0-9_:@.-]{1,160})\.)?',text)
 if not m:return None
 package,program=m.groups();nodes=[{'id':'population','operation':'cohort','inputs':[],'parameters':{'specification':empty()},'purpose':'Complete observed synthetic population'},
  {'id':'requests','operation':'program_acquisition','inputs':['population'],'parameters':{'package':package},'purpose':'Separate specification defects from patient evidence needs and preserve hypothetical input dependencies'}]
 if program:nodes.append({'id':'work','operation':'worklist','inputs':['population','requests'],'parameters':{'program':program},'purpose':'Attach source requests to separate clinical-review assessments; no outreach'})
 return {'interpretation':'Prepare internal source/specification review requests. Conditional cases are not predictions or missing care.',
  'coverage':'supported_observation','unmet_requirements':[],'nodes':nodes}
