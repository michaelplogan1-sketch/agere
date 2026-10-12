"""Verify the activated warehouse through actual local question and evidence APIs."""
import argparse,json,re,subprocess
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from clinical_core.agere.clinical.graph_workbench import requirement_examples


def main():
 p=argparse.ArgumentParser();p.add_argument('--build',type=Path,required=True);a=p.parse_args()
 out=a.build/'source-extension';v=json.loads((a.build/'source-extension-verification.json').read_text())
 def get(route,body=None):
  r=Request('http://127.0.0.1:8885'+route,data=json.dumps(body).encode() if body else None,headers={'Content-Type':'application/json'})
  with urlopen(r,timeout=600) as response:return response.read()
 def api(route,body=None):return json.loads(get(route,body))
 def write(name,value):(out/name).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
 meta=api('/api/stack/meta');assert meta['retrieval']['snapshot']==v['source_snapshot']
 assert not meta['model_roles']['trained_source_students']['attached']
 cases={r['contract']:r for r in v['contracts']};verified=[]
 for ex in requirement_examples():
  key=ex['graph']['nodes'][1]['parameters']['contract']
  if key not in cases:continue
  result=api('/api/stack/compose',{'question':ex['question']});assert result['new_paid_api_calls']==0
  r=result['results'][-1]['result'];assert r['patients_evaluated']==v['patients'] and r['status_counts']==cases[key]['states']
  row=next((x for x in r['patients'] if x['status']=='supported'),r['patients'][0])
  detail=api('/api/stack/requirements?id='+row['id']);assert detail['patient_id']==row['patient_id']
  write('http-'+key+'.json',result);write('http-'+key+'-patient.json',detail);verified.append(key)
 measure=api('/api/stack/measure?id='+v['measure_id']);assert measure['counts']==v['measure_counts']
 try:api('/api/stack/measure?id='+v['measure_id']+'&patient=OUTSIDE')
 except HTTPError as error:assert error.code==400
 else:raise AssertionError('Outside patient accepted')
 scripts=[]
 for route,name in [('/stack','stack'),('/measures','measures'),('/programs','programs')]:
  html=get(route).decode();code='\n'.join(re.findall(r'<script[^>]*>(.*?)</script>',html,re.S));file=out/(name+'-served.js');file.write_text(code)
  subprocess.run(['node','--check',str(file)],check=True);scripts.append(route)
 write('http-verification.json',{'passed':True,'synthetic':True,'source_snapshot':v['source_snapshot'],'patients':v['patients'],
       'full_population_questions':verified,'measure_counts':v['measure_counts'],'outside_patient_rejected':True,
       'served_javascript_checked':scripts,'old_students_transplanted':False,'new_paid_api_calls':0})
 print('Activated source, full-population questions, patient evidence, measure and served scripts verified.')


if __name__=='__main__':main()
