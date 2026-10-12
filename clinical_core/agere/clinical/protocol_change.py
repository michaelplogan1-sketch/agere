"""Compare explicit versions over the same received population, without activation.

These are changes in source-review results under different rule/input contracts,
not observed changes in patients, measured benefit or a claim of improved accuracy.
"""
from collections import Counter
from pathlib import Path
import hashlib,json,re,sqlite3,zlib
from ...contracts import digest
from . import care_protocols as C

VERSION='protocol-version-impact/1'
LIMITS=['Both versions use the same received snapshot. Differences describe rule/input coverage, not change in a patient or an outcome.',
        'This comparison does not activate a rule, advance program work, establish eligibility or validate clinical accuracy.']


def code_identity():return digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),C.code_identity()])


def validate(p):
 if not isinstance(p,dict) or set(p)!={'before','after'} or any(not isinstance(p[x],str) or '@' not in p[x] for x in p):raise ValueError('Two explicit version-qualified protocols required')
 a,b=C.definition(p['before']),C.definition(p['after'])
 if a['id']!=b['id'] or a['version']==b['version']:raise ValueError('Two different versions of the same protocol required')
 return dict(p)


def prepare(path,parameters):
 p=validate(parameters);versions={k:C.prepare(path,{'protocol':v,'filter':'all'}) for k,v in p.items()}
 if versions['before']['base']['snapshot']!=versions['after']['base']['snapshot']:raise ValueError('One source snapshot required for version comparison')
 return {'parameters':p,'versions':versions,'identity':digest([code_identity(),p,{k:v['identity'] for k,v in versions.items()}])}


def storage(path):
 db=sqlite3.connect(Path(path)/'protocol-changes.sqlite3',timeout=30)
 db.executescript('CREATE TABLE IF NOT EXISTS comparisons(id TEXT PRIMARY KEY,payload BLOB NOT NULL); CREATE TRIGGER IF NOT EXISTS immutable_comparison_update BEFORE UPDATE ON comparisons BEGIN SELECT RAISE(ABORT,"Immutable protocol comparison"); END; CREATE TRIGGER IF NOT EXISTS immutable_comparison_delete BEFORE DELETE ON comparisons BEGIN SELECT RAISE(ABORT,"Immutable protocol comparison"); END;')
 return db


def project(before,after):
 if before['patient_id']!=after['patient_id'] or before['as_of']!=after['as_of'] or before['base']['source_digest']!=after['base']['source_digest']:raise ValueError('Same-patient same-snapshot protocol comparison required')
 def ref(row):return {k:row[k] for k in ('id','status','trigger','documentary_readiness','review_candidate')}|{'protocol':C.qualified(row['protocol']),'protocol_hash':row['protocol']['hash'],'receipt_hash':digest(row)}
 changes=[name for name in sorted(set(before['bindings'])|set(after['bindings'])) if before['bindings'].get(name)!=after['bindings'].get(name)]
 return {'patient_id':before['patient_id'],'before':ref(before),'after':ref(after),'changed_bindings':changes,
   'criterion_value_changed':before['trigger'] is not after['trigger'],
   'readiness_value_changed':before['documentary_readiness'] is not after['documentary_readiness'],
   'entered_review':not before['review_candidate'] and after['review_candidate'],
   'exited_review':before['review_candidate'] and not after['review_candidate'],
   'clinical_eligibility_established':False}


def execute(path,patients,parameters,*,prepared=None,preview_limit=8):
 p=validate(parameters);prep=prepared or prepare(path,p)
 if prep['parameters']!=p:raise ValueError('Protocol comparison preparation mismatch')
 if type(preview_limit) is not int or not 0<=preview_limit<=20:raise ValueError('Bounded comparison preview required')
 ids=sorted(set(patients));ic=C.interpretation.code_identity();versions=prep['versions'];keys={}
 for side in ('before','after'):
  if set(ids)-set(versions[side]['patient_inputs']):raise ValueError('Unknown comparison patient')
  C.execute(path,ids,{'protocol':p[side],'filter':'all'},prepared=versions[side],preview_limit=0)
  keys[side]=[C.receipt_id(versions[side],pid,ic) for pid in ids]
 identity=digest([code_identity(),p,ids,keys]);db=storage(path);source=C.storage(path)
 try:
  cached=db.execute('SELECT payload FROM comparisons WHERE id=?',(identity,)).fetchone()
  if cached:result=json.loads(zlib.decompress(cached[0]))
  else:
   rows=[]
   for idx,pid in enumerate(ids):
    rr=[]
    for side in ('before','after'):
     raw=source.execute('SELECT payload FROM receipts WHERE id=? AND patient=?',(keys[side][idx],pid)).fetchone()
     if raw is None:raise ValueError('Protocol comparison receipt missing')
     rr.append(json.loads(zlib.decompress(raw[0])))
    rows.append(project(*rr))
   specs={side:versions[side]['protocol'] for side in versions};a,b=specs['before'],specs['after']
   changed_definition_fields=[k for k in sorted(set(a)|set(b)) if k not in ('version','hash') and a.get(k)!=b.get(k)]
   result={'id':identity,'schema':VERSION,'synthetic':True,'parameters':p,'definitions':specs,
     'source_snapshot':versions['after']['base'],'patients_evaluated':len(ids),'population_digest':digest(ids),
     'definition_fields_changed':changed_definition_fields,
     'status_transitions':[{'before':a,'after':b,'patients':n} for (a,b),n in sorted(Counter((r['before']['status'],r['after']['status']) for r in rows).items())],
     'change_counts':{k:sum(r[k] for r in rows) for k in ('criterion_value_changed','readiness_value_changed','entered_review','exited_review')},
     'patient_changes':rows,'code_identity':code_identity(),'new_paid_api_calls':0,'clinical_eligibility_established':False,'limits':LIMITS}
   with db:db.execute('INSERT OR IGNORE INTO comparisons VALUES (?,?)',(identity,zlib.compress(json.dumps(result,allow_nan=False).encode())))
  fresh=prepare(path,p)
  for side in versions:
   if any(C.receipt_id(fresh['versions'][side],pid,ic)!=keys[side][i] for i,pid in enumerate(ids)):raise ValueError('Protocol inputs changed during comparison')
  return {**{k:v for k,v in result.items() if k!='patient_changes'},'patients':sorted(result['patient_changes'],key=lambda r:(not r['criterion_value_changed'],r['patient_id']))[:preview_limit],
    'all_patient_changes_persisted':True,'comparison_cache_hit':bool(cached)}
 finally:db.close();source.close()


def inspect(path,key,patient):
 if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{64}',key) or not isinstance(patient,str):raise ValueError('Exact comparison and patient required')
 db=storage(path)
 try:raw=db.execute('SELECT payload FROM comparisons WHERE id=?',(key,)).fetchone()
 finally:db.close()
 if raw is None:raise ValueError('Unknown protocol comparison')
 result=json.loads(zlib.decompress(raw[0]))
 if result['code_identity']!=code_identity():raise ValueError('Protocol comparison code changed; rerun')
 row=next((r for r in result['patient_changes'] if r['patient_id']==patient),None)
 if row is None:raise ValueError('Patient not assessed in this comparison')
 receipts={side:C.inspect(path,row[side]['id']) for side in ('before','after')}
 if any(digest(receipts[s])!=row[s]['receipt_hash'] for s in receipts):raise ValueError('Protocol comparison receipt changed')
 return {'schema':VERSION,'synthetic':True,'comparison_id':key,'patient_change':row,'protocol_receipts':receipts,'limits':LIMITS}
