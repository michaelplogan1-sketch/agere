"""Compose quoted documentary components with explicit missingness and rate bounds.

This is snapshot patient counting, not a certified payer measure or payment model.
The existing Program interpreter evaluates all predicates. Boolean sensitivity
shares source identities; unexplained cross-source dependencies remain a limit.
"""
from collections import Counter,defaultdict
from pathlib import Path
import hashlib,itertools,json,re,sqlite3,zlib
from ...contracts import digest
from ..policy import expression
from . import program_documents as D,program_resolution as P,evidence_requirements as R
VERSION='documentary-measure-composition/1'
ROLES=('denominator','numerator','exclusion','attribution')
LIMITS=['Snapshot patient counts of quoted documentary criteria; not a certified clinical or payer measure.',
 'No invented eligibility, attribution, missing care, financial savings or clinical decisions.',
 'Sensitivity bounds are logical possibilities conditional on resolving inputs; not probabilities or confidence intervals.',
 'Shared source bindings are correlated. Further dependencies across contracts/patients can make the aggregate bounds conservative.',
 'Not-applied components are explicit descriptive omissions, never proof of payer-defined exclusions or attribution.']
ELIGIBLE={'all':[{'eq':['denominator',True]},{'eq':['attribution',True]},{'eq':['exclusion',False]}]}


def identity():return digest([VERSION,P.identity(),hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])


def validate(p):
 if not isinstance(p,dict) or set(p)!={'title','components'} or not D.text(p['title'],300):raise ValueError('Named measure and four explicit components required')
 if not isinstance(p['components'],dict) or set(p['components'])!=set(ROLES):raise ValueError('Denominator, numerator, exclusion and attribution are required')
 for role,b in p['components'].items():
  if not isinstance(b,dict) or len(b)!=1:raise ValueError('Exact component binding required')
  if 'package' in b:D.validate({'package':b['package'],'filter':'all'})
  elif role in ('exclusion','attribution') and next(iter(b)) in ('unbound','not_applied') and D.text(next(iter(b.values())),2000):pass
  else:raise ValueError('Quoted denominator/numerator required; optional component omissions need a reason')
 return json.loads(json.dumps(p))


def prepare(path,parameters):
 p=validate(parameters);docs={}
 for role,b in p['components'].items():
  if 'package' not in b:continue
  key=b['package'];docs.setdefault(key,D.prepare(path,{'package':key,'filter':'all'}))
  if docs[key]['package']['role']!=role:raise ValueError('Quoted package role does not match measure component')
 code=identity();return {'parameters':p,'documents':docs,'code_identity':code,'identity':digest([code,p,{k:v['identity'] for k,v in docs.items()}])}


def storage(path):
 db=sqlite3.connect(Path(path)/'measure-composition.sqlite3',timeout=30)
 for table in ('patients','populations'):
  db.execute(f'CREATE TABLE IF NOT EXISTS {table}(id TEXT PRIMARY KEY,payload BLOB NOT NULL)')
  for op in ('UPDATE','DELETE'):db.execute(f'CREATE TRIGGER IF NOT EXISTS immutable_measure_{table}_{op} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,"Immutable measure receipt"); END;')
 return db


def put(db,table,key,row):db.execute(f'INSERT OR IGNORE INTO {table} VALUES (?,?)',(key,zlib.compress(json.dumps(row,allow_nan=False).encode())))

def read(db,table,key):
 if table not in ('patients','populations') or not isinstance(key,str) or not re.fullmatch('[a-f0-9]{64}',key):raise ValueError('Exact measure receipt required')
 raw=db.execute(f'SELECT payload FROM {table} WHERE id=?',(key,)).fetchone()
 if not raw:raise ValueError('Measure receipt unavailable')
 return json.loads(zlib.decompress(raw[0]))


def patient_id(prep,pid):return digest([prep['code_identity'],prep['parameters'],pid,{k:D.receipt_id(v,pid) for k,v in prep['documents'].items()}])


