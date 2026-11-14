"""Quoted program/measure packages bound to shared evidence, never eligibility.

Reuse the existing Program/compile_document interpreter. This adapter supplies
versioned inputs, immutable source packages and auditable human review records;
source quotation and user-reported review do not prove semantic equivalence.
"""
from collections import Counter
from datetime import date,datetime,timezone
from pathlib import Path
import hashlib,json,re,sqlite3,zlib
from ...contracts import digest
from ..policy import Program,compile_document
from . import program_registry as legacy,care_protocols as C,evidence_requirements as R
from . import patient_input_index as I

VERSION='quoted-program-evidence/1'
FILTERS={'all','supported','contradicted','unresolved','not_effective'}
LIMITS=['Quoted program simulation over received synthetic evidence, not verified enrollment, measure certification, attribution or payment.',
 'Exact quotes establish provenance, not complete or correct interpretation. A recorded semantic review is a user report, not independent verification.',
 'Unbound or missing inputs remain unknown. Negative criteria do not exclude other indications or programs. No rule is automatically activated.']


def code_identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),C.code_identity(),hashlib.sha256(Path(legacy.__file__).read_bytes()).hexdigest(),hashlib.sha256(Path(__file__).parents[1].joinpath('policy.py').read_bytes()).hexdigest()])


def storage(path):
 db=sqlite3.connect(Path(path)/'program-documents.sqlite3',timeout=30)
 db.executescript('CREATE TABLE IF NOT EXISTS packages(id TEXT PRIMARY KEY,program TEXT NOT NULL,version TEXT NOT NULL,payload TEXT NOT NULL,UNIQUE(program,version)); CREATE TABLE IF NOT EXISTS reviews(package TEXT NOT NULL,revision INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(package,revision)); CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY,patient TEXT NOT NULL,payload BLOB NOT NULL);')
 for table in ('packages','reviews','receipts'):
  for op in ('UPDATE','DELETE'):db.execute(f'CREATE TRIGGER IF NOT EXISTS immutable_{table}_{op} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,"Immutable program evidence"); END;')
 return db


def text(value,limit=2000):return isinstance(value,str) and 0<len(value.strip())<=limit


def validate_binding(b):
 if not isinstance(b,dict) or not text(b.get('label'),500):raise ValueError('Named typed program input required')
 kind=b.get('kind')
 if kind=='requirement':
  if set(b)!={'kind','label','contract','version','contract_hash','expected'}:raise ValueError('Pinned requirement input required')
  r=R.definition(b['contract'])
  if b['version']!=r['version'] or b['contract_hash']!=r['hash'] or b['expected'] not in ('supported','contradicted','known'):raise ValueError('Requirement pin changed')
 elif kind=='protocol':
  if set(b)!={'kind','label','protocol','protocol_hash','field'} or not isinstance(b['protocol'],str) or '@' not in b['protocol'] or b['field'] not in ('trigger','documentary_readiness'):raise ValueError('Explicit protocol version and value required')
  if C.definition(b['protocol'])['hash']!=b['protocol_hash']:raise ValueError('Protocol pin changed')
 elif kind=='unbound':
  if set(b)!={'kind','label','value_type','reason'} or b['value_type'] not in ('boolean','number','string') or not text(b['reason']):raise ValueError('Typed missing input and reason required')
 else:raise ValueError('Unsupported program input binding')
 return b


def check_types(expr,bindings):
 op,arg=next(iter(expr.items()))
 if op in ('all','any'):
  for x in arg:check_types(x,bindings)
 elif op=='not':check_types(arg,bindings)
 else:
  key,value=arg;b=bindings[key];kind=b.get('value_type','boolean')
  values=value if op=='in' else [value]
  if not isinstance(values,list) or not 1<=len(values)<=100:raise ValueError('Bounded typed comparison required')
  valid={'boolean':lambda v:type(v) is bool,'number':lambda v:type(v) in (int,float) and __import__('math').isfinite(v),'string':lambda v:text(v,1000)}[kind]
  if not all(valid(v) for v in values) or (kind=='boolean' and op not in ('eq','ne','in')):raise ValueError('Program comparison and bound input types differ')


