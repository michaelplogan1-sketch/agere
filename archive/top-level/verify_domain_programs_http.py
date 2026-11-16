"""Local source populations -> registered work -> saved answer -> buyer handoff."""
import hashlib,json,sqlite3
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from clinical_core.agere.active import stack_path
from clinical_core.agere.gateway import BudgetLedger

OUT=Path('artifacts/agere-domain-programs-v1');PRIOR=Path('artifacts/agere-domain-review-v1')
ACTOR='Automated synthetic domain-program verification'
def api(route,body=None):
    request=Request('http://localhost:8885'+route,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
    with urlopen(request,timeout=300) as response:return json.load(response)
def write(name,value):(OUT/(name+'.json')).write_text(json.dumps(value,indent=2)+'\n')
def snapshot():
    with sqlite3.connect(stack_path()/'program-work.sqlite3') as db:
        rows=list(db.execute("SELECT id,payload FROM work_items WHERE program='functional_support_review' ORDER BY id"))
        events=list(db.execute("SELECT item,revision,payload FROM work_events WHERE item IN (SELECT id FROM work_items WHERE program='functional_support_review') ORDER BY item,revision"))
    return {'items':len(rows),'item_digest':hashlib.sha256(json.dumps(rows).encode()).hexdigest(),'events':len(events),'event_digest':hashlib.sha256(json.dumps(events).encode()).hexdigest()}
def node(id,operation,inputs,parameters):return {'id':id,'operation':operation,'inputs':inputs,'parameters':parameters,'purpose':id}
def main():
    before=snapshot();write('functional-before',before);assert before==json.loads(Path('artifacts/agere-work-context-preservation-v1/queue-after.json').read_text())
    ledger=BudgetLedger(Path('artifacts/agere-full/provider-budget.sqlite3')).receipt();write('ledger-before',ledger);results=[]
    prior=json.loads((PRIOR/'http-verification.json').read_text())
    for case in prior:
        domain=case['domain'];program=domain+'_source_review';patient=case['patient_id'];expected=100 if domain=='respiratory' else 24
        graph={'interpretation':'Source-bound '+domain+' review, not a current clinical or benefit determination.','coverage':'partial','unmet_requirements':['Current clinical status, service indication, goals, consent and member benefits unverified.'],
            'nodes':[node('population','cohort',[],{'specification':None}),node('domain','domain_review',['population'],{'domain':domain,'filter':'source_review_candidates'}),node('work','worklist',['domain'],{'program':program})]}
        result=api('/api/stack/execute-plan',graph);write(domain+'-graph',result)
        if not (OUT/(domain+'-initial-graph.json')).exists():write(domain+'-initial-graph',result)
        initial=json.loads((OUT/(domain+'-initial-graph.json')).read_text())['results'][2]['result']
        selected=result['results'][1]['result'];proposal=result['results'][2]['result']
        assert selected['counts']['matched']==expected and selected['counts']['unknown']==1614-expected
        assert proposal['counts']['selected']==expected and proposal['counts']['candidates']==expected
        assert api('/api/stack/execute-plan',graph)['cache_hit']
        saved=api('/api/stack/work/save',{'proposal_id':proposal['proposal_id'],'actor':ACTOR});write(domain+'-initial-save',saved)
        assert saved['changes']['created']+saved['changes']['unchanged']==expected
        replay=api('/api/stack/work/save',{'proposal_id':proposal['proposal_id'],'actor':ACTOR});assert replay['changes']['unchanged']==expected
        attached=api('/api/stack/work/prepare',{'program':program,'patient':patient,'synthesis_ids':[case['answer_id']]});write(domain+'-attached-proposal',attached)
        assert attached['synthesis_assessments']==1 and attached['counts']['selected']==1
        api('/api/stack/work/save',{'proposal_id':attached['proposal_id'],'actor':ACTOR})
        board=api('/api/stack/work?program='+program);write(domain+'-board',board)
        items=list(board['items'])
        while len(items)<board['matched']:items.extend(api('/api/stack/work?program='+program+'&offset='+str(len(items)))['items'])
        item=next(i for i in items if i['patient_id']==patient);inspected=api('/api/stack/work?item='+item['id']);write(domain+'-work',inspected)
        assert inspected['assessment']['synthesis_context']['answers'][0]['answer_id']==case['answer_id']
        assert item['state']=='review_needed' and item['owner'] is None and not item['clinical_goal_verified']
        assert board['summary']['work_items']==expected and board['summary']['unassigned']==expected
        assert set(board['summary']['state_counts'])=={'review_needed'}
        try:
            api('/api/stack/work/save',{'proposal_id':initial['proposal_id'],'actor':ACTOR});raise AssertionError('Answer-dropping proposal accepted')
        except HTTPError as e:assert e.code==409 and 'Attached machine answers would be lost' in e.read().decode()
        refresh=api('/api/stack/work/prepare',{'program':program});write(domain+'-refresh-proposal',refresh)
        assert refresh['counts']=={'selected':1614,'assessed':expected,'candidates':expected,'missing_assessment':1614-expected} and refresh['synthesis_assessments']==1
        resaved=api('/api/stack/work/save',{'proposal_id':refresh['proposal_id'],'actor':ACTOR});write(domain+'-refresh-save',resaved)
        assert resaved['changes']['unchanged']==expected and resaved['changes']['evidence_changed']==0
        bound=api('/api/stack/domain-review/work',{'review_id':case['review_id'],'patient_ids':[patient]});assert bound['patients'][0]['assessment_id']==item['assessment_id'];write(domain+'-source-bound-proposal',bound)
        request={'buyer_id':'synthetic-domain-source-review','origin':{'kind':'assessment','id':item['assessment_id'],'finding_ids':[]},'need_tags':['respiratory'] if domain=='respiratory' else ['behavioral_health'],
            'rationale':'Synthetic internal evidence review from dated received accounts. The saved answer is unvalidated context; any identity hypothesis is not endorsed. No clinical service indication, benefit entitlement, current assessment or consent inferred.',
            'coverage':{'payer_type':'unknown','plan_id':'','region':'','as_of':'2029-08-15','provenance':'Member-specific benefit evidence is unresolved in this synthetic fixture','preferences':'Patient goals and consent unverified'}}
        plan=api('/api/stack/capabilities/prepare',request);write(domain+'-buyer-plan',plan)
        assert not any(o['clinical_execution_authorized'] for o in plan['options'])
        option=next(o for o in plan['options'] if o['capability']['id']==domain+'-evidence-review')
        q={'plan_id':plan['id'],'option_id':option['id'],'actor':ACTOR};task=api('/api/stack/capabilities/queue',q)['task'];assert api('/api/stack/capabilities/queue',q)['idempotent_replay']
        if task['state']=='queued_for_internal_review':task=api('/api/stack/capabilities/event',{'task_id':task['id'],'expected_revision':task['revision'],'request_id':domain+'-program-packet-v1','action':'prepare_handoff','actor':ACTOR,'note':'Internal synthetic evidence packet only; no human clinical review or external action. Machine identity hypotheses remain unvalidated, not accepted clinical facts.'})['task']
        write(domain+'-task',task);assert task['state']=='handoff_prepared_for_review'
        availability=api('/api/stack/capabilities/availability?task_id='+task['id']);write(domain+'-availability',availability)
        assert availability['answers'][0]['answer_id']==case['answer_id']
        row={'domain':domain,'program':program,'population':1614,'source_review_candidates':expected,'unknown_need_without_selected_accounts':1614-expected,'work_items':expected,'unassigned_review_needed':expected,'full_refresh_unchanged':expected,'saved_answer_retained':case['answer_id'],'stale_drop_rejected':True,'patient_id':patient,'work_item_id':item['id'],'assessment_id':item['assessment_id'],'buyer_task_id':task['id'],'buyer_plan_id':plan['id'],'task_state':task['state'],'graph_replay':True,'human_reviews_created':0,'clinical_execution_authorized':False,'external_actions':0}
        results.append(row);print(json.dumps(row),flush=True)
    write('http-verification',results);after=snapshot();write('functional-after',after);assert after==before
    ledger_after=BudgetLedger(Path('artifacts/agere-full/provider-budget.sqlite3')).receipt();write('ledger-after',ledger_after);assert ledger_after==ledger
    print('Domain programs verified; functional work and provider ledger unchanged.',flush=True)
if __name__=='__main__':main()