def documentary_value(row):return True if row['status']=='supported' else False if row['status']=='contradicted' else None


def sensitivity(components,documents,packages,maximum_unknown=8):
 expressions={};values={};reasons=[]
 for role,b in components.items():
  if 'not_applied' in b:expressions[role]=role;values[role]=role=='attribution';continue
  if 'unbound' in b:expressions[role]=role;values[role]=None;continue
  key=b['package'];row=documents[key]
  try:
   if row['evaluation']['status'] in ('not_effective','semantic_changes_requested','document_not_received_by_cutoff'):raise P.UnsupportedDomain('Specification cannot currently support this component')
   _,vv,inputs,_=P.source_variables(packages[key],row)
   expr=P.boolean_expression(packages[key]['spec']['predicate'],inputs)
   for name,value in vv.items():
    if name in values and values[name] is not value:raise ValueError('Shared measure source values disagree')
    values[name]=value
   expressions[role]=expr
  except P.UnsupportedDomain as exc:expressions[role]=role;values[role]=documentary_value(row);reasons.append(role+': '+str(exc))
 unknown=[k for k,v in values.items() if v is None]
 if len(unknown)>maximum_unknown:
  reasons.append('Shared-source expansion exceeded bound; component-level conservative possibilities retained')
  values={role:documentary_value(documents[b['package']]) if 'package' in b else (role=='attribution' if 'not_applied' in b else None) for role,b in components.items()};expressions={r:r for r in ROLES};unknown=[k for k,v in values.items() if v is None]
 pairs=set()
 for assignment in itertools.product((False,True),repeat=len(unknown)):
  facts={**values,**dict(zip(unknown,assignment))};role_values={r:R.logic(expr,facts) for r,expr in expressions.items()}
  eligible=expression(ELIGIBLE,role_values);pairs.add((int(eligible),int(eligible and role_values['numerator'])))
 return {'possible_denominator_numerator_pairs':[list(p) for p in sorted(pairs)],'method':'conservative_component_envelope' if reasons else 'shared_source_boolean_completion','limitations':reasons,'assignments_evaluated':2**len(unknown)}


def rate_bounds(rows):
 # Every patient can contribute only (0,0), (1,0), or (1,1).
 mandatory=positive=mandatory_uncertain=optional_negative=optional_positive=0;possible=0
 for row in rows:
  pairs={tuple(p) for p in row['sensitivity']['possible_denominator_numerator_pairs']}
  if not pairs<={(0,0),(1,0),(1,1)} or not pairs:raise ValueError('Invalid count possibilities')
  if pairs=={(0,0)}:continue
  possible+=1
  if (0,0) not in pairs:
   mandatory+=1;positive+=pairs=={(1,1)};mandatory_uncertain+=len(pairs)==2
  else:optional_negative+=(1,0) in pairs;optional_positive+=(1,1) in pairs
 low_d=mandatory+optional_negative;high_d=mandatory+optional_positive
 lower=positive/low_d if low_d else (1.0 if optional_positive else None)
 upper=(positive+mandatory_uncertain+optional_positive)/high_d if high_d else (0.0 if optional_negative else None)
 return {'minimum':lower,'maximum':upper,'possible_denominator_patients':possible,'mandatory_denominator_under_completion':mandatory,
  'zero_denominator_possible':mandatory==0,'conditional_on_nonzero_denominator':True,'probability_or_confidence_interval':False}