def validate_package(p):
 if not isinstance(p,dict) or set(p)!={'document','spec','bindings','input_bindings','provenance','role'}:raise ValueError('Exact quoted document, specification, spans, typed inputs, provenance and role required')
 if not text(p['document'],60000) or not isinstance(p['spec'],dict):raise ValueError('Bounded source document and specification required')
 spec=p['spec']
 if set(spec)!={'id','version','effective_start','effective_end','source','purpose','predicate'}:raise ValueError('Exact program specification required')
 if not re.fullmatch('[a-z][a-z0-9_]{0,63}',spec.get('id','')) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',spec.get('version','')):raise ValueError('Bounded program identity required')
 if not all(text(spec.get(k)) for k in ('source','purpose')):raise ValueError('Program source and purpose required')
 legacy.bounded_expression(spec['predicate']);program=Program(spec)
 if len(program.inputs)>20 or not isinstance(p['input_bindings'],dict) or set(p['input_bindings'])!=program.inputs:raise ValueError('Every predicate input needs one explicit binding or missing-input declaration')
 for b in p['input_bindings'].values():validate_binding(b)
 check_types(spec['predicate'],p['input_bindings'])
 if not isinstance(p['bindings'],dict) or any(not isinstance(b,dict) or set(b)!={'start','end','quote'} for b in p['bindings'].values()):raise ValueError('Exact source quote spans required')
 compiled=compile_document(p['document'],spec,p['bindings'])
 provenance=p['provenance']
 if not isinstance(provenance,dict) or set(provenance)!={'document_id','document_version','publisher','source_uri','received_on'} or any(not text(v) for v in provenance.values()):raise ValueError('Explicit document version and provenance required')
 date.fromisoformat(provenance['received_on'])
 if provenance['source_uri']!=spec['source']:raise ValueError('Specification and document source differ')
 if p['role'] not in ('review','denominator','numerator','exclusion','attribution','payment_prerequisite'):raise ValueError('Explicit program/measure role required')
 return compiled


def install(path,package):
 compiled=validate_package(package);meta=json.loads((Path(path)/'run.json').read_text())
 if meta.get('synthetic') is not True:raise ValueError('Synthetic development warehouse required')
 # A package hash binds the document, spans and mappings as well as its rule.
 row={'schema':VERSION,'synthetic':True,**package,'compiled':compiled,'status':'quoted_research_candidate','clinical_execution_authorized':False}
 row['id']=digest(row);db=storage(path)
 try:
  with db:
   old=db.execute('SELECT id FROM packages WHERE program=? AND version=?',(package['spec']['id'],package['spec']['version'])).fetchone()
   if old and old[0]!=row['id']:raise ValueError('Existing document/rule/binding version immutable; use a new version')
   db.execute('INSERT OR IGNORE INTO packages VALUES (?,?,?,?)',(row['id'],package['spec']['id'],package['spec']['version'],json.dumps(row,allow_nan=False)))
  return summary(row)
 finally:db.close()


def bind_existing(path,policy_hash,input_bindings,provenance,role):
 """Bridge an already imported quoted legacy package; do not invent its source."""
 p=next((r for r in legacy.packages(path) if r['policy_hash']==policy_hash),None)
 if not p or not p.get('source_document_available') or not p.get('source_document'):raise ValueError('Existing quoted source document required; defaults without a source cannot be rebound')
 return install(path,{'document':p['source_document'],'spec':p['spec'],'bindings':p['clause_bindings'],
  'input_bindings':input_bindings,'provenance':provenance,'role':role})


def summary(p):return {'id':p['id'],'program':p['spec']['id'],'version':p['spec']['version'],'purpose':p['spec']['purpose'],'role':p['role'],
 'effective_start':p['spec']['effective_start'],'effective_end':p['spec']['effective_end'],'document_hash':p['compiled']['document_hash'],
 'status':p['status'],'unbound_inputs':[k for k,b in p['input_bindings'].items() if b['kind']=='unbound'],'clinical_eligibility_established':False}


def packages(path):
 db=storage(path)
 try:return [summary(json.loads(r[0])) for r in db.execute('SELECT payload FROM packages ORDER BY program,version')]
 finally:db.close()


