"""Current synthetic patient: quoted rule context, budget and source checks."""
import argparse,json
from pathlib import Path
from urllib.request import Request,urlopen
from clinical_core.contracts import digest
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import context_builder as C

def main():
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();out=a.output
 def api(route,body=None):
  with urlopen(Request('http://127.0.0.1:8885'+route,data=json.dumps(body).encode() if body else None,headers={'Content-Type':'application/json'}),timeout=180) as r:return json.load(r)
 def write(name,row):(out/name).write_text(json.dumps(row,indent=2,allow_nan=False)+'\n')
 keys=json.loads((out/'http-verification.json').read_text())['packages']
 request={'patient':'P-2004','parameters':{'question':'Review received sources relevant to the quoted specification. Preserve its missing inputs and limitations.',
  'streams':['financial_screens'],'concepts':[],'contract':'','program_ids':[keys[1]],'max_bytes':10000}}
 result=api('/api/stack/context/prepare',request);write('http-rule-context.json',result);assert result['patients_evaluated']==1
 receipt=api('/api/stack/context?id='+result['patients'][0]['id']);write('http-rule-context-patient.json',receipt)
 rules=receipt['model_context']['versioned_program_context'];assert len(rules)==1 and rules[0]['id']==keys[1]
 assert rules[0]['input_bindings']['attribution']['kind']=='unbound' and rules[0]['semantic_review']['status']=='unreviewed'
 assert not receipt['dispatch']['clinical_decision_allowed'] and not receipt['clinical_eligibility_established']
 assert receipt['budget']['used']==len(C.encode(receipt['model_context']))<=10000
 replay=api('/api/stack/context/prepare',request);assert replay['counts']['cache_hits']==1
 small={**request,'parameters':{**request['parameters'],'max_bytes':2048}}
 overflow=api('/api/stack/context/prepare',small);write('http-rule-context-overflow.json',overflow)
 assert overflow['patients'][0]['status']=='protected_overflow' and not overflow['patients'][0]['dispatch']['source_review_allowed']
 # The production for_model entry point requires an actual provider tokenizer.
 # This engineering byte tokenizer only verifies source checks and guard wiring;
 # it is not used to claim native model-token counts.
 tokenizer=type('EngineeringByteTokenizer',(),{'encode':lambda self,s:list(s.encode())})()
 checked=C.for_model(stack_path(),receipt['id'],tokenizer,12000,1000)
 verification={'passed':True,'synthetic':True,'patient':receipt['patient_id'],'exact_package':keys[1],'context_status':receipt['status'],
  'context_utf8_bytes':receipt['budget']['used'],'source_units':receipt['budget']['selected_units'],'unbound_input_retained':True,'unreviewed_status_retained':True,
  'cache_replay':True,'overflow_blocks_dispatch':True,'source_hashes_checked_before_dispatch':True,'provider_native_tokens_measured':False,'new_paid_api_calls':0}
 write('context-verification.json',verification);print(json.dumps(verification,indent=2))

if __name__=='__main__':main()
