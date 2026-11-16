"""Predeclared G2 development probe; no retries, tuning, or automatic promotion."""
import json
import sqlite3
import time
import urllib.request
import urllib.error
from pathlib import Path
from clinical_core.agere.economics import usage_cost

OUT = Path('artifacts/agere-source-selection-v1/decision')
SCOPE = OUT.parent
LEDGER = Path('artifacts/agere-full/provider-budget.sqlite3')


def save(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def request(route, body):
    started = time.perf_counter()
    req = urllib.request.Request('http://localhost:8885/api/stack/' + route,
        data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=900) as response:
            return {'status': 'executed', 'response': json.load(response), 'seconds': time.perf_counter()-started}
    except urllib.error.HTTPError as exc:
        return {'status': 'failed', 'http_status': exc.code, 'error': exc.read().decode(), 'seconds':time.perf_counter()-started}
    except Exception as exc:
        return {'status':'failed', 'error':str(exc), 'seconds':time.perf_counter()-started}


def ledger():
    with sqlite3.connect('file:' + str(LEDGER.resolve()) + '?mode=ro', uri=True) as db:
        return {key:{'id':key,'provider':provider,'model':model,'status':status,'usage':json.loads(raw).get('usage') if raw else None,
            'estimate':usage_cost(model,json.loads(raw).get('usage')) if raw else None,'error':error,'seconds':seconds}
            for key,provider,model,status,raw,error,seconds in db.execute('SELECT id,provider,model,status,response,error,seconds FROM calls')}


def main():
    protocol=json.loads((OUT/'protocol.json').read_text())
    before=ledger(); prior=OUT/'ledger-before.json'
    if prior.exists(): raise ValueError('This diagnostic was already started; inspect saved failures/results. No blanket rerun.')
    save(prior,list(before.values()))
    for case in protocol['cases']:
        ident=case['id']; candidate=json.loads((SCOPE/(ident+'-http.json')).read_text())
        contexts=candidate['source_plan']['contexts']
        prep=request('context/from-question',{'patient':case['patient'],'question':case['question'],'selection':'schema_candidate'})
        save(OUT/(ident+'-prepare.json'),prep)
        assert prep['status']=='executed' and prep['response']['id']==candidate['id']
        if len(contexts)!=1:
            result={'status':'blocked','reason':'No complete-answer cross-group interpretation in the existing source-plan contract',
                'groups':len(contexts),'source_plan_id':candidate['source_plan']['plan']['id'],
                'cross_group_interpretation':candidate['source_plan']['plan']['cross_group_interpretation'],
                'inference_dispatched':False,'complete_answer_cost_usd':None}
        else:
            ctx=contexts[0]; preflight=request('context/plan',{'context_id':ctx['id']})
            save(OUT/(ident+'-preflight.json'),preflight)
            if preflight['status']!='executed' or not preflight['response']['can_dispatch']:
                result={'status':'blocked','reason':'Preflight failed','preflight':preflight}
            else:
                result=request('context/synthesize',{'context_id':ctx['id']})
                if result['status']=='executed':
                    answer=result['response']
                    assert answer['patient_id']==case['patient'] and answer['question'].startswith(case['question'])
                    assert answer['context_id']==ctx['id'] and not answer['clinical_execution_authorized']
        save(OUT/(ident+'-interpretation.json'),result)
        after=ledger(); save(OUT/'ledger-delta.json',[v for k,v in after.items() if k not in before])
        print(json.dumps({'case':ident,'status':result['status'],'seconds':result.get('seconds'),
            'new_calls':result.get('response',{}).get('new_paid_api_calls'),'error':result.get('error'),'reason':result.get('reason')}),flush=True)
    save(OUT/'execution.json',{'status':'finished','cases':len(protocol['cases']),'no_retries':True,'clinical_validated':False})

if __name__=='__main__':main()