def discovery(path,as_of):
 """Expose installed dated definitions without installing or selecting a rule.

 Read-only discovery includes unavailable/expired versions so a caller can
 explain their disposition. It never promotes an authored or quoted package to
 an applicable clinical program, or resolves overlapping versions by recency.
 """
 date.fromisoformat(as_of);path=Path(path);quoted=[];older=[]
 file=path/'program-documents.sqlite3'
 if file.exists():
  db=sqlite3.connect(file.resolve().as_uri()+'?mode=ro',uri=True)
  try:
   for raw, in db.execute('SELECT payload FROM packages ORDER BY program,version'):
    p=json.loads(raw)
    if digest({k:v for k,v in p.items() if k!='id'})!=p['id']:raise ValueError('Program document package changed')
    state=review_state(db,p['id']);spec=p['spec']
    temporal=('document_not_received_by_cutoff' if p['provenance']['received_on']>as_of else
        'not_effective' if not spec['effective_start']<=as_of<=spec['effective_end'] else 'within_declared_period')
    quoted.append({**summary(p),'source':spec['source'],'provenance':p['provenance'],
        'temporal_status':temporal,'review':state,'input_bindings':p['input_bindings'],
        'clinical_execution_authorized':False})
  finally:db.close()
 counts=Counter(p['program'] for p in quoted if p['temporal_status']=='within_declared_period')
 for p in quoted:p['overlapping_versions_require_selection']=counts[p['program']]>1 and p['temporal_status']=='within_declared_period'
 file=path/'stack.sqlite3'
 if file.exists():
  db=sqlite3.connect(file.resolve().as_uri()+'?mode=ro',uri=True)
  try:
   if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='program_packages'").fetchone():
    for raw, in db.execute('SELECT payload FROM program_packages ORDER BY program,start,version'):
     p=json.loads(raw);s=p['spec']
     if Program(s).hash!=p['policy_hash']:raise ValueError('Legacy program policy changed')
     older.append({'policy_hash':p['policy_hash'],'program':s['id'],'version':s['version'],
        'purpose':s['purpose'],'source':s['source'],'effective_start':s['effective_start'],'effective_end':s['effective_end'],
        'temporal_status':'within_declared_period' if s['effective_start']<=as_of<=s['effective_end'] else 'not_effective',
        'source_document_available':p.get('source_document_available',False),'status':p['status'],
        'clinical_eligibility_established':False})
  finally:db.close()
 return {'as_of':as_of,'quoted_packages':quoted,'legacy_packages':older,
    'scope':'Installed program-document and legacy program registries only; not an exhaustive search of source records, public programs or buyer contracts. A date-effective research/example rule remains a simulation, not approved program criteria.',
    'clinical_eligibility_established':False,'automatic_version_selection':False}


def load_package(path,key):
 if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{64}',key):raise ValueError('Exact imported package hash required')
 db=storage(path)
 try:r=db.execute('SELECT payload FROM packages WHERE id=?',(key,)).fetchone()
 finally:db.close()
 if r is None:raise ValueError('Unknown imported program package')
 p=json.loads(r[0]);copy={k:v for k,v in p.items() if k!='id'}
 if digest(copy)!=key:raise ValueError('Program document package changed')
 return p


def review_state(db,key):
 row=db.execute('SELECT payload FROM reviews WHERE package=? ORDER BY revision DESC LIMIT 1',(key,)).fetchone()
 return json.loads(row[0]) if row else {'package':key,'revision':0,'status':'unreviewed','independently_verified':False}


def review(path,event):
 if not isinstance(event,dict) or set(event)!={'package','expected_revision','actor','status','note'} or type(event['expected_revision']) is not int or event['expected_revision']<0 or not text(event['actor'],120) or not text(event['note'],3000) or event['status'] not in ('mapping_confirmed','changes_requested','unreviewed'):raise ValueError('Explicit user-reported semantic review required')
 load_package(path,event['package']);db=storage(path)
 try:
  db.execute('BEGIN IMMEDIATE');old=review_state(db,event['package'])
  if event['expected_revision']!=old['revision']:raise ValueError('Semantic review revision changed')
  row={k:v for k,v in event.items() if k!='expected_revision'}|{'revision':old['revision']+1,'recorded_at':datetime.now(timezone.utc).isoformat(),'independently_verified':False,'clinical_execution_authorized':False}
  db.execute('INSERT INTO reviews VALUES (?,?,?)',(event['package'],row['revision'],json.dumps(row)));db.commit();return row
 finally:db.close()


