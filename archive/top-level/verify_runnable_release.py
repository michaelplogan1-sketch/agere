"""Actual archived server checks. Never runs model inference or touches original work."""
import hashlib
import json
import os
import sqlite3
import sys
import urllib.request
import urllib.error
from pathlib import Path
import verify_scoped_review_compiler as H

OUT=Path(__file__).resolve().parent/'artifacts/agere-runnable-release-v1'
BASE=OUT
OUT=OUT/os.environ.get('AGERE_RELEASE_CHECKS','http-v2')
OUT.mkdir(exist_ok=True)
INDIVIDUAL='f0f91f6f87e8520fb1eb9442e8376692cb99839f549ffe7df81d74b4c5e54a5c'
POPULATION='47b22d8abb7edbcff4b7ac1a630495bf4d4957f2a90fd33c8fb53658d99cadb2'
TASK='ff02281ddee2bdde8f127c8416626f38ffc5b8c06055707bf9da22c9574ff41b'


def save(name,data):
    p=OUT/(name+'.json')
    with p.open('x') as f:json.dump(data,f,indent=2);f.write('\n')
    return data


def read(name):return json.loads((OUT/(name+'.json')).read_text())


def call(port,route,body=None):
    req=urllib.request.Request(f'http://127.0.0.1:{port}/api/stack/'+route,
        data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=180) as r:return r.status,json.load(r)
    except urllib.error.HTTPError as e:
        data=e.read().decode()
        try:data=json.loads(data)
        except ValueError:pass
        return e.code,data


def ok(port,route,body=None):
    status,data=call(port,route,body);assert status==200,(route,status,data);return data


def snapshot(port):
    return {'individual':ok(port,'decision?id='+INDIVIDUAL),
            'population':ok(port,'decision?id='+POPULATION),'ledger':H.ledger()}


def equal(before,after):
    for kind in ('individual','population'):
        assert after[kind]['dependencies_current'],after[kind].get('stale_reason')
        for key in ('decision','tasks','followups'):
            assert before[kind][key]==after[kind][key],(kind,key)
    assert before['ledger']==after['ledger'],'Provider ledger changed'


def event(expected,request_id,action):
    return {'decision_id':INDIVIDUAL,'task_id':TASK,'expected_revision':expected,'request_id':request_id,
        'action':action,'actor':'synthetic-respiratory-review-team',
        'note':'Synthetic isolated recovery verification; no external dispatch or clinical review.'}


def retained_audit():
    result={}
    for name,revision in [('installation-v2',4),('rollback',2)]:
        path=BASE/name/'build/stack/program-work.sqlite3'
        with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
            current=db.execute('SELECT revision FROM capability_tasks WHERE id=?',(TASK,)).fetchone()[0]
            events=[(r,json.loads(p)['action']) for r,p in db.execute('SELECT revision,payload FROM capability_events WHERE task=? ORDER BY revision',(TASK,))]
            requests=db.execute("SELECT count(*) FROM capability_requests WHERE id IN ('runnable-release-resume-v1','runnable-release-prepare-v1')").fetchone()[0]
        assert current==revision and max(r for r,a in events)==revision
        assert requests==(2 if name=='installation-v2' else 0)
        result[name]={'revision':current,'events':events,'new_request_rows':requests}
    save('retained-audit',result)
    return result


def run(mode):
    before=json.loads((BASE/'before-http.json').read_text()) if mode!='before' else None
    if mode=='before':save('before-http',snapshot(8885))
    elif mode=='resume':
        initial=save('resumed-initial',snapshot(8887));equal(before,initial)
        meta=save('installed-meta',ok(8887,'meta'));assert meta['recovery']['mode']=='owned_work_only'
        task=next(x['task'] for x in initial['individual']['tasks'] if x['task']['id']==TASK)
        rev=task['revision'];body=event(rev,'runnable-release-resume-v1','hold')
        changed=save('held',ok(8887,'decision/event',body))
        assert changed['task']['revision']==rev+1 and changed['task']['state']=='on_hold'
        replay=save('held-replay',ok(8887,'decision/event',body));assert replay['idempotent_replay']
        assert replay['task']==changed['task']
        code,rejected=call(8887,'decision/event',event(rev,'runnable-release-stale-v1','cancel'))
        save('stale-rejected',{'http':code,'response':rejected});assert code==409,(code,rejected)
        result=save('prepared',ok(8887,'decision/event',event(rev+1,'runnable-release-prepare-v1','prepare_handoff')))
        assert result['task']['revision']==rev+2 and result['task']['state']=='handoff_prepared_for_review'
        assert result['external_actions_executed']==0
        for route in ('decision/prepare','decision/statement-review','portable-intake/save','context/interpret'):
            code,data=call(8887,route,{})
            save('disabled-'+route.replace('/','-'),{'http':code,'response':data});assert code==403
        after=save('resumed-after',snapshot(8887))
        assert after['individual']['decision']==before['individual']['decision']
        assert after['individual']['followups']==before['individual']['followups']
        assert after['population']['tasks']==before['population']['tasks']
        equal(before,save('original-after-resume',snapshot(8885)))
        save('resume-verification',{'status':'passed','revision_before':rev,'revision_after':rev+2,
             'idempotent_replay':True,'stale_request_http':409,'new_paid_calls':0,'new_human_reviews':0})
    elif mode=='restart':
        after=save('restart-http',snapshot(8887));equal(read('resumed-after'),after)
        equal(before,save('original-after-restart',snapshot(8885)))
        save('restart-verification',{'status':'passed','work_survived_restart':True})
    elif mode=='rollback':
        restored=save('rollback-http',snapshot(8887));equal(before,restored)
        equal(before,save('original-after-rollback',snapshot(8885)))
        retained_audit()
        save('rollback-verification',{'status':'passed','original_revision_restored':True,
             'changed_installation_audit_retained':True,'provider_ledger_rewound':False})
    else:raise ValueError(mode)

if __name__=='__main__':run(sys.argv[1])
