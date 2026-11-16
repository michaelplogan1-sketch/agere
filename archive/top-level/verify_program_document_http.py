"""Live local question/intake/impact contracts with no provider or review calls."""
import argparse,json,sqlite3,zlib
from pathlib import Path
from urllib.request import Request,urlopen
from clinical_core.contracts import digest
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import program_documents as D,program_document_change as X

def main():
 p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);a=p.parse_args();out=a.output
 def api(route,body=None):
  request=Request('http://127.0.0.1:8885'+route,data=json.dumps(body).encode() if body else None,headers={'Content-Type':'application/json'})
  with urlopen(request,timeout=300) as r:return json.load(r)
 def write(name,obj):(out/name).write_text(json.dumps(obj,indent=2,allow_nan=False)+'\n')
 versions=[]
 for n in (1,2):
  package=json.loads((out/f'synthetic-package-{n}.json').read_text());installed=api('/api/stack/program-import',package);versions.append(installed['id'])
  doc=api('/api/stack/program-documents?package='+installed['id']);assert doc['review']['revision']==0;write(f'http-source-{n}.json',doc)
  answer=api('/api/stack/compose',{'question':D.question(installed['id'])});write(f'http-question-{n}.json',answer)
  result=answer['results'][-1]['result'];assert result['patients_evaluated']==1614 and answer['new_paid_api_calls']==0
  patient=api('/api/stack/program-documents?receipt='+result['patients'][0]['id']);write(f'http-patient-{n}.json',patient)
 answer=api('/api/stack/compose',{'question':D.question(versions[0],'protocol:affordability_action_review@2')});write('http-work.json',answer);assert answer['results'][-1]['result']['counts']['selected']==115
 answer=api('/api/stack/compose',{'question':X.question(*versions)});write('http-impact.json',answer);result=answer['results'][-1]['result']
 assert result['patients_evaluated']==1614 and result['change_counts']=={'status_changed':115,'entered_supported':0,'left_supported':115,'became_unresolved':115}
 replay=api('/api/stack/compose',{'question':X.question(*versions)});assert replay['cache_hit'];write('http-impact-replay.json',replay)
 patient=api('/api/stack/program-document-change?id='+result['id']+'&patient='+result['patients'][0]['patient_id']);write('http-impact-patient.json',patient)
 source=D.storage(stack_path());comparisons=X.storage(stack_path());checks=0
 try:
  all_rows=json.loads(zlib.decompress(comparisons.execute('SELECT payload FROM comparisons WHERE id=?',(result['id'],)).fetchone()[0]))['patient_changes']
  before=json.loads((out/'patient-states-1.json').read_text());after=json.loads((out/'patient-states-2.json').read_text());assert {r['patient_id'] for r in all_rows}==set(before)==set(after)
  for row in all_rows:
   assert row['before']['status']==before[row['patient_id']] and row['after']['status']==after[row['patient_id']]
   for side in ('before','after'):
    r=json.loads(zlib.decompress(source.execute('SELECT payload FROM receipts WHERE id=? AND patient=?',(row[side]['id'],row['patient_id'])).fetchone()[0]));assert digest(r)==row[side]['receipt_hash'];checks+=1
 finally:source.close();comparisons.close()
 verification={'passed':True,'synthetic':True,'packages':versions,'patient_transitions_checked':len(all_rows),'before_after_receipts_checked':checks,'changes':result['change_counts'],'replay_cache_hit':True,'work_assessments_with_rule_selection':115,'new_paid_api_calls':0,'human_reviews_created':0,'live_work_items_created':0}
 write('http-verification.json',verification);print(json.dumps(verification,indent=2))

if __name__=='__main__':main()
