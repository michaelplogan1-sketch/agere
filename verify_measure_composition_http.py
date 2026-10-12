"""Real loopback question, measure inspection and served page checks."""
import argparse,json,re,subprocess
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from clinical_core.agere.clinical import measure_composition as M

def main():
 p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);a=p.parse_args();out=a.output;v=json.loads((out/'verification.json').read_text())
 def get(route,body=None):
  req=Request('http://127.0.0.1:8885'+route,data=json.dumps(body).encode() if body else None,headers={'Content-Type':'application/json'})
  with urlopen(req,timeout=600) as r:return r.read()
 def api(route,body=None):return json.loads(get(route,body))
 def write(name,obj):(out/name).write_text(json.dumps(obj,indent=2,allow_nan=False)+'\n')
 result=api('/api/stack/compose',{'question':M.question(v['component_packages']['denominator'],v['component_packages']['numerator'])});write('http-unbound-question.json',result);assert result['results'][-1]['result']['counts']==v['unbound_counts'];assert result['new_paid_api_calls']==0
 descriptive=api('/api/stack/execute-plan',json.loads((out/'descriptive-graph.json').read_text()));write('http-descriptive-question.json',descriptive);r=descriptive['results'][-1]['result'];assert r['counts']==v['descriptive_counts'] and r['sensitivity_bounds']==v['logical_bounds']
 for state,pid in v['samples'].items():
  row=api('/api/stack/measure?id='+r['id']+'&patient='+pid);assert row['patient']['patient_id']==pid;write('http-patient-'+state+'.json',row)
 try:api('/api/stack/measure?id='+r['id']+'&patient=OUTSIDE')
 except HTTPError as e:assert e.code==400
 else:raise AssertionError('Outside patient accepted')
 syntax=[]
 for route,name in [('/stack','stack'),('/measures','measure'),('/programs','work')]:
  html=get(route).decode();scripts='\n'.join(re.findall(r'<script[^>]*>(.*?)</script>',html,re.S));file=out/(name+'-syntax.js');file.write_text(scripts);subprocess.run(['node','--check',str(file)],check=True);syntax.append(route)
 write('http-verification.json',{'passed':True,'synthetic':True,'patients':r['patients_evaluated'],'counts':r['counts'],'logical_bounds':r['sensitivity_bounds'],'patient_inspections':v['samples'],'outside_patient_rejected':True,'javascript_syntax':syntax,'new_paid_api_calls':0});print('Live measure question, complete component populations, patient receipts, guard and served scripts passed.')

if __name__=='__main__':main()
