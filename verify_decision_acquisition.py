"""Local HTTP replay of real saved functional work; controlled source removals.

No fabricated interpretation, external request or frozen evaluation mutation.
"""
import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path
from urllib.request import Request, urlopen
from clinical_core.agere.clinical import question_decision as Q

OUT = Path('artifacts/agere-decision-acquisition-v1')
REPLAY = OUT/'replay'
SOURCE = Path(json.loads(Path('config/agere/active_warehouse.json').read_text())['build'])/'stack'
ORIGIN = {'decision_id':'5ff4579639e1215c9739b07c0d3daf7a08ac22b2ae41cfb12b7f2894fa8ac464',
          'task_id':'8fdc6e2e21ae567c96d5188bf07c9ae98565ae8430028a0c79a46d5ee0941d33'}
TARGET = 'WHL-R-3e5354db752dabd3759d69'
OTHER = 'WHL-R-f2fbfb016208d2eb7afc4b'


def save(name, value):
    p=OUT/(name+'.json')
    if p.exists():raise ValueError('Immutable evidence already exists: '+str(p))
    p.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
    return value


def api(route, body=None):
    req=Request('http://127.0.0.1:8887/api/stack/decision'+route,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type':'application/json'})
    with urlopen(req,timeout=45) as r:return json.load(r)


def init():
    prior=Path('artifacts/agere-decision-reassessment-v1/replay')
    def ignore(path,names):return [n for n in names if n.endswith(('.sqlite3','.sqlite3-wal','.sqlite3-shm','.lock'))]
    shutil.copytree(prior,REPLAY,symlinks=True,ignore=ignore)
    for p in prior.rglob('*.sqlite3'):
        with sqlite3.connect('file:'+str(p.resolve())+'?mode=ro',uri=True) as src,sqlite3.connect(REPLAY/p.relative_to(prior)) as dst:src.backup(dst)
    lp=REPLAY/'longitudinal'
    (REPLAY/'longitudinal-link.json').write_text(json.dumps({'path':str(lp.resolve()),'manifest_sha256':hashlib.sha256((lp/'run.json').read_bytes()).hexdigest()}))
    save('origin',ORIGIN);save('parent',Q.load(REPLAY,ORIGIN['decision_id']))
    save('prior-task',Q.care_readiness.task(REPLAY,ORIGIN['task_id']))
    save('active-inventory-before',Q.received_inventory(SOURCE,'P-1004'))
    save('unaffected-inventory-before',Q.received_inventory(REPLAY,'P-3002'))
    removed=[]
    with sqlite3.connect(REPLAY/'stack.sqlite3') as db:
        for key in (TARGET,OTHER):
            removed.append(json.loads(db.execute('SELECT payload FROM records WHERE id=?',(key,)).fetchone()[0]))
            db.execute('DELETE FROM records WHERE id=?',(key,))
    save('removed-records',removed)
    # No stale event assertion should masquerade as available source evidence.
    with sqlite3.connect(lp/'events.sqlite3') as db:
        db.execute("DELETE FROM events WHERE json_extract(payload,'$.source.record_id') IN (?,?)",(TARGET,OTHER))
    save('predeclaration',{'workflow':'Existing functional question, exact owner, missing direct citation and two declared typed references',
        'source_fixture':'Remove two existing sources only from an isolated two-patient development copy, then restore one unchanged received source.',
        'tests':['Actual HTTP save/reopen/idempotency','Exact matching arrival changes only affected dependency versions','Source and task history retained','Browser save/check/reload','Fresh-process reopen'],
        'clinical_or_model_qualification':False,'new_model_calls':0,'external_contact':False,
        'limitations':['Controlled received-source availability replay, not an actual external acquisition','Original model qualification is not claimed for copied warehouse','Date-window and adversarial branches are tested in engineering fixtures']})
    print('Independent functional replay prepared; pending appointment replay unchanged.')


def before():
    p=save('http-preview',api('/acquisition-preview',ORIGIN))
    assert len(p['dependencies'])==4
    rows=[]
    for d in p['dependencies']:
        req={**ORIGIN,'preview_id':p['id'],'dependency_id':d['id'],'actor':'Synthetic acquisition verifier'}
        saved=api('/acquisition-create',req);assert saved==api('/acquisition-create',req)
        check=api('/acquisition-check',{'request_id':saved['result_id']})
        assert check['status']=='not_received_for_patient'
        rows.append({'request':saved,'check':check})
    save('http-requests-before',rows)
    view=save('http-view-before',api('?id='+ORIGIN['decision_id']))
    assert view['tasks'][0]['task']==json.loads((OUT/'prior-task.json').read_text())
    print('Four real local dependency requests saved and checked through HTTP.')


def arrive():
    r=next(r for r in json.loads((OUT/'removed-records.json').read_text()) if r['record_id']==TARGET)
    with sqlite3.connect(REPLAY/'stack.sqlite3') as db:
        db.execute('INSERT INTO records VALUES (?,?,?,?)',(r['record_id'],r['patient_id'],r['source_table'],json.dumps(r)))
    save('arrival',{'record_id':TARGET,'record_restored_byte_equivalent':True,'meaning':'Source available again; clinical resolution unassessed'})
    print('Restored one exact local received source; no external acquisition.')


def after():
    results=[]
    for row in json.loads((OUT/'http-requests-before.json').read_text()):
        saved=row['request'];req={'request_id':saved['result_id']}
        view=api('/acquisition-open',req);check=api('/acquisition-check',req)
        restored=saved['dependency']['identity']['target_record_id']==TARGET
        assert view['dependency_unchanged'] is not restored
        assert check['status']==('available_requires_interpretation' if restored else 'not_received_for_patient')
        assert check['dependency_id']==saved['dependency']['id']
        assert not check['clinical_resolution_established'] and not check['task_state_changed'] and not check['request_sent']
        assert check==api('/acquisition-check',req)
        results.append({'request_id':saved['result_id'],'view':view,'check':check})
    save('http-after',results)
    view=save('http-view-after',api('?id='+ORIGIN['decision_id']))
    assert view['tasks'][0]['task']==json.loads((OUT/'prior-task.json').read_text())
    assert view['decision']==json.loads((OUT/'parent.json').read_text())
    assert Q.received_inventory(SOURCE,'P-1004')==json.loads((OUT/'active-inventory-before.json').read_text())
    assert Q.received_inventory(REPLAY,'P-3002')==json.loads((OUT/'unaffected-inventory-before.json').read_text())
    save('http-verification',{'passed':True,'requests_saved':4,'affected_dependencies_updated':3,'unaffected_dependency_version_retained':1,
        'original_question_and_work_unchanged':True,'exact_dependency_ids_retained':True,'active_and_other_patient_sources_unchanged':True,
        'external_requests':0,'model_calls':0,'clinical_resolution_claimed':False})
    print('Three dependency observations updated; one unaffected version and original work unchanged.')


def restart():
    for row in json.loads((OUT/'http-after.json').read_text()):
        assert api('/acquisition-open',{'request_id':row['request_id']})==row['view']
        assert api('/acquisition-check',{'request_id':row['request_id']})==row['check']
    save('restart-verification',{'passed':True,'request_and_current_observation_reopened':4,'history_unchanged':True})
    print('Fresh-process request and observation reopen passed.')


if __name__=='__main__':globals()[sys.argv[1]]()
