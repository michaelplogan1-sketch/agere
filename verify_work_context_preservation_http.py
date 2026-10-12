"""Verify the existing real synthetic functional queue retains its saved answer."""
import hashlib,json,sqlite3
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from clinical_core.agere.active import stack_path
from clinical_core.agere.gateway import BudgetLedger

OUT=Path('artifacts/agere-work-context-preservation-v1')
PRIOR=Path('artifacts/agere-functional-work-v1')
def api(route,body=None):
    request=Request('http://localhost:8885'+route,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
    with urlopen(request,timeout=240) as r:return json.load(r)
def write(name,value):(OUT/(name+'.json')).write_text(json.dumps(value,indent=2)+'\n')
def snapshot():
    with sqlite3.connect(stack_path()/'program-work.sqlite3') as db:
        rows=list(db.execute("SELECT id,payload FROM work_items WHERE program='functional_support_review' ORDER BY id"))
        events=list(db.execute("SELECT item,revision,payload FROM work_events WHERE item IN (SELECT id FROM work_items WHERE program='functional_support_review') ORDER BY item,revision"))
    return {'items':len(rows),'item_digest':hashlib.sha256(json.dumps(rows).encode()).hexdigest(),
        'events':len(events),'event_digest':hashlib.sha256(json.dumps(events).encode()).hexdigest()}
def main():
    before=snapshot();write('queue-before',before)
    ledger=BudgetLedger(Path('artifacts/agere-full/provider-budget.sqlite3')).receipt();write('ledger-before',ledger)
    old=json.loads((PRIOR/'full-population-assessment.json').read_text())
    try:
        api('/api/stack/work/save',{'proposal_id':old['proposal_id'],'actor':'Automated synthetic stale-refresh check'})
        raise AssertionError('Old answer-dropping proposal was accepted')
    except HTTPError as e:
        message=e.read().decode();assert e.code==409 and 'Attached machine answers would be lost' in message,(e.code,message)
        write('stale-save-rejection',{'status':e.code,'reason':'Attached machine answers would be lost; prepare a current proposal'})
    assert snapshot()==before
    proposal=api('/api/stack/work/prepare',{'program':'functional_support_review'});write('refresh-proposal',proposal)
    assert proposal['counts']=={'selected':1614,'assessed':308,'candidates':256,'missing_assessment':1306}
    assert proposal['synthesis_assessments']==1
    saved=api('/api/stack/work/save',{'proposal_id':proposal['proposal_id'],'actor':'Automated synthetic attachment-preservation check'});write('refresh-save',saved)
    assert saved['changes']=={'created':0,'unchanged':256,'evidence_changed':0,'reopened':0,'noncandidate_without_work':1358},saved
    after=snapshot();assert after==before;write('queue-after',after)
    item_id='5aa9d25af843a70adfaed327beb2355d6d014f36dc54136387f648f63d9c87c2'
    item=api('/api/stack/work?item='+item_id);write('attached-work',item)
    assert item['assessment']['synthesis_context']['answers'][0]['answer_id']=='4937dbd744271141f3ed99482cef4fce506c59eb3295096319c6841e524dd9fe'
    assert item['assessment']['id']=='5278761a50013cbde1d7e9f5d123415b9bfb49314c520ab340a002a1c9a31ee3'
    assert item['item']['state']=='review_needed'
    ledger_after=BudgetLedger(Path('artifacts/agere-full/provider-budget.sqlite3')).receipt();assert ledger_after==ledger;write('ledger-after',ledger_after)
    result={'old_proposal_rejected':True,'population':1614,'work_items_unchanged':256,'unchanged_work_and_event_digest':True,
        'saved_machine_contexts_retained':1,'proposal_id':proposal['proposal_id'],'work_item_id':item_id,
        'assessment_id':item['assessment']['id'],'saved_answer_id':item['assessment']['synthesis_context']['answers'][0]['answer_id'],
        'work_state':'review_needed','human_reviews_created':0,'new_paid_api_calls':0,'external_actions':0}
    write('http-verification',result);print(json.dumps(result,indent=2))
if __name__=='__main__':main()