def project(prep,pid,documents):
 params=prep['parameters'];components={};refs={};unresolved=[];unreviewed=[]
 for role,b in params['components'].items():
  if 'package' in b:
   row=documents[b['package']];value=documentary_value(row)
   components[role]={'value':value,'status':row['status'],'package':b['package'],'receipt_id':row['id'],'receipt_hash':digest(row),'review':row['review']}
   refs[b['package']]={'receipt_id':row['id'],'receipt_hash':digest(row)}
   if row['review']['status']!='mapping_confirmed':unreviewed.append(role)
  else:components[role]={'value':role=='attribution' if 'not_applied' in b else None,'status':next(iter(b)),'reason':next(iter(b.values()))}
  if components[role]['value'] is None:unresolved.append(role)
 values={r:c['value'] for r,c in components.items()};eligible=expression(ELIGIBLE,values)
 achieved=expression({'all':[ELIGIBLE,{'eq':['numerator',True]}]},values)
 status='outside_denominator' if eligible is False else 'denominator_unresolved' if eligible is None else 'numerator_met' if achieved else 'numerator_unresolved' if achieved is None else 'numerator_not_met'
 row={'schema':VERSION,'synthetic':True,'patient_id':pid,'components':components,'effective_denominator':eligible,'effective_numerator':achieved,'status':status,
  'unresolved_components':unresolved,'unreviewed_components':unreviewed,'references':refs,
  'sensitivity':sensitivity(params['components'],documents,{k:v['package'] for k,v in prep['documents'].items()}),'clinical_execution_authorized':False}
 row['id']=patient_id(prep,pid);return row


def execute(path,patients,parameters,*,prepared=None,selection=None):
 prep=prepared or prepare(path,parameters);p=validate(parameters)
 if prep['parameters']!=p:raise ValueError('Measure preparation mismatch')
 ids=sorted(set(patients));db=storage(path);doc=D.storage(path);processing=Counter();rows=[]
 try:
  for key,dp in prep['documents'].items():D.execute(path,ids,{'package':key,'filter':'all'},prepared=dp,preview_limit=0)
  with db:
   for pid in ids:
    key=patient_id(prep,pid);raw=db.execute('SELECT payload FROM patients WHERE id=?',(key,)).fetchone()
    if raw:row=json.loads(zlib.decompress(raw[0]));processing['patient_cache_hits']+=1
    else:
     documents={}
     for pkg,dp in prep['documents'].items():
      data=doc.execute('SELECT payload FROM receipts WHERE id=? AND patient=?',(D.receipt_id(dp,pid),pid)).fetchone()
      if not data:raise ValueError('Measure component patient receipt missing')
      documents[pkg]=json.loads(zlib.decompress(data[0]))
     row=project(prep,pid,documents);put(db,'patients',key,row);processing['patients_computed']+=1
    rows.append(row)
  refs=[{'patient_id':r['patient_id'],'id':r['id'],'hash':digest(r)} for r in rows];key=digest([identity(),p,selection,refs])
  counts=dict(Counter(r['status'] for r in rows));den=sum(r['effective_denominator'] is True for r in rows);num=sum(r['effective_numerator'] is True for r in rows)
  unresolved=sum(r['status'] in ('denominator_unresolved','numerator_unresolved') for r in rows)
  result={'schema':VERSION,'synthetic':True,'id':key,'code_identity':identity(),'definition':p,'definition_hash':digest(p),'patients_evaluated':len(ids),'selection':selection,
   'snapshot':next(iter(prep['documents'].values()))['base']['snapshot'],'component_packages':{k:D.summary(v['package']) for k,v in prep['documents'].items()},
   'counts':{'known_denominator':den,'known_numerator':num,'unresolved_measure_patients':unresolved,'status_counts':counts},
   'rate':num/den if den and not unresolved else None,'rate_status':'incomplete_inputs' if unresolved else 'zero_denominator' if not den else 'documentary_rate',
   'sensitivity_bounds':rate_bounds(rows),'patient_references':refs,'patients':[{k:r[k] for k in ('id','patient_id','status','unresolved_components')} for r in rows[:8]],
   'unreviewed_component_roles':sorted({x for r in rows for x in r['unreviewed_components']}),'not_applied_components':[r for r,b in p['components'].items() if 'not_applied' in b],
   'new_paid_api_calls':0,'certified_measure':False,'financial_value_computed':False,'clinical_execution_authorized':False,'limits':LIMITS}
  fresh=prepare(path,p)
  if any(patient_id(fresh,r['patient_id'])!=r['id'] for r in rows):raise ValueError('Measure evidence changed during evaluation')
  old=db.execute('SELECT payload FROM populations WHERE id=?',(key,)).fetchone()
  if old and json.loads(zlib.decompress(old[0]))!=result:raise ValueError('Measure identity collision')
  with db:put(db,'populations',key,result)
  return {**summary(result),'cache_hit':bool(old),'processing_counts':dict(processing)}
 finally:db.close();doc.close()