def inspect_package(path,key):
 package=load_package(path,key);db=storage(path)
 try:events=[json.loads(r[0]) for r in db.execute('SELECT payload FROM reviews WHERE package=? ORDER BY revision',(key,))];current=review_state(db,key)
 finally:db.close()
 return {'package':package,'review':current,'review_history':events,'limits':LIMITS}


def validate(p):
 if not isinstance(p,dict) or set(p)!={'package','filter'} or p['filter'] not in FILTERS or not isinstance(p['package'],str) or not re.fullmatch('[a-f0-9]{64}',p['package']):raise ValueError('Exact imported package and documentary filter required')
 return dict(p)


def prepare(path,parameters):
 p=validate(parameters);package=load_package(path,p['package']);validate_package({k:package[k] for k in ('document','spec','bindings','input_bindings','provenance','role')})
 meta=json.loads((Path(path)/'run.json').read_text());index=I.snapshot(path);req={};protocols={}
 if meta.get('synthetic') is not True:raise ValueError('Synthetic development warehouse required')
 for b in package['input_bindings'].values():
  if b['kind']=='requirement' and b['contract'] not in req:req[b['contract']]=R.snapshot(path,{'contract':b['contract'],'filter':'all'})
  if b['kind']=='protocol' and b['protocol'] not in protocols:protocols[b['protocol']]=C.prepare(path,{'protocol':b['protocol'],'filter':'all'})
 db=storage(path)
 try:state=review_state(db,p['package'])
 finally:db.close()
 base={'code':code_identity(),'package':p['package'],'source_digest':meta['source_digest'],'source_run':meta['source_run'],'snapshot':meta['snapshot'],'review':state}
 patients={pid:x['protocol_hash'] for pid,x in index['patients'].items()}
 return {'identity':digest([base,patients,{k:v['identity'] for k,v in req.items()},{k:v['identity'] for k,v in protocols.items()}]),'base':base,'package':package,'patients':patients,'requirements':req,'protocols':protocols,'requirement_code':R.code_identity()}


def receipt_id(prep,pid,ic=None):return digest([prep['base'],pid,prep['patients'].get(pid),
 {k:R.receipt_id(s,pid,code=prep['requirement_code'],interpretation_code=ic) for k,s in prep['requirements'].items()},
 {k:C.receipt_id(s,pid,ic) for k,s in prep['protocols'].items()}])


def evaluate(prep,pid,inputs):
 package=prep['package'];asof=prep['base']['snapshot']['as_of'];program=Program(package['spec']);facts={k:v['value'] for k,v in inputs.items()}
 result=program.evaluate(facts,asof);status={'criteria_met':'supported','criteria_not_met':'contradicted','unknown':'unresolved','not_effective':'not_effective'}[result['status']]
 if prep['base']['review']['status']=='changes_requested':status='unresolved';result={**result,'status':'semantic_changes_requested'}
 if package['provenance']['received_on']>asof:status='unresolved';result={**result,'status':'document_not_received_by_cutoff'}
 return {'schema':VERSION,'synthetic':True,'patient_id':pid,'as_of':asof,'package_id':package['id'],'policy':summary(package),
  'status':status,'evaluation':result,'inputs':inputs,'review':prep['base']['review'],'clinical_eligibility_established':False,'clinical_execution_authorized':False,'limits':LIMITS}


