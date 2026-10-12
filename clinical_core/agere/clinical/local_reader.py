"""Offline Qwen challenger for free-concept clinical extraction; never default authority."""
from pathlib import Path
import json,os,time
from ...contracts import digest
from ...mlx_reader import MLXGenerator
from .facts import validate_model_facts,MODEL_TYPES,MODEL_ASSERTIONS
from .model_reader import INSTRUCTIONS,score,validate_cases

PROMPT=INSTRUCTIONS+''' Return only a compact JSON object with exactly label, evidence_ids, facts.
label must be yes, no, or unknown. evidence_ids must cite only the supplied evidence IDs;
use an empty array for unknown. Extract zero or one clinical fact into facts.
Each fact has exactly kind, concept, value, assertion, subject, source_id, quote.
kind must be one of: '''+', '.join(MODEL_TYPES)+'''.
assertion must be one of: '''+', '.join(MODEL_ASSERTIONS)+'''.
subject must be patient, other, or uncertain. concept and value are strings, not medical advice.
source_id must match the supplied evidence. quote must be a SHORT EXACT VERBATIM substring
supporting the fact. Include negation and attribution in the quote where needed. No markdown.
'''


def run(items,model_path,output):
    validate_cases(items)
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',HF_HUB_DISABLE_IMPLICIT_TOKEN='1')
    g=MLXGenerator(Path(model_path))  # Existing manifest/hash verification, no downloads.
    from mlx_lm import load,stream_generate
    from mlx_lm.sample_utils import make_sampler
    started=time.perf_counter()
    model,tokenizer=load(str(g.path),trust_remote_code=False,tokenizer_config={'local_files_only':True,'trust_remote_code':False})
    load_seconds=time.perf_counter()-started
    out=Path(output);out.mkdir(exist_ok=True,parents=True);results=[];traces=[]
    identity=digest({'manifest':g.manifest,'versions':g.versions,'prompt':PROMPT,'adapter':Path(__file__).read_text()})
    for c in items:
        public={k:c[k] for k in ('case_id','question','as_of','options','evidence')}
        key=digest({'model':identity,'input':public});path=out/(key+'.json')
        if path.exists():trace=json.loads(path.read_text());trace['cache_hit']=True
        else:
            prompt=tokenizer.apply_chat_template([{'role':'system','content':PROMPT},{'role':'user','content':json.dumps(public)}],tokenize=True,add_generation_prompt=True,enable_thinking=False)
            if len(prompt)>12000:raise ValueError('Input exceeds local pilot bound')
            t=time.perf_counter();parts=[];final=None
            for response in stream_generate(model,tokenizer,prompt=prompt,max_tokens=600,sampler=make_sampler(temp=0.0)):
                parts.append(response.text);final=response
            trace={'case_id':c['case_id'],'raw':''.join(parts),'cache_hit':False,'seconds':time.perf_counter()-t,
                   'model_identity':identity,'finish_reason':final.finish_reason if final else 'empty',
                   'prompt_tokens':final.prompt_tokens if final else len(prompt),'generation_tokens':final.generation_tokens if final else 0}
            path.write_text(json.dumps(trace,indent=2))
        a={'case_id':c['case_id'],'label':'unknown','evidence_ids':[],'probabilities':None,'extracted_facts':[],
           'rejected_facts':[],'unsupported_decision':False,'semantic_validation':'pending','contract_invalid':False}
        try:
            if trace['finish_reason']!='stop':raise ValueError('Incomplete response')
            v=json.loads(trace['raw'])
            if not isinstance(v,dict) or set(v)!={'label','evidence_ids','facts'}:raise ValueError('Invalid schema')
            if v['label'] not in c['options'] or not isinstance(v['evidence_ids'],list) or set(v['evidence_ids'])-{e['id'] for e in c['evidence']}:raise ValueError('Invalid label/source')
            if not isinstance(v['facts'],list) or len(v['facts'])>1:raise ValueError('Invalid fact list')
            a.update(label=v['label'],evidence_ids=v['evidence_ids'],unsupported_decision=v['label']!='unknown' and not v['evidence_ids'])
            for p in v['facts']:
                try:a['extracted_facts']+=validate_model_facts([p],c['evidence'])
                except ValueError as e:a['rejected_facts'].append({'proposal':p,'reason':str(e)})
        except (ValueError,TypeError,KeyError) as e:a.update(contract_invalid=True,error=str(e))
        results.append(a);traces.append(trace)
        print(json.dumps({'case':c['case_id'],'contract_valid':not a['contract_invalid'],'seconds':trace['seconds']}),flush=True)
    result={'status':'validated_response','provider':'qwen3-4b-local','model_identity':identity,'answers':results,
            'model_load_seconds':load_seconds,'generation_seconds':sum(t['seconds'] for t in traces if not t['cache_hit']),
            'cache_hits':sum(t['cache_hit'] for t in traces),'external_model_calls':0,'new_paid_api_usd':0,
            'decision_authority':False,'training_performed':False,'model_role':'Experimental local extraction challenger, not promoted'}
    (out/'result.json').write_text(json.dumps(result,indent=2));(out/'score.json').write_text(json.dumps(score(items,result),indent=2));return result
