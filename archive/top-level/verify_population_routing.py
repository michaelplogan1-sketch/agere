"""Actual original-cohort routing and bounded internal work, no external care."""
import json, os, sqlite3, time, urllib.request
from pathlib import Path
from collections import Counter

OUT = Path(os.environ.get('AGERE_ROUTING_OUT', 'artifacts/agere-population-routing-v1/http'))
BASE = 'http://localhost:8885/api/stack/'
PRIOR = Path('artifacts/agere-population-decision-v1/verified-v2')

def save(name, row):
    p = OUT / (name + '.json'); p.parent.mkdir(parents=True, exist_ok=True)
    assert not p.exists(), str(p)
    p.write_text(json.dumps(row, indent=2, allow_nan=False) + '\n')
    return row

def call(route, body=None):
    req = urllib.request.Request(BASE + route, data=None if body is None else json.dumps(body).encode(), headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req, timeout=300) as response: return json.load(response)

def ledger():
    with sqlite3.connect('artifacts/agere-full/provider-budget.sqlite3') as db: return dict(db.execute('SELECT id,status FROM calls'))

def main():
    OUT.mkdir(parents=True, exist_ok=True); before = ledger(); results = []
    for domain in ('respiratory', 'behavioral'):
        decision = json.loads((PRIOR / (domain + '-decision.json')).read_text())
        view = call('decision?id=' + decision['id']); assert view['dependencies_current']
        nominees = [m for m in decision['members'] if m['topics']]
        for i, member in enumerate(nominees):
            name = domain + '/member-' + member['patient_id']
            # Interrupted attempts retain their immutable member receipts. Final
            # allocation independently checks every saved context against runtime.
            if not (OUT / (name + '.json')).exists():
                route = save(name, call('decision/population-route-member', {'decision_id':decision['id'], 'patient_id':member['patient_id']}))
                assert route['coverage_context']['patient_id'] == member['patient_id'] and not route['coverage_context']['verified_benefits']
            if (i + 1) % 10 == 0: print(json.dumps({'domain':domain, 'member_routes':i + 1, 'total':len(nominees)}), flush=True)
        request = {'decision_id':decision['id'], 'review_limits':{s['capability']['id']:4 for s in decision['service_scope']},
            'assumption_source':'Explicit fictional engineering assumption: at most four noncancelled internal review tasks per service in this demonstration. Not staff availability, clinical priority or reserved service slots.'}
        allocation = save(domain + '/allocation', call('decision/population-allocate', request))
        assert allocation['routing_complete'] and len(allocation['members']) == 1614
        assert len({m['patient_id'] for m in allocation['members']}) == 1614
        assert Counter(m['disposition'] for m in allocation['members']) == allocation['counts']
        assert allocation['counts']['existing_internal_work'] == 1
        assert allocation['counts']['proposed_internal_review'] == 3
        assert allocation['counts']['waiting_for_internal_review_capacity'] == len(nominees) - 4
        execute = {'decision_id':decision['id'], 'allocation_id':allocation['result_id'], 'actor':'G4 synthetic internal review worker'}
        done = save(domain + '/execution', call('decision/population-execute', execute))
        assert done['prepared_count'] == 3 and done['status'] == 'internal_batch_prepared'
        assert not done['human_review_completed'] and not done['external_dispatch_performed']
        replay = call('decision/population-execute', execute); assert replay.pop('idempotent_replay') is True and replay == done
        after = save(domain + '/after-disposition', call('decision/population-allocate', request))
        assert after['counts']['existing_internal_work'] == 4 and not after['counts'].get('proposed_internal_review')
        reopened = save(domain + '/reopened', call('decision?id=' + decision['id']))
        assert reopened['dependencies_current'] and len(reopened['tasks']) == 4
        assert all(t['binding_current'] and t['task']['state'] == 'handoff_prepared_for_review' and t['decision_binding']['question'] == decision['question'] for t in reopened['tasks'])
        results.append({'domain':domain, 'decision_id':decision['id'], 'member_contexts':len(nominees), 'roster':1614,
            'disposition_counts':after['counts'], 'owned_internal_handoffs':4, 'new_handoffs':3, 'clinical_eligibility':'unverified'})
        print(json.dumps(results[-1]), flush=True)
    assert ledger() == before
    save('verification', {'status':'passed', 'programs':results, 'provider_calls_added':0, 'external_dispatches':0,
        'clinical_validated':False, 'meaning':'All source-nominated members have source-bound patient/payer/participation review routes and a disposition; supplied internal-review limits select work. Waiting and unknown members remain accounted for. No clinical eligibility, clinical service allocation, real staffing, human review or patient outcomes claimed.'})

if __name__ == '__main__': main()