def summary(row):return {k:v for k,v in row.items() if k!='patient_references'}


def load(path,key,patients=None):
 db=storage(path)
 try:row=read(db,'populations',key)
 finally:db.close()
 if row['code_identity']!=identity():raise ValueError('Measure logic changed; reassess')
 refs={r['patient_id']:r for r in row['patient_references']};ids=set(refs) if patients is None else set(patients)
 if ids-set(refs):raise ValueError('Patient outside measure population')
 prep=prepare(path,row['definition'])
 if any(patient_id(prep,pid)!=refs[pid]['id'] for pid in ids):raise ValueError('Measure source or mapping review changed; reassess')
 return row


def inspect(path,key,patient=None):
 result=load(path,key,[patient] if patient is not None else None)
 if patient is None:return summary(result)
 ref=next(r for r in result['patient_references'] if r['patient_id']==patient);db=storage(path)
 try:row=read(db,'patients',ref['id'])
 finally:db.close()
 if digest(row)!=ref['hash']:raise ValueError('Measure patient receipt changed')
 documents={k:D.inspect(path,v['receipt_id']) for k,v in row['references'].items()}
 if any(digest(documents[k])!=v['receipt_hash'] for k,v in row['references'].items()):raise ValueError('Measure component receipt changed')
 return {'synthetic':True,'population_id':key,'patient':row,'source_receipts':documents,'definition':result['definition'],'limits':LIMITS}


def contexts(path,key,patients):
 row=load(path,key,patients);refs={r['patient_id']:r for r in row['patient_references']}
 return {pid:{'patient_id':pid,'population_id':key,'reference':refs[pid],'authority':'documentary_measure_context_only'} for pid in sorted(set(patients))}


def verify_contexts(path,contexts):
 groups=defaultdict(list)
 for c in contexts:
  if not isinstance(c,dict) or set(c)!={'patient_id','population_id','reference','authority'} or c['authority']!='documentary_measure_context_only':raise ValueError('Exact measure context required')
  groups[c['population_id']].append(c)
 for key,items in groups.items():
  refs={r['patient_id']:r for r in load(path,key,[c['patient_id'] for c in items])['patient_references']}
  if any(c['reference']!=refs[c['patient_id']] for c in items):raise ValueError('Measure context changed')


def question(denominator,numerator,exclusion='unbound',attribution='unbound'):
 return f'Measure quoted criteria across all synthetic patients using denominator {denominator}, numerator {numerator}, exclusion {exclusion}, and attribution {attribution}.'


def local_question(text):
 from .cohorts import empty
 m=re.fullmatch(r'Measure quoted criteria across all synthetic patients using denominator ([a-f0-9]{64}), numerator ([a-f0-9]{64}), exclusion ([a-f0-9]{64}|unbound), and attribution ([a-f0-9]{64}|unbound)\.',text)
 if not m:return None
 components={r:({'unbound':'No quoted '+r+' specification is bound.'} if v=='unbound' else {'package':v}) for r,v in zip(ROLES,m.groups())}
 return {'interpretation':'Compose documentary measure inputs and show unresolved eligibility, numerator and logical sensitivity. No certified performance or payment.',
  'coverage':'supported_observation','unmet_requirements':[],'nodes':[{'id':'population','operation':'cohort','inputs':[],'parameters':{'specification':empty()},'purpose':'Complete observed synthetic population'},
   {'id':'measure','operation':'measure_review','inputs':['population'],'parameters':{'title':'Quoted documentary measure','components':components},'purpose':'Preserve full component evidence and unknowns'}]}
