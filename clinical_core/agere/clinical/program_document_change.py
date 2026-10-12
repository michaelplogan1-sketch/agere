"""Patient-level impact of two quoted specification versions over one snapshot.

Rule evaluation remains in program_documents/Program. This layer only compares
their immutable evidence receipts; it never activates rules or updates patients.
"""
from collections import Counter
from pathlib import Path
import hashlib,json,re,sqlite3,zlib
from ...contracts import digest
from . import program_documents as D

VERSION='quoted-program-impact/1'
LIMITS=['Both specifications use the same received synthetic population. Differences are rule/input changes, not patient outcomes.',
        'No automatic activation, verified eligibility, clinical review or program execution.']


def code_identity():return digest([VERSION,D.code_identity(),hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])


def validate(p):
 if not isinstance(p,dict) or set(p)!={'before','after'} or any(not isinstance(v,str) or not re.fullmatch('[a-f0-9]{64}',v) for v in p.values()) or p['before']==p['after']:raise ValueError('Two different exact imported package hashes required')
 return dict(p)


def prepare(path,parameters):
 p=validate(parameters);versions={s:D.prepare(path,{'package':k,'filter':'all'}) for s,k in p.items()}
 a,b=versions['before'],versions['after']
 if a['package']['spec']['id']!=b['package']['spec']['id'] or a['package']['spec']['version']==b['package']['spec']['version']:raise ValueError('Different versions of the same program required')
 if any(a['base'][k]!=b['base'][k] for k in ('snapshot','source_digest','source_run')):raise ValueError('One received source snapshot required')
 return {'parameters':p,'versions':versions,'identity':digest([code_identity(),p,{s:v['identity'] for s,v in versions.items()}])}


def storage(path):
 db=sqlite3.connect(Path(path)/'program-document-changes.sqlite3',timeout=30)
 db.executescript('CREATE TABLE IF NOT EXISTS comparisons(id TEXT PRIMARY KEY,payload BLOB NOT NULL); CREATE TRIGGER IF NOT EXISTS immutable_change_update BEFORE UPDATE ON comparisons BEGIN SELECT RAISE(ABORT,"Immutable quoted-program comparison"); END; CREATE TRIGGER IF NOT EXISTS immutable_change_delete BEFORE DELETE ON comparisons BEGIN SELECT RAISE(ABORT,"Immutable quoted-program comparison"); END;')
 return db


def project(a,b):
 if a['patient_id']!=b['patient_id'] or a['as_of']!=b['as_of'] or a['base']['source_digest']!=b['base']['source_digest']:raise ValueError('Same-patient same-snapshot comparison required')
 def ref(r):return {'id':r['id'],'package':r['package_id'],'status':r['status'],'receipt_hash':digest(r),'missing_inputs':r['evaluation']['missing']}
 return {'patient_id':a['patient_id'],'before':ref(a),'after':ref(b),
  'changed_inputs':[k for k in sorted(set(a['inputs'])|set(b['inputs'])) if a['inputs'].get(k)!=b['inputs'].get(k)],
  'status_changed':a['status']!=b['status'],'entered_supported':a['status']!='supported' and b['status']=='supported',
  'left_supported':a['status']=='supported' and b['status']!='supported','became_unresolved':a['status']!='unresolved' and b['status']=='unresolved',
  'clinical_eligibility_established':False}


