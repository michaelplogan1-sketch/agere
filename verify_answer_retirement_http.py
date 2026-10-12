"""Live preview/rejection checks; never retires a current live answer for a test."""
import hashlib,json,sqlite3
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from clinical_core.agere.active import stack_path
from clinical_core.agere.gateway import BudgetLedger

OUT=Path('artifacts/agere-answer-retirement-v1')
ITEM='5aa9d25af843a70adfaed327beb2355d6d014f36dc54136387f648f63d9c87c2'
def snapshot():
    with sqlite3.connect(stack_path()/'program-work.sqlite3') as db:
        tables=('work_items','work_events','capability_tasks','capability_events')
        result={}
        for name in tables:
            rows=sorted(db.execute('SELECT * FROM '+name).fetchall());result[name]={'count':len(rows),'digest':hashlib.sha256(json.dumps(rows).encode()).hexdigest()}
        return result

def main():
    before=snapshot();ledger=BudgetLedger(Path('artifacts/agere-full/provider-budget.sqlite3')).receipt()
    def api(route,body=None):
        with urlopen(Request('http://localhost:8885'+route,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'}),timeout=120) as response:return json.load(response)
    state=api('/api/stack/work/answers?item='+ITEM);assert state['answers'] and all(a['current'] for a in state['answers']) and not state['retirements']
    request={'item_id':ITEM,'expected_revision':state['revision'],'answer_ids':[state['answers'][0]['answer_id']],'replacement_ids':[], 'reason':'withdrawn_for_review','note':'Preview-only interface verification. No current answer is being retired.'}
    plan=api('/api/stack/work/answers/prepare',request);assert len(plan['downstream_tasks'])==1 and plan['reassessment_required']
    assert plan==api('/api/stack/work/answers/prepare',request)
    errors=[]
    for body,code,term in [({**request,'reason':'stale_dependencies'},400,'failed current verification'),({**request,'expected_revision':0+state['revision']+1},409,'changed')]:
        try:api('/api/stack/work/answers/prepare',body);raise AssertionError('Expected rejection')
        except HTTPError as error:
            text=error.read().decode();assert error.code==code and term in text;errors.append({'status':error.code,'error':text})
    after=snapshot();assert after==before
    assert BudgetLedger(Path('artifacts/agere-full/provider-budget.sqlite3')).receipt()==ledger
    with urlopen('http://localhost:8885/programs?program=functional_support_review') as response:html=response.read().decode()
    assert 'Manage attached machine answers' in html and 'Record retirement and require reassessment' in html
    (OUT/'live-preview.json').write_text(json.dumps({'state':state,'plan':plan,'rejections':errors,'before':before,'after':after,'ledger':ledger,'new_provider_calls':0,'live_retirements_applied':0,'preview_replay_exact':True,'ui_served':True},indent=2)+'\n')
    print(json.dumps({'live_preview':'passed','dependent_tasks':len(plan['downstream_tasks']),'live_mutations':0,'all_work_and_buyer_history_unchanged':True},indent=2))
if __name__=='__main__':main()
