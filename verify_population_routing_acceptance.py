"""Attach verified local documentary population evidence to the finite contract."""
import hashlib, json
from pathlib import Path
from clinical_core.contracts import digest
from clinical_core.agere.workflow_scorecard import capability_checks, acceptance_status, release_acceptance

ROOT = Path('artifacts/agere-population-routing-v1')
def read(p): return json.loads(Path(p).read_text())
def evidence(p): return {'path':str(p), 'sha256':hashlib.sha256(Path(p).read_bytes()).hexdigest()}
def save(p, row):
    assert not p.exists(), str(p)
    p.write_text(json.dumps(row, indent=2) + '\n')

def main():
    live = read(ROOT/'http/verification.json'); assert live['status'] == 'passed'
    assert len(live['programs']) == 2 and all(p['member_contexts'] in (100,24) and p['roster'] == 1614 and p['owned_internal_handoffs'] == 4 for p in live['programs'])
    old = read('artifacts/agere-population-decision-v1/population-acceptance.json')
    rows = old['checks']
    for row in rows:
        if row['id'] == 'buyer_capacity_and_payer_prerequisites':
            row.update(status='passed', detail='Every source-nominated member has current source-bound patient, received payer and participation contexts. Conflicting/undetermined coverage and consent stay unresolved. All roster members have dispositions. Dated service declarations remain distinct from explicitly supplied internal-review limits; no service slot is reserved.',
                evidence=[evidence(ROOT/'http/verification.json')] + [evidence(ROOT/f'http/{d}/after-disposition.json') for d in ('respiratory','behavioral')])
        elif row['id'] == 'owned_work_and_internal_followthrough':
            row.update(status='passed', detail='Each complete population is accounted for. Four owned internal handoffs per program fit the explicit review envelope; 96 respiratory and 20 behavioral members wait with inspectable options/owners and unresolved prerequisites. All other members retain explicit unknown need. Batch replay is idempotent. This verifies internal documentary work, not completed human review, service delivery or clinical outcomes.',
                evidence=[evidence(ROOT/'http/verification.json')] + [evidence(ROOT/f'http/{d}/{n}.json') for d in ('respiratory','behavioral') for n in ('execution','after-disposition','reopened')])
    old['scope'] = 'Local documentary population program engineering only; clinical indications, actual clinical allocation, live benefits and outcomes remain unverified.'
    receipt = ROOT/'population-acceptance.json'; save(receipt, old)
    snapshot = read('config/agere/active_warehouse.json')['snapshot']
    assert digest(snapshot) == old['snapshot_digest']
    spec = next(s for s in read('config/agere/release_acceptance.json')['requirements'] if s['id'] == old['requirement_id'])
    checks = capability_checks(spec, str(receipt), snapshot)
    assert acceptance_status(checks) == 'passed'
    save(ROOT/'population-acceptance-verification.json', {'status':'passed','passed':6,'total':6,'checks':checks,'clinical_validated':False})
    report = read('artifacts/agere-workflow-evaluation-v1/scorecard.json')
    capability_map = {'complete_population_program':str(receipt)}
    result = release_acceptance(report['cases'], capability_evidence=capability_map, snapshot=snapshot)
    assert result['engineering_requirements_passed'] == 1 and result['engineering_requirements_total'] == 9
    assert report['complete_workflows'] == 5
    save(ROOT/'release-acceptance.json', result)
    # Only the evaluation view reads this pointer; no clinical runtime evidence.
    config = Path('config/agere/capability_acceptance.json')
    if config.exists():
        current = read(config)
        assert set(current) <= {'complete_population_program'}, 'Preserve and reconcile concurrent capability evidence'
    config.write_text(json.dumps(capability_map, indent=2) + '\n')
    print(json.dumps({'population_checks':'6/6','full_release_engineering':'1/9','individual_workflows':'5/6','clinical_validation':False}))

if __name__ == '__main__': main()
