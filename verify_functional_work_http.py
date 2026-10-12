"""Current synthetic population -> work -> saved answer -> buyer review handoff."""
import json,sqlite3
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from clinical_core.agere.active import stack_path

OUT=Path('artifacts/agere-functional-work-v1')
def api(route,body=None):
    req=Request('http://localhost:8885'+route,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
    try:
        with urlopen(req,timeout=240) as response:return json.load(response)
    except HTTPError as error:
        print(error.read().decode(),flush=True);raise
def write(name,value):(OUT/(name+'.json')).write_text(json.dumps(value,indent=2)+'\n')
def main():
    program='functional_support_review';patient='SYN-69283717-971';actor='Automated synthetic functional-work verification'
    pop=api('/api/stack/functional-review');assert pop['counts']['source_review_candidates']==256
    plan={'interpretation':'Review dated functional/support source signals and prepare internal work across the whole population.',
        'coverage':'partial','unmet_requirements':['Clinical need, current goals and member benefits are unverified.'],
        'nodes':[{'id':'population','operation':'cohort','inputs':[],'parameters':{'specification':None},'purpose':'Full received population'},
                 {'id':'function','operation':'functional_review','inputs':['population'],'parameters':{'filter':'source_review_candidates'},'purpose':'Dated source review; no nomination remains unknown'},
                 {'id':'work','operation':'worklist','inputs':['function'],'parameters':{'program':program},'purpose':'Prepare internal source-bound review work'}]}
    graph=api('/api/stack/execute-plan',plan);write('population-graph',graph)
    # The service wraps graph results in the established stack response.
    result=graph.get('result',graph)
    if 'results' not in result:result=graph['answer']
    membership=result['results'][1]['result'];proposal=result['results'][2]['result']
    assert membership['counts']=={'evaluated':1614,'matched':256,'not_matched':0,'unknown':1358},membership
    assert proposal['counts']=={'selected':256,'assessed':256,'candidates':256,'missing_assessment':0}
    replay=api('/api/stack/execute-plan',plan);write('graph-replay',replay)
    replay_result=replay.get('result',replay)
    if 'results' not in replay_result:replay_result=replay['answer']
    assert replay_result['cache_hit'] and replay_result['new_paid_api_calls']==0
    full=api('/api/stack/work/prepare',{'program':program});write('full-population-assessment',full)
    assert full['counts']=={'selected':1614,'assessed':308,'candidates':256,'missing_assessment':1306}
    saved=api('/api/stack/work/save',{'proposal_id':proposal['proposal_id'],'actor':actor});write('population-work-save',saved)
    repeated=api('/api/stack/work/save',{'proposal_id':proposal['proposal_id'],'actor':actor})
    assert repeated['changes']['unchanged']==256
    board=api('/api/stack/work?program='+program);assert board['matched']==256;assert board['summary']['ever_offered']==0;write('work-board',board)
    single=api('/api/stack/functional-review/work',{'review_id':pop['id'],'patient_ids':[patient]});assert single['counts']['selected']==1
    try:api('/api/stack/functional-review/work',{'review_id':pop['id'],'patient_ids':[]});raise AssertionError('Empty explicit selection accepted')
    except HTTPError as error:assert error.code==400
    old=json.loads(Path('artifacts/agere-availability-synthesis-v1/answer.json').read_text())
    bound=api('/api/stack/work/prepare',{'program':program,'patient':patient,'synthesis_ids':[old['id']]});write('answer-bound-proposal',bound)
    assert bound['synthesis_assessments']==1;aid=bound['patients'][0]['assessment_id']
    assessment=api('/api/stack/capabilities/assessment?id='+aid)['assessment'];write('answer-bound-assessment',assessment)
    assert assessment['program']['id']==program and not assessment['clinical_eligibility_established']
    assert assessment['synthesis_context']['answers'][0]['answer_id']==old['id']
    assert len(assessment['evidence'])==4
    # Keep new machine evidence visible as a required reassessment, never fabricate a human review.
    api('/api/stack/work/save',{'proposal_id':bound['proposal_id'],'actor':actor})
    db=sqlite3.connect(stack_path()/'program-work.sqlite3')
    item=json.loads(db.execute('SELECT payload FROM work_items WHERE patient=? AND program=?',(patient,program)).fetchone()[0]);db.close()
    detail=api('/api/stack/work?item='+item['id']);write('demonstration-work',detail)
    assert item['state']=='review_needed' and not item['clinical_goal_verified']
    assert all(e['action'] in ('created','evidence_changed') for e in detail['history'])
    return finish_buyer(pop,result,board,patient,program,aid,item,old,actor)

def finish_buyer(pop,result,board,patient,program,aid,item,old,actor):
    old_plan=json.loads(Path('artifacts/agere-availability-synthesis-v1/buyer-plan.json').read_text())
    request={'buyer_id':'synthetic-functional-review','origin':{'kind':'assessment','id':aid,'finding_ids':[]},
        'need_tags':['functional_recovery','home_care'],
        'rationale':'Synthetic integration of a dated source-review assessment and saved machine answer. No current need, clinical recommendation, benefit eligibility or consent established.',
        'coverage':{k:old_plan['coverage'][k] for k in ('payer_type','plan_id','region','as_of','provenance','preferences')}}
    buyer=api('/api/stack/capabilities/prepare',request);write('buyer-plan',buyer)
    assert not any(o['clinical_execution_authorized'] for o in buyer['options'])
    option=next(o for o in buyer['options'] if o['capability']['id']=='functional-evidence-review')
    queue={'plan_id':buyer['id'],'option_id':option['id'],'actor':actor}
    task=api('/api/stack/capabilities/queue',queue)['task'];assert api('/api/stack/capabilities/queue',queue)['idempotent_replay']
    if task['state']=='queued_for_internal_review':
        task=api('/api/stack/capabilities/event',{'task_id':task['id'],'expected_revision':task['revision'],'request_id':'functional-assessment-packet-v1','action':'prepare_handoff','actor':actor,'note':'Prepare local evidence packet only. No human clinical review, care referral, enrollment or contact performed.'})['task']
    assert task['state']=='handoff_prepared_for_review';write('internal-task',task)
    availability=api('/api/stack/capabilities/availability?task_id='+task['id']);write('answer-availability',availability)
    assert availability['answers'][0]['answer_id']==old['id']
    result={'population_receipt':pop['id'],'population':1614,'source_candidates':256,'no_selected_sources':1306,
        'composed_unknown_membership':1358,'full_population_work_items':board['matched'],
        'graph_result_id':result['result_id'],'graph_replay':True,'work_save_idempotent':True,
        'patient_id':patient,'program':program,'assessment_id':aid,'work_item_id':item['id'],
        'saved_answer_id':old['id'],'buyer_plan_id':buyer['id'],'buyer_task_id':task['id'],
        'task_state':task['state'],'clinical_quality_validated':False,'clinical_eligibility_established':False,
        'human_reviews_created':0,'external_actions':0,'new_paid_api_calls':0}
    write('http-verification',result);print(json.dumps(result,indent=2))
if __name__=='__main__':
    import sys
    if '--resume-buyer' in sys.argv:
        read=lambda name:json.loads((OUT/(name+'.json')).read_text())
        a=read('answer-bound-assessment');g=read('population-graph');item=read('demonstration-work')['item']
        finish_buyer({'id':read('population-graph')['results'][1]['result']['functional_review_id']},g,read('work-board'),
            a['patient_id'],a['program']['id'],a['id'],item,json.loads(Path('artifacts/agere-availability-synthesis-v1/answer.json').read_text()),
            'Automated synthetic functional-work verification')
    else:main()
