"""Local integration of program evidence acquisition; no external API calls."""
import argparse,json,re,subprocess
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from clinical_core.agere.clinical import program_acquisition as A

def main():
 p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);a=p.parse_args();out=a.output
 expected=json.loads((out/'population/verification.json').read_text())
 def get(route,body=None):
  req=Request('http://127.0.0.1:8885'+route,data=json.dumps(body).encode() if body else None,headers={'Content-Type':'application/json'})
  with urlopen(req,timeout=300) as r:return r.read()
 def api(route,body=None):return json.loads(get(route,body))
 def write(name,obj):(out/name).write_text(json.dumps(obj,indent=2,allow_nan=False)+'\n')
 answer=api('/api/stack/compose',{'question':A.question(expected['package'])});write('http-question.json',answer);assert answer['new_paid_api_calls']==0
 r=answer['results'][-1]['result'];assert r['counts']==expected['counts'];page=api('/api/stack/program-acquisition?id='+r['id']);assert page['items'][0]['scope']=='specification';write('http-page.json',page)
 for state,pid in expected['samples'].items():
  data=api('/api/stack/program-acquisition?id='+r['id']+'&patient='+pid);assert data['resolution']['patient_id']==pid;write('http-'+state+'.json',data)
 try:api('/api/stack/program-acquisition?id='+r['id']+'&patient=OUTSIDE')
 except HTTPError as e:assert e.code==400
 else:raise AssertionError('Outside patient accepted')
 syntax=[]
 for route,name in [('/stack','stack'),('/program-documents','documents'),('/programs','work')]:
  html=get(route).decode();scripts='\n'.join(re.findall(r'<script[^>]*>(.*?)</script>',html,re.S));file=out/(name+'-syntax.js');file.write_text(scripts);subprocess.run(['node','--check',str(file)],check=True);syntax.append(route)
 write('http-verification.json',{'passed':True,'synthetic':True,'patients':r['patients_evaluated'],'counts':r['counts'],'patient_inspections':expected['samples'],'outside_patient_rejected':True,'javascript_syntax':syntax,'new_paid_api_calls':0})
 print('Question, paged queue, all three evidence states, outside-patient rejection and served JavaScript verified.')

if __name__=='__main__':main()