def execute(path,patients,parameters,*,prepared=None,preview_limit=6):
 p=validate(parameters);prep=prepared or prepare(path,p);ids=sorted(set(patients));versions=prep['versions']
 if prep['parameters']!=p or type(preview_limit) is not int or not 0<=preview_limit<=20:raise ValueError('Matching comparison and bounded preview required')
 keys={}
 for side,v in versions.items():
  D.execute(path,ids,{'package':p[side],'filter':'all'},prepared=v,preview_limit=0)
  keys[side]=[D.receipt_id(v,pid) for pid in ids]
 identity=digest([code_identity(),p,ids,keys]);db=storage(path);source=D.storage(path)
 try:
  cached=db.execute('SELECT payload FROM comparisons WHERE id=?',(identity,)).fetchone()
  if cached:result=json.loads(zlib.decompress(cached[0]))
  else:
   rows=[]
   for i,pid in enumerate(ids):
    receipts=[]
    for side in ('before','after'):
     raw=source.execute('SELECT payload FROM receipts WHERE id=? AND patient=?',(keys[side][i],pid)).fetchone()
     if raw is None:raise ValueError('Compared source receipt unavailable')
     receipts.append(json.loads(zlib.decompress(raw[0])))
    rows.append(project(*receipts))
   a,b=(versions[s]['package'] for s in ('before','after'))
   result={'id':identity,'schema':VERSION,'synthetic':True,'parameters':p,'packages':{s:D.summary(v['package']) for s,v in versions.items()},
    'definition_fields_changed':[k for k in ('document','spec','bindings','input_bindings','provenance','role') if a[k]!=b[k]],
    'patients_evaluated':len(ids),'population_digest':digest(ids),'snapshot':versions['before']['base']['snapshot'],
    'status_transitions':[{'before':x,'after':y,'patients':n} for (x,y),n in sorted(Counter((r['before']['status'],r['after']['status']) for r in rows).items())],
    'change_counts':{k:sum(r[k] for r in rows) for k in ('status_changed','entered_supported','left_supported','became_unresolved')},
    'patient_changes':rows,'code_identity':code_identity(),'new_paid_api_calls':0,'limits':LIMITS,'clinical_eligibility_established':False}
   with db:db.execute('INSERT OR IGNORE INTO comparisons VALUES (?,?)',(identity,zlib.compress(json.dumps(result,allow_nan=False).encode())))
  fresh=prepare(path,p)
  if any(D.receipt_id(fresh['versions'][s],pid)!=keys[s][i] for s in versions for i,pid in enumerate(ids)):raise ValueError('Compared evidence or review changed; rerun')
  return {**{k:v for k,v in result.items() if k!='patient_changes'},'patients':sorted(result['patient_changes'],key=lambda r:(not r['status_changed'],r['patient_id']))[:preview_limit],
   'all_patient_changes_persisted':True,'comparison_cache_hit':bool(cached)}
 finally:db.close();source.close()


def inspect(path,key,patient):
 if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{64}',key) or not isinstance(patient,str):raise ValueError('Exact comparison and patient required')
 db=storage(path)
 try:raw=db.execute('SELECT payload FROM comparisons WHERE id=?',(key,)).fetchone()
 finally:db.close()
 if raw is None:raise ValueError('Unknown quoted-program comparison')
 result=json.loads(zlib.decompress(raw[0]))
 if result['code_identity']!=code_identity():raise ValueError('Comparison implementation changed; rerun')
 row=next((r for r in result['patient_changes'] if r['patient_id']==patient),None)
 if row is None:raise ValueError('Patient not in compared population')
 receipts={s:D.inspect(path,row[s]['id']) for s in ('before','after')}
 if any(digest(receipts[s])!=row[s]['receipt_hash'] for s in receipts):raise ValueError('Compared patient receipt changed')
 return {'schema':VERSION,'synthetic':True,'id':key,'patient_change':row,'program_receipts':receipts,'limits':LIMITS}


def question(before,after):return 'Compare imported program packages '+before+' and '+after+' across all synthetic patients.'


def local_question(text):
 from .cohorts import empty
 m=re.fullmatch(r'Compare imported program packages ([a-f0-9]{64}) and ([a-f0-9]{64}) across all synthetic patients\.',text)
 if not m:return None
 return {'interpretation':'Compare explicit quoted rule versions over the same observed population; no activation or outcome claim.',
  'coverage':'supported_observation','unmet_requirements':[],'nodes':[
   {'id':'population','operation':'cohort','inputs':[],'parameters':{'specification':empty()},'purpose':'Complete observed synthetic population'},
   {'id':'impact','operation':'program_document_change','inputs':['population'],'parameters':dict(zip(('before','after'),m.groups())),'purpose':'Preserve patient-specific before and after source receipts'}]}