def execute(path,patients,parameters,*,prepared=None,preview_limit=6):
 p=validate(parameters);prep=prepared or prepare(path,p);ids=sorted(set(patients));ic=C.interpretation.code_identity()
 if prep['package']['id']!=p['package'] or set(ids)-set(prep['patients']):raise ValueError('Program package or patient mismatch')
 if type(preview_limit) is not int or not 0<=preview_limit<=20:raise ValueError('Bounded program preview required')
 for k,s in prep['requirements'].items():R.execute(path,ids,{'contract':k,'filter':'all'},prepared=s,preview_limit=0)
 for k,s in prep['protocols'].items():C.execute(path,ids,{'protocol':k,'filter':'all'},prepared=s,preview_limit=0)
 db=storage(path);reqdb=R.storage(path);cpdb=C.storage(path);source=sqlite3.connect((Path(path)/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
 statuses=Counter();processing=Counter();yes=set();unknown=set();preview=[]
 try:
  with db:
   for pid in ids:
    key=receipt_id(prep,pid,ic);old=db.execute('SELECT payload FROM receipts WHERE id=? AND patient=?',(key,pid)).fetchone()
    if old:row=json.loads(zlib.decompress(old[0]));processing['patient_cache_hits']+=1
    else:
     bindings=prep['package']['input_bindings'];requirement_rows={}
     if prep['requirements']:
      patient=json.loads(source.execute('SELECT payload FROM patients WHERE id=?',(pid,)).fetchone()[0]);records=[json.loads(r[0]) for r in source.execute('SELECT payload FROM records WHERE patient=? ORDER BY id',(pid,))]
      cprep={'protocol':{'bindings':{k:b for k,b in bindings.items() if b['kind']=='requirement'}},'documentary_dependencies':prep['requirements'],'supporting_code_identity':prep['requirement_code'],'base':prep['base']}
      requirement_rows=C.requirement_inputs(reqdb,source,cprep,pid,ic,patient,records)
     inputs={}
     for name,b in bindings.items():
      if b['kind']=='unbound':value=None;state={'status':'unbound','reason':b['reason'],'value_type':b['value_type']}
      elif b['kind']=='requirement':
       result=C.requirement_binding(requirement_rows[b['contract']],b,pid,prep['base']['snapshot']['as_of']);value=result['value'];state={'status':result['status'],'requirement_reference':result['requirement_reference'],'evidence':requirement_rows[b['contract']]}
      else:
       rid=C.receipt_id(prep['protocols'][b['protocol']],pid,ic);raw=cpdb.execute('SELECT payload FROM receipts WHERE id=? AND patient=?',(rid,pid)).fetchone()
       if raw is None:raise ValueError('Required protocol input receipt missing')
       protocol=json.loads(zlib.decompress(raw[0]))
       if protocol['patient_id']!=pid or protocol['protocol']['hash']!=b['protocol_hash']:raise ValueError('Program protocol input mismatch')
       value=protocol[b['field']];state={'status':protocol['status'],'protocol_receipt_id':rid,'receipt_hash':digest(protocol),'evidence':protocol}
      inputs[name]={'label':b['label'],'value':value,'kind':b['kind'],'quote':prep['package']['bindings'][name],**state}
     row=evaluate(prep,pid,inputs);row.update(id=key,base=prep['base']);db.execute('INSERT INTO receipts VALUES (?,?,?)',(key,pid,zlib.compress(json.dumps(row,allow_nan=False).encode())));processing['patient_rules_computed']+=1
    statuses[row['status']]+=1
    if p['filter']=='all' or row['status']==p['filter']:yes.add(pid)
    elif row['status']=='unresolved':unknown.add(pid)
    sample={k:row[k] for k in ('id','patient_id','status','evaluation')};preview=sorted(preview+[sample],key=lambda r:(r['status']!='supported',r['patient_id']))[:preview_limit]
  fresh=prepare(path,p)
  if any(receipt_id(fresh,pid,ic)!=receipt_id(prep,pid,ic) for pid in ids):raise ValueError('Program rule source or review changed; rerun')
 finally:db.close();source.close();reqdb.close();cpdb.close()
 return yes,unknown,{'schema':VERSION,'synthetic':True,'package':summary(prep['package']),'identity':prep['identity'],'patients_evaluated':len(ids),'population_digest':digest(ids),'status_counts':dict(statuses),'processing_counts':dict(processing),'review':prep['base']['review'],'patients':preview,'all_patient_receipts_persisted':True,'new_paid_api_calls':0,'clinical_eligibility_established':False,'limits':LIMITS}


def inspect(path,key):
 if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{64}',key):raise ValueError('Exact program-rule receipt required')
 db=storage(path)
 try:raw=db.execute('SELECT payload FROM receipts WHERE id=?',(key,)).fetchone()
 finally:db.close()
 if raw is None:raise ValueError('Unknown program-rule receipt')
 row=json.loads(zlib.decompress(raw[0]));fresh=prepare(path,{'package':row['package_id'],'filter':'all'})
 if receipt_id(fresh,row['patient_id'])!=key:raise ValueError('Program evidence or semantic review changed; rerun')
 return row


def contexts(path,patients,nodes,preparations,*,evaluated_patients):
 db=storage(path);result={};ic=C.interpretation.code_identity()
 try:
  for pid in sorted(set(patients)):
   refs=[]
   for node in nodes:
    prep=preparations[node['id']];assessed=pid in evaluated_patients[node['id']]
    key=receipt_id(prep,pid,ic) if assessed else None
    raw=db.execute('SELECT payload FROM receipts WHERE id=? AND patient=?',(key,pid)).fetchone() if assessed else None
    if assessed and raw is None:raise ValueError('Assessed program-document receipt missing')
    row=json.loads(zlib.decompress(raw[0])) if raw else None
    refs.append({'node_id':node['id'],'package':prep['package']['id'],'filter':node['parameters']['filter'],
      'receipt_id':key,'receipt_hash':digest(row) if row else None,'status':row['status'] if row else 'not_assessed'})
   result[pid]={'patient_id':pid,'references':refs,'authority':'quoted_rule_simulation_selection_only'}
 finally:db.close()
 return result


def verify_contexts(path,contexts):
 if not contexts:return
 preps={};db=storage(path);ic=C.interpretation.code_identity()
 try:
  for ctx in contexts:
   if not isinstance(ctx,dict) or set(ctx)!={'patient_id','references','authority'} or ctx['authority']!='quoted_rule_simulation_selection_only' or not isinstance(ctx['references'],list) or not ctx['references']:raise ValueError('Exact program document context required')
   for ref in ctx['references']:
    if set(ref)!={'node_id','package','filter','receipt_id','receipt_hash','status'}:raise ValueError('Exact quoted-rule reference required')
    params=validate({'package':ref['package'],'filter':ref['filter']})
    if ref['receipt_id'] is None:
     if ref['status']!='not_assessed' or ref['receipt_hash'] is not None:raise ValueError('Program document receipt missing')
     load_package(path,ref['package']);continue
    if ref['package'] not in preps:preps[ref['package']]=prepare(path,params)
    if receipt_id(preps[ref['package']],ctx['patient_id'],ic)!=ref['receipt_id']:raise ValueError('Program source or semantic review changed; reassess work')
    raw=db.execute('SELECT payload FROM receipts WHERE id=? AND patient=?',(ref['receipt_id'],ctx['patient_id'])).fetchone()
    if raw is None:raise ValueError('Program document context unavailable')
    row=json.loads(zlib.decompress(raw[0]))
    if digest(row)!=ref['receipt_hash'] or row['status']!=ref['status']:raise ValueError('Program document context changed')
 finally:db.close()


def question(key,program=None):
 return ('Evaluate imported program package '+key+' across all synthetic patients.'+
  (' Prepare source-review work using '+program+' for supported documentary criteria.' if program else ''))


def local_question(text):
 from .cohorts import empty
 # Exact parameterized commands only; extra population qualifiers must go to
 # the regular planner instead of being silently removed.
 match=re.fullmatch(r'Evaluate imported program package ([a-f0-9]{64}) across all synthetic patients\.(?: Prepare source-review work using ([a-z0-9_:@.-]{1,160}) for supported documentary criteria\.)?',text)
 if not match:return None
 key,program=match.groups()
 nodes=[{'id':'population','operation':'cohort','inputs':[],'parameters':{'specification':empty()},'purpose':'Complete observed synthetic population'},
  {'id':'quoted_rule','operation':'program_document','inputs':['population'],'parameters':{'package':key,'filter':'supported' if program else 'all'},'purpose':'Evaluate the quoted research rule with pinned shared evidence and explicit unknowns'}]
 if program:nodes.append({'id':'work','operation':'worklist','inputs':['quoted_rule'],'parameters':{'program':program},'purpose':'Retain quoted-rule selection provenance in source-review work; no outreach'})
 return {'interpretation':'Evaluate an explicit imported research package without treating quoted criteria as verified eligibility.','coverage':'supported_observation','unmet_requirements':[],'nodes':nodes}
