"""Local source-arrival integration; never dispatches a provider request.

Actual saved functional answer -> baseline decision -> existing internal work ->
received inventory via HTTP. Controlled source changes run only in a small,
isolated copy with an explicitly synthetic replay row, not the active warehouse.
"""
import json
import sqlite3
import sys
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from clinical_core.contracts import digest
from clinical_core.agere.clinical import question_decision as q

OUT=Path('artifacts/agere-source-arrival-v1')
BASE='http://localhost:8885/api/stack/'
LEGACY='4425a7424b037f08578c34c260a7dc2cde965890aba0a0b39f223346898dd25b'


def save(name,value):
    p=OUT/(name+'.json')
    if p.exists(): raise ValueError('Evidence already exists: '+str(p))
    p.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
    return value


def call(route,body=None):
    request=urllib.request.Request(BASE+route,data=None if body is None else json.dumps(body).encode(),
        headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=90) as response:return json.load(response)


def ledger():
    with sqlite3.connect('artifacts/agere-full/provider-budget.sqlite3') as db:
        return dict(db.execute('SELECT id,status FROM calls'))


def main():
    OUT.mkdir(exist_ok=True); before=ledger()
    legacy=save('legacy-reopen',call('decision?id='+LEGACY))
    assert legacy['dependencies_current']
    assert legacy['received_source_changes']['newly_received_count'] is None
    request={**legacy['decision']['request'],'planner':'cited_source_type_baseline'}
    decision=save('current-decision',call('decision/prepare',request))
    assert decision==call('decision/prepare',request)
    assert decision['answer_digest']==legacy['decision']['answer_digest']
    assert decision['received_inventory']['records']
    option=decision['matching']['options'][0]
    task=save('work-binding',call('decision/queue',{'decision_id':decision['id'],
        'option_id':option['id'],'actor':'Local G3 source-arrival verification'}))['task']
    assert task['id'] in [t['task']['id'] for t in legacy['tasks']]
    obs=save('current-observation',call('decision/observe',{'decision_id':decision['id'],'task_id':task['id']}))
    assert obs['status']=='no_received_source_changes' and not obs['changes']
    assert obs==call('decision/observe',{'decision_id':decision['id'],'task_id':task['id']})
    live=save('current-reopen',call('decision?id='+decision['id']))
    assert live['dependencies_current'] and live['tasks'][0]['task']==task
    assert any(f['result_id']==obs['result_id'] for f in live['followups'])

    # Copy only patient source payloads and exact saved question/work rows.
    # No source/provider caches, vectors, credentials or generator data are used.
    config=json.loads(Path('config/agere/active_warehouse.json').read_text())
    source=Path(config['build'])/'stack'; replay=OUT/'isolated-replay';replay.mkdir()
    (replay/'run.json').write_bytes((source/'run.json').read_bytes())
    with sqlite3.connect(source.resolve().joinpath('stack.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        records=[json.loads(r[0]) for r in db.execute('SELECT payload FROM records WHERE patient=? ORDER BY id',(decision['patient_id'],))]
    with sqlite3.connect(replay/'stack.sqlite3') as db:
        db.execute('CREATE TABLE patients(id TEXT PRIMARY KEY)')
        db.execute('INSERT INTO patients VALUES (?)',(decision['patient_id'],))
        db.execute('CREATE TABLE records(id TEXT PRIMARY KEY,patient TEXT,payload TEXT)')
        db.executemany('INSERT INTO records VALUES (?,?,?)',[(r['record_id'],r['patient_id'],json.dumps(r)) for r in records])
    assert q.received_inventory(replay,decision['patient_id'])==decision['received_inventory']
    db=q.database(replay)
    try:
        with db:
            db.execute('INSERT INTO question_decisions VALUES (?,?)',(decision['id'],json.dumps(decision)))
            link=live['tasks'][0]['decision_binding']
            db.execute('INSERT INTO question_decision_tasks VALUES (?,?,?)',(decision['id'],task['id'],json.dumps(link)))
            db.execute('INSERT INTO capability_tasks VALUES (?,?,?,?,?)',(task['id'],task['buyer_id'],task['patient_id'],task['revision'],json.dumps(task)))
    finally:db.close()
    retriever=SimpleNamespace(path=replay)
    request={'decision_id':decision['id'],'task_id':task['id']}
    initial=save('replay-before',q.observe(retriever,request))
    assert not initial['changes']
    added={'record_id':'local-development-arrival-probe','patient_id':decision['patient_id'],
        'source_table':'local_development_probe','available_date':config['snapshot']['as_of'],
        'event_date':None,'synthetic':True,'identity_status':'matched',
        'text':'Controlled local engineering replay only. No clinical statement or patient event.'}
    selected=next(iter(decision['source_versions']['records']))
    original=next(r for r in records if r['record_id']==selected)
    modified={**original,'local_development_revision_marker':True}
    outside=next(r for r in records if r['record_id'] not in decision['source_versions']['records'])
    with sqlite3.connect(replay/'stack.sqlite3') as db:
        db.execute('INSERT INTO records VALUES (?,?,?)',(added['record_id'],added['patient_id'],json.dumps(added)))
        db.execute('UPDATE records SET payload=? WHERE id=?',(json.dumps(modified),selected))
        db.execute('DELETE FROM records WHERE id=?',(outside['record_id'],))
    follow=save('replay-changed-observation',q.observe(retriever,request))
    assert follow==q.observe(retriever,request)
    assert (follow['newly_received_count'],follow['version_changed_count'],follow['not_in_current_snapshot_count'])==(1,1,1)
    assert follow['question']==decision['question'] and follow['task_id']==task['id']
    assert not follow['care_outcome_established'] and not follow['interpretation_changed']
    assert q.care_readiness.task(replay,task['id'])==task
    persisted=save('replay-reopened',q.reopen(retriever,decision['id']))
    assert len(persisted['followups'])==2
    assert q.received_inventory(source,decision['patient_id'])==decision['received_inventory']
    assert before==ledger()
    save('verification',{'status':'passed','decision_id':decision['id'],'task_id':task['id'],
        'received_records':len(records),'selected_records':len(decision['source_versions']['records']),
        'actual_http_question_work_observation':True,'controlled_isolated_changes':3,
        'active_patient_inventory_unchanged':True,'legacy_arrivals_not_fabricated':True,
        'work_unchanged':True,'provider_calls_added':0,'ledger_unchanged':True,
        'clinical_validated':False,'new_interpretation_generated':False,
        'scope':'Source arrival observation is verified. Isolated copy contains exact saved decision/work and received records, not its complete interpretation cache. It does not demonstrate recomputation of clinical meaning or live external source intake.'})
    print(json.dumps({'status':'passed','decision':decision['id'],'records':len(records),'replay_changes':3,'provider_calls_added':0}))


def verify_restart():
    before=ledger()
    decision=json.loads((OUT/'current-decision.json').read_text())
    task=json.loads((OUT/'work-binding.json').read_text())['task']
    observed=json.loads((OUT/'current-observation.json').read_text())
    current=call('decision?id='+decision['id'])
    assert current['decision']==decision and current['dependencies_current']
    assert current['tasks'][0]['task']==task and observed in current['followups']
    follow=save('answer-followup-after-restart',call('decision/followup',{
        'decision_id':decision['id'],'task_id':task['id'],'answer_id':decision['answer_id']}))
    assert follow['status']=='no_new_evidence' and follow['received_source_changes']['newly_received_count']==0
    assert follow['current_received_inventory']==decision['received_inventory']
    assert before==ledger()
    save('restart-verification',{'status':'passed','decision_reopened_unchanged':True,
        'observation_persisted':True,'task_unchanged':True,'answer_followup_binds_received_inventory':True,
        'provider_calls_added':0,'ledger_unchanged':True})
    print('Restart and answer follow-up verified; no provider calls.')


if __name__=='__main__':
    if sys.argv[1:]==['--restart']:verify_restart()
    elif not sys.argv[1:]:main()
    else:raise ValueError('Use no arguments or --restart')
