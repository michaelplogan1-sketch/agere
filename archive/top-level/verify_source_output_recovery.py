"""Read-only live API checks; never dispatches a valid recovery request."""
import json
import sqlite3
import urllib.request
import urllib.error
from pathlib import Path
from verify_complete_plan_http import OUT,BASE

CID='7acb0ab626bab79239003fb60611f37abdd513a139f6e9289cb6ca2b92700fa6'
GROUP1='0d43f81aabea63e96bf9ce92af0e32e00152121e1cfac0ad2c5523b3efd5f71b'

def request(route,body=None):
    req=urllib.request.Request(BASE+route,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=90) as response:return response.status,json.load(response)
    except urllib.error.HTTPError as error:return error.code,json.loads(error.read())

def main():
    ledger=Path('artifacts/agere-full/provider-budget.sqlite3')
    def snapshot():
        with sqlite3.connect(ledger) as db:return dict(db.execute('SELECT id,status FROM calls'))
    before=snapshot()
    code,plan=request('context/output-recovery-plan',{'context_id':CID})
    assert code==200 and len(plan['failures'])==1 and plan['source_units']==19
    assert plan['failures'][0]['corrected_max_tokens']==8192 and not plan['saved_answer_available']
    (OUT/'output-recovery-plan-current.json').write_text(json.dumps(plan,indent=2)+'\n')
    results=[]
    for route,body in [('context/output-recover',{'context_id':CID,'recovery_id':'0'*64}),
            ('context/output-recovery-plan',{'context_id':GROUP1}),
            ('context/output-recovery-plan',{'context_id':CID,'max_tokens':999999})]:
        status,result=request(route,body)
        assert status==400
        results.append({'route':route,'status':status,'result':result})
    status,current=request('context/source-stage?context_id='+CID)
    assert status==200 and current['answer'] is None
    status,saved=request('context/source-stage?context_id='+GROUP1)
    assert status==200 and saved['answer']['coverage']['received_units']==39
    # Replay a previously complete original-question answer using current dispatch.
    card=json.loads(Path('artifacts/agere-remaining-questions-v1/cardiometabolic/canonical-answer.json').read_text())
    status,replayed=request('case-synthesis?id='+card['id'])
    assert status==200 and replayed==card
    after=snapshot();assert before==after
    report={'status':'passed','recovery_plan_id':plan['id'],'negative_api_checks':results,
        'incomplete_group_not_promoted':True,'prior_group_reopened':True,'complete_cardiometabolic_answer_unchanged':True,
        'provider_calls_added':0,'live_recovery_executed':False,'pending_transfer_approval':True}
    (OUT/'api-verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))

if __name__=='__main__':main()
