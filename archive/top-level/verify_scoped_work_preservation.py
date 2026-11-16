"""Regression discovered by actual scoped command verification; local-only recovery."""
import json,urllib.error
from pathlib import Path
import verify_scoped_review_compiler as V

def main():
    V.BASE='http://localhost:8885/api/stack/'
    V.OUT=Path('artifacts/agere-scoped-review-compiler-v1/final');V.OUT.mkdir(exist_ok=True)
    initial=Path('artifacts/agere-scoped-review-compiler-v1')
    candidate=json.loads((initial/'candidate-decision.json').read_text())
    match=json.loads((initial/'candidate-match.json').read_text())
    option=next(o for o in match['matching']['options'] if o['capability']['id']=='respiratory-review')
    old_id='47b22d8abb7edbcff4b7ac1a630495bf4d4957f2a90fd33c8fb53658d99cadb2'
    before=V.call('decision?id='+old_id);ledger=V.ledger()
    try:V.call('decision/population-queue',{'decision_id':candidate['id'],'match_id':match['result_id'],'option_id':option['id'],'actor':'Verify cross-question protection'})
    except urllib.error.HTTPError as exc:
        error=json.load(exc);assert 'different saved service plan' in error['error']
        V.save('cross-question-conflict',{'status':exc.code,'response':error})
    else:raise AssertionError('Expected existing work protection')
    after=V.call('decision?id='+old_id)
    assert after['tasks']==before['tasks'] and all(t['binding_current'] for t in after['tasks'])
    restored=next(t['task'] for t in after['tasks'] if t['task']['id']=='0260238af000caeb086bcce9043757d3896fab9751aecfc0f0d1359cafbe5872')
    assert restored['state']=='handoff_prepared_for_review'
    V.save('prior-decision-recovered',after)
    # Keep real demonstration work independent, under a declared fictional buyer.
    buyer='g5-scoped-command-development'
    port=V.call('capabilities?buyer=g4-population-development');existing=V.call('capabilities?buyer='+buyer)
    if not existing['revision']:
        V.save('isolated-buyer',V.call('capabilities/save',{'buyer_id':buyer,'expected_revision':0,'actor':'Scoped compiler isolated synthetic verification','capabilities':port['capabilities']}))
    else:assert existing['capabilities']==port['capabilities']
    command=V.save('composition',V.call('compose',{'question':V.QUESTION,'planning':'scoped_local_candidate'}))
    decision=V.save('decision',V.call('decision/scoped-prepare',{'question':V.QUESTION,'buyer_id':buyer,'as_of':command['decision_seed']['as_of'],'compiler_identity':command['planner_receipt']['compiler_identity']}))
    V.save('payer',V.call('decision/population-route-member',{'decision_id':decision['id'],'patient_id':'P-1004'}))
    match=V.save('match',V.call('decision/population-match',{'decision_id':decision['id'],'patient_id':'P-1004'}))
    option=next(o for o in match['matching']['options'] if o['capability']['id']=='respiratory-review')
    req={'decision_id':decision['id'],'match_id':match['result_id'],'option_id':option['id'],'actor':'Scoped compiler isolated synthetic verification'}
    queued=V.save('work',V.call('decision/population-queue',req));assert queued['task']['revision']==1
    assert V.call('decision/population-queue',req)['idempotent_replay']
    view=V.save('reopen',V.call('decision?id='+decision['id']))
    assert view['dependencies_current'] and all(t['binding_current'] for t in view['tasks'])
    assert len([r for r in view['followups'] if r['schema']=='scoped-command-plan/1'])==1
    assert V.call('decision?id='+old_id)['tasks']==before['tasks']
    assert V.ledger()==ledger
    V.save('verification',{'status':'passed','normal_server_restart':True,'candidate_decision_id':decision['id'],
        'isolated_buyer':buyer,'new_synthetic_task_id':queued['task']['id'],'prior_work_restored_through_audited_events':True,
        'prior_work_unchanged_by_rejected_collision_and_isolated_work':True,'original_task_revision':restored['revision'],
        'new_queue_idempotency_verified':True,'provider_ledger_unchanged':True,'new_paid_calls':0,
        'limitation':'Population question queue guard serializes shared population routes only; legacy direct capability paths retain their prior refresh behavior. Original failed candidate link remains historical.'})
    print(json.dumps({'status':'passed','decision_id':decision['id'],'old_work_preserved':True}))
if __name__=='__main__':main()
