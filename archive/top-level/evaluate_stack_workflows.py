"""Development workflow execution against actual localhost APIs, with exact traces.

Targets stay in the evaluator. No expectations, hidden truth or evaluation labels
are passed to runtime. No retries after HTTP/provider errors. Reopen saved traces
explicitly; a new run directory is required for a changed implementation.
"""
import argparse,hashlib,json,time,urllib.error,urllib.request
from pathlib import Path


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n');temp.replace(path)

class Run:
    def __init__(self,output,base):self.output=Path(output);self.base=base;self.traces=[]
    def call(self,case,stage,route,body=None):
        target=self.output/case/(stage+'.json')
        request={'route':route,'body':body}
        if target.exists():
            out=json.loads(target.read_text())
            if out['request']!=request:raise ValueError('Saved trace request differs; use a new run directory')
            return out
        start=time.perf_counter()
        try:
            req=urllib.request.Request(self.base+'/api/stack/'+route,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
            with urllib.request.urlopen(req,timeout=600) as response:result=json.load(response)
            out={'status':'executed','response':result,'http_status':200}
        except urllib.error.HTTPError as exc:
            out={'status':'runtime_error','http_status':exc.code,'error':exc.read().decode()[:1500]}
        except (TimeoutError,urllib.error.URLError) as exc:
            out={'status':'transport_error','error':str(exc),'outcome_unknown':True}
        out.update(request=request,elapsed_seconds=round(time.perf_counter()-start,4),stage=stage,case_id=case)
        write(target,out);print(json.dumps({k:out[k] for k in ('case_id','stage','status','elapsed_seconds')},sort_keys=True),flush=True)
        return out


def matching(run,case,asof):
    key=case['id'];actor='Agere synthetic workflow evaluation';buyer='workflow-eval-'+key
    proposal=json.loads((run.output/key/'diagnostic_work.json').read_text())
    if proposal['status']!='executed' or not proposal['response'].get('patients'):
        write(run.output/key/'matching.json',{'status':'upstream_unavailable','stage':'matching','case_id':key,'reason':'No diagnostic assessment origin; not substituted with another patient or source.'});return
    assessment=proposal['response']['patients'][0]['assessment_id']
    tag=case['diagnostic_need_tag']
    cap={'id':'available','name':'Synthetic evidence review','description':'Local synthetic evidence review fixture only. No real clinical service, contact or treatment.',
         'need_tags':[tag],'modality':'virtual','regions':[],'payer_types':[],'plan_ids':[],'pathway_ids':[],
         'owner':{'kind':'agent','id':'synthetic-evidence-reviewer'},'available':True,'capacity_slots':None,'capacity_as_of':asof,'capacity_until':asof,
         'effective_start':'2029-01-01','effective_end':'2029-12-31','prerequisites':['Confirm current goals, consent, clinical appropriateness and benefits.'],
         'provenance':'Assistant-authored synthetic software test fixture; not actual buyer inventory.'}
    caps=[cap,{**cap,'id':'unavailable','available':False},{**cap,'id':'wrong-plan','plan_ids':['OTHER-SYNTHETIC-PLAN']},{**cap,'id':'unrelated','need_tags':['unrelated_test_need']}]
    saved=run.call(key,'buyer_fixture','capabilities/save',{'buyer_id':buyer,'expected_revision':0,'actor':actor,'capabilities':caps})
    if saved['status']!='executed':return
    request={'buyer_id':buyer,'origin':{'kind':'assessment','id':assessment,'finding_ids':[]},'need_tags':[tag],
      'rationale':'Engineering diagnostic: manually supplied tag, not an inferred clinical indication. This branch is not a continuation of a saved answer.',
      'coverage':{'payer_type':'unknown','plan_id':'SYNTHETIC-PLAN','region':'','as_of':asof,'provenance':'Deliberately unverified synthetic coverage input for the constraint test.','preferences':'Unknown; no permission or need established.'}}
    plan=run.call(key,'matching','capabilities/prepare',request)
    if plan['status']!='executed':return
    p=plan['response'];option=next(o for o in p['options'] if o['capability']['id']=='available')
    for o in p['options']:
        if o['capability']['id'] in ('unavailable','wrong-plan'):
            run.call(key,'reject_'+o['capability']['id'],'capabilities/queue',{'plan_id':p['id'],'option_id':o['id'],'actor':actor})
    task=run.call(key,'work_followthrough','capabilities/queue',{'plan_id':p['id'],'option_id':option['id'],'actor':actor})
    if task['status']=='executed':
        t=task['response']['task']
        run.call(key,'handoff','capabilities/event',{'task_id':t['id'],'expected_revision':t['revision'],'request_id':'workflow-eval-handoff-'+key,
          'action':'prepare_handoff','actor':actor,'note':'Synthetic internal evidence packet only. No patient contact, care, consent, benefits or human review is asserted.'})

def connected_work(run,case,asof):
    """Exercise the existing answer-origin API, retaining explicit user-choice tags."""
    key=case['id'];file=run.output/key/'source_interpretation.json'
    source=json.loads(file.read_text()) if file.exists() else {'status':'not_run'}
    if source['status']!='executed':
        write(run.output/key/'connected_matching.json',{'status':'upstream_unavailable','reason':'No executed original-question source answer. No alternative answer substituted.'});return
    answer=source['response'];assert answer['question']==case['question'] and answer['patient_id']==case['patient']
    origin={'kind':'answer','id':answer['id'],'finding_ids':[f['id'] for f in answer['findings']]}
    actor='Agere synthetic workflow evaluation'
    matched=run.call(key,'connected_matching','capabilities/prepare',{'buyer_id':'workflow-eval-'+key,'origin':origin,
        'need_tags':[case['diagnostic_need_tag']],
        'rationale':'Explicit evaluator-selected service tag for internal evidence review, not an inferred clinical indication. Carry every finding and the original question; no clinician review, eligibility or service suitability is asserted.',
        'coverage':{'payer_type':'unknown','plan_id':'SYNTHETIC-PLAN','region':'','as_of':asof,
                    'provenance':'Unverified synthetic coverage input.','preferences':'Unconfirmed; no contact or care permission.'}})
    if matched['status']!='executed':return
    plan=matched['response'];option=next(o for o in plan['options'] if o['capability']['id']=='available')
    task=run.call(key,'connected_queue','capabilities/queue',{'plan_id':plan['id'],'option_id':option['id'],'actor':actor})
    if task['status']!='executed':return
    saved=task['response']['task']
    run.call(key,'connected_handoff','capabilities/event',{'task_id':saved['id'],'expected_revision':saved['revision'],
        'request_id':'workflow-eval-connected-'+key,'action':'prepare_handoff','actor':actor,
        'note':'Carry the original question and all saved findings into an internal evidence-review packet. Service tag was explicitly selected. No care, human review or external action asserted.'})

def main():
    p=argparse.ArgumentParser();p.add_argument('--plan',default='research/agere/workflow-evaluation-v1/plan.json');p.add_argument('--output',default='artifacts/agere-workflow-evaluation-v1/baseline');p.add_argument('--base',default='http://localhost:8885');p.add_argument('--phase',choices=['scope','planning','interpretation','matching','reader_baselines','connected_work','all'],default='scope');p.add_argument('--case',choices=['medication','respiratory','cardiometabolic','functional','appointment','payer']);args=p.parse_args()
    plan=json.loads(Path(args.plan).read_text());run=Run(args.output,args.base)
    manifest={'plan_sha256':hashlib.sha256(Path(args.plan).read_bytes()).hexdigest(),'plan':plan,'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    # Stage traces are immutable; execution manifest revisions disclose runner updates.
    write(run.output/'manifests'/(hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()+'.json'),manifest)
    for case in plan['cases']:
        if args.case and case['id']!=args.case:continue
        key=case['id'];q={'question':case['question'],'patient':case['patient']}
        if args.phase in ('reader_baselines','all'):
            # These existing, frozen exact-statement comparisons are separate
            # diagnostics, not this question's selected evidence or new targets.
            rows=json.loads(Path('artifacts/agere-reader-comparison-v1/case-results.json').read_text())
            for row in rows:
                if row['workflow']==key:
                    run.call(key,'reader_'+row['case_id'],'reader-comparison/inspect',{'id':row['comparison_id']})
        if args.phase in ('scope','all'):
            run.call(key,'retrieval','retrieval-comparison/inspect',{'id':case['comparison_id']})
            run.call(key,'coverage','retrieval-comparison/coverage',{'id':case['comparison_id']})
            context=run.call(key,'source_scope','context/from-question',q)
            if context['status']=='executed' and context['response'].get('context_id'):
                run.call(key,'interpretation_preflight','context/plan',{'context_id':context['response']['context_id']})
            run.call(key,'diagnostic_work','work/prepare',{'program':case['diagnostic_program'],'patient':case['patient']})
        if args.phase in ('matching','all'):
            matching(run,case,plan['snapshot']['as_of'])
        if args.phase in ('planning','all'):
            run.call(key,'planning','compose',{'question':case['planning_question']})
        if args.phase in ('interpretation','all'):
            context=json.loads((run.output/key/'source_scope.json').read_text())
            preflight=json.loads((run.output/key/'interpretation_preflight.json').read_text())
            if preflight['status']=='executed':
                # This is the application's exact context, without narrowing/retry.
                run.call(key,'source_interpretation','context/synthesize',{'context_id':context['response']['context_id']})
        if args.phase in ('connected_work','all'):
            connected_work(run,case,plan['snapshot']['as_of'])
    print('Phase complete; failed traces are retained and not retried.',flush=True)

if __name__=='__main__':main()
