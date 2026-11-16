"""Actual local decision plus isolated bounded source-review preparation.

No model call or fabricated interpretation. The replay uses the actual protected
context/source-pass planner and exact existing events in a separate local snapshot.
"""
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace
import urllib.request
from clinical_core.contracts import digest
from clinical_core.agere.clinical import question_decision as q, record_context, longitudinal, source_synthesis

OUT=Path(os.environ.get('AGERE_SOURCE_REVIEW_OUT','artifacts/agere-source-review-v1'))
BASE='http://localhost:8885/api/stack/'


def save(name,value):
    path=OUT/(name+'.json')
    if path.exists():raise ValueError('Do not overwrite existing evidence: '+str(path))
    path.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
    return value


def call(route,body=None):
    request=urllib.request.Request(BASE+route,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=180) as response:return json.load(response)


def ledger():
    with sqlite3.connect('artifacts/agere-full/provider-budget.sqlite3') as db:
        return dict(db.execute('SELECT id,status FROM calls'))


def main():
    OUT.mkdir(exist_ok=True);before=ledger()
    prior=json.loads(Path('artifacts/agere-source-arrival-v1/current-decision.json').read_text())
    decision=save('live-decision',call('decision/prepare',prior['request']))
    assert decision['finding_sources'] and decision['answer_digest']==prior['answer_digest']
    work=save('live-work',call('decision/queue',{'decision_id':decision['id'],
        'option_id':decision['matching']['options'][0]['id'],'actor':'Local follow-up integration'}))
    task=work['task'];origin={'decision_id':decision['id'],'task_id':task['id']}
    initial=save('live-no-change-preview',call('decision/review-preview',origin))
    assert not initial['can_prepare'] and not initial['arrivals']['changes']
    assert initial['finding_dependency_index_complete'] and len(initial['finding_impacts'])==len(decision['finding_sources'])

    config=json.loads(Path('config/agere/active_warehouse.json').read_text());source=Path(config['build'])/'stack'
    replay=OUT/'isolated-replay';replay.mkdir()
    records,original_meta=record_context.received(source,decision['patient_id'])
    # A distinct development source identity prevents this local fixture from
    # masquerading as an activated warehouse release.
    meta={**original_meta,'source_digest':digest(['local-followup-replay',digest(records)]),
        'source_run':'isolated-source-review-development','development_replay':True}
    (replay/'run.json').write_text(json.dumps(meta,indent=2)+'\n')
    (replay/'annotation-link.json').write_bytes((source/'annotation-link.json').read_bytes())
    with sqlite3.connect(replay/'stack.sqlite3') as db:
        db.execute('CREATE TABLE patients(id TEXT PRIMARY KEY)');db.execute('INSERT INTO patients VALUES (?)',(decision['patient_id'],))
        db.execute('CREATE TABLE records(id TEXT PRIMARY KEY,patient TEXT,payload TEXT)')
        db.executemany('INSERT INTO records VALUES (?,?,?)',[(r['record_id'],r['patient_id'],json.dumps(r)) for r in records])
        db.execute('CREATE TABLE program_packages(id TEXT PRIMARY KEY,program TEXT,version TEXT,start TEXT,end TEXT,payload TEXT)')
    events,lm=longitudinal.connect(source)
    local_long=replay/'longitudinal';local_long.mkdir()
    try:
        schema=events.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='events'").fetchone()[0]
        with sqlite3.connect(local_long/'events.sqlite3') as db:
            db.execute(schema)
            rows=events.execute('SELECT * FROM events WHERE patient=?',(decision['patient_id'],)).fetchall()
            db.executemany('INSERT INTO events VALUES ('+','.join('?' for _ in rows[0])+')',rows)
    finally:events.close()
    lm={**lm,'source_digest':meta['source_digest'],'development_replay':True}
    (local_long/'run.json').write_text(json.dumps(lm,indent=2)+'\n')
    (replay/'longitudinal-link.json').write_text(json.dumps({'path':str(local_long.resolve()),
        'manifest_sha256':hashlib.sha256((local_long/'run.json').read_bytes()).hexdigest()}))
    db=q.database(replay)
    try:
        with db:
            db.execute('INSERT INTO question_decisions VALUES (?,?)',(decision['id'],json.dumps(decision)))
            db.execute('INSERT INTO question_decision_tasks VALUES (?,?,?)',(decision['id'],task['id'],json.dumps(work['decision_binding'])))
            db.execute('INSERT INTO capability_tasks VALUES (?,?,?,?,?)',(task['id'],task['buyer_id'],task['patient_id'],task['revision'],json.dumps(task)))
    finally:db.close()
    added={'record_id':'local-followup-engineering-probe','patient_id':decision['patient_id'],
        'source_table':'notes','available_date':meta['snapshot']['as_of'],'event_date':meta['snapshot']['as_of'],
        'synthetic':True,'identity_status':'matched',
        'text':'Controlled local engineering replay. No clinical finding or care outcome is asserted.'}
    with sqlite3.connect(replay/'stack.sqlite3') as db:
        db.execute('INSERT INTO records VALUES (?,?,?)',(added['record_id'],added['patient_id'],json.dumps(added)))
    retriever=SimpleNamespace(path=replay)
    # Match the workbench's context/reader contract bootstrap. These only
    # install local dispatch adapters; they do not call a provider.
    from clinical_core.agere.clinical import (literal_reference_runtime, structured_transport,
        reference_table_context, reference_reconciliation_transport, interpretation_recovery,
        linked_claim_integrity, meaning_source_dispatch)
    for module in (literal_reference_runtime,structured_transport,reference_table_context,
                   reference_reconciliation_transport,interpretation_recovery,linked_claim_integrity):
        module.install()
    meaning_source_dispatch.install(replay)
    preview=save('replay-review-preview',q.review_preview(retriever,origin))
    assert preview['can_prepare'] and preview['changed_records_selected']==[added['record_id']]
    assert preview['analysis_window']==decision['analysis_window']
    assert all(not f['changed_cited_records'] for f in preview['finding_impacts'])
    prepared=save('replay-prepared-review',q.prepare_review(retriever,{**origin,'preview_id':preview['id']}))
    assert prepared['status']=='followup_contexts_prepared'
    assert prepared==q.prepare_review(retriever,{**origin,'preview_id':preview['id']})
    actual=[]
    for group in prepared['groups']:
        ctx=source_synthesis.context(replay,group['context_id'])
        save('replay-context-'+str(group['group_number']),ctx)
        assert ctx['parameters']['question']==preview['review_question'] and not ctx['omissions']
        assert ctx['dispatch']['source_review_allowed']
        actual.extend(ctx['record_scope']['record_ids'])
    assert sorted(actual)==preview['selected_record_ids'] and len(actual)==len(set(actual))
    assert q.care_readiness.task(replay,task['id'])==task
    assert q.received_inventory(source,decision['patient_id'])==decision['received_inventory']
    assert before==ledger()
    save('verification',{'status':'passed','live_decision_id':decision['id'],'task_id':task['id'],
        'actual_http_preview_verified':True,'all_findings_indexed':len(decision['finding_sources']),
        'isolated_prepared_groups':len(prepared['groups']),'selected_records':len(actual),
        'source_pass_planner':'existing protected implementation, no mocks',
        'context_omissions':0,'original_question_and_window_preserved':True,
        'active_patient_inventory_unchanged':True,'work_unchanged':True,'provider_calls_added':0,
        'clinical_validated':False,'new_interpretation_generated':False,
        'scope':'Actual HTTP no-change preview and isolated positive source preparation. Answer attachment is covered by contract tests with explicit machine stubs, not a live model answer or clinical adjudication.'})
    print(json.dumps({'status':'passed','decision_id':decision['id'],'review_groups':len(prepared['groups']),'selected_records':len(actual),'provider_calls_added':0}))


if __name__=='__main__':main()
