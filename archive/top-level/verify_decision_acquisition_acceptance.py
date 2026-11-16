"""Attach one demonstrated dependency check; do not promote answer reassessment."""
import hashlib
import json
from pathlib import Path
from clinical_core.contracts import digest
from clinical_core.agere.workflow_scorecard import release_acceptance
from verify_decision_acquisition import OUT, TARGET, OTHER, save


def read(path):return json.loads(Path(path).read_text())
def evidence(path):return {'path':str(path),'sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest()}


def main():
    rows=read(OUT/'http-requests-before.json');after=read(OUT/'http-after.json')
    assert len(rows)==len(after)==4
    assert read(OUT/'http-verification.json')['passed'] and read(OUT/'restart-verification.json')['passed']
    assert read(OUT/'browser-verification.json')['actual_ui_save_and_check']
    by_id={x['request']['result_id']:x for x in rows}
    for x in after:
        old=by_id[x['request_id']]['request'];cur=x['check'];dep=old['dependency']
        assert old['question']==read(OUT/'parent.json')['question']
        assert old['owner']==read(OUT/'prior-task.json')['owner']
        assert dep['id']==cur['dependency_id'] and dep['identity']==cur['current_dependency']['identity']
        assert old['analysis_window']==read(OUT/'parent.json')['analysis_window']
        assert not old['external_dispatch_performed'] and not cur['task_state_changed'] and not cur['clinical_resolution_established']
        assert cur['status']==('available_requires_interpretation' if dep['identity']['target_record_id']==TARGET else 'not_received_for_patient')
        if dep['identity']['target_record_id']==OTHER:assert dep['version']==cur['current_dependency']['version']
    paths=[OUT/(s+'.json') for s in ('predeclaration','http-preview','http-requests-before','http-after','http-verification','restart-verification','browser-verification')]
    paths += [OUT/'focused-tests.txt',OUT/'browser-dom.txt',Path('clinical_core/agere/clinical/decision_acquisition.py')]
    snapshot=read('config/agere/active_warehouse.json')['snapshot']
    checks=[]
    for key in ('changed_source_invalidates_dependents','unaffected_results_retain_identity','late_and_conflicting_sources'):
        checks.append({'id':key,'status':'unassessed','detail':'Real current answer and service/work recomputation is not demonstrated by local acquisition. Specific hosted interpretation approvals remain pending; unchanged dependency observations are not proof of unchanged clinical findings.','evidence':[]})
    checks.append({'id':'acquisition_tracks_blocking_dependency','status':'passed',
        'detail':'Existing functional question/task owns four exact documentary dependency requests. Actual HTTP save/reopen and restart; actual browser save/check/reload and accessibility verified. Restoring one source updates three affected observations while another dependency version remains exact. Original question/window/owner/history retained; unknown dates/types and stale/forged scope rejected in fixtures. Local requests only; clinical relevance and actual external acquisition unassessed. Screenshot capture returned blank, so no visual-layout claim.',
        'evidence':[evidence(p) for p in paths]})
    receipt=save('dependency-acceptance',{'schema':'agere-capability-acceptance/1','requirement_id':'evidence_change_reassessment','snapshot_digest':digest(snapshot),'checks':checks,
        'scope':'One of four engineering checks, using an explicitly controlled local availability replay of received sources. No new model output or clinical validation.'})
    cfg=Path('config/agere/capability_acceptance.json');cap=read(cfg)
    assert 'evidence_change_reassessment' not in cap
    cap['evidence_change_reassessment']=str(OUT/'dependency-acceptance.json')
    score=release_acceptance(read('artifacts/agere-workflow-evaluation-v1/scorecard.json')['cases'],capability_evidence=cap,snapshot=snapshot)
    assert score['engineering_requirements_passed']==6
    dep=next(r for r in score['requirements'] if r['id']=='evidence_change_reassessment')
    assert sum(x['status']=='passed' for x in dep['observations'])==1 and dep['status']=='unassessed'
    save('release-acceptance',score);cfg.write_text(json.dumps(cap,indent=2)+'\n')
    print('Dependency workflow 1/4; full release engineering remains 6/9.')


if __name__=='__main__':main()
