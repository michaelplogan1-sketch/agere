"""Local demo agents over saved cohorts, using the existing counterparty and work store.

Simulation receipts never alter source evidence or establish clinical eligibility.
One click advances one step; optimistic revisions make duplicate clicks harmless.
"""
import json
from . import answer_context as A
from ..plans import catalog
from ..plans.counterparty import SimulatedCounterparty

STEPS = {
    'pharmd': ('chart_review', 'patient_outreach', 'consent', 'eligibility_check', 'enrollment', 'schedule_first_visit', 'first_visit'),
    'rpm': ('chart_review', 'patient_outreach', 'consent', 'eligibility_check', 'enrollment', 'device_shipment', 'device_activation'),
    'custom': ('chart_review', 'patient_outreach', 'consent', 'schedule_first_visit', 'first_visit'),
}
SUCCESS = {'reviewed', 'reached', 'consented', 'eligible', 'enrolled', 'scheduled', 'attended', 'delivered', 'activated'}
SCENARIOS = ('happy_path', 'records_needed', 'patient_declines')


def advance(path, request):
    if set(request) != {'context_id', 'item_id', 'revision', 'scenario'} or request['scenario'] not in SCENARIOS:
        raise ValueError('Choose a saved work item, its revision and a supported demo scenario')
    c = A.load(path, request['context_id'], current=True)
    db = A._db(path, c)
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision,payload FROM work_items WHERE id=?', (request['item_id'],)).fetchone()
            if not row:
                raise ValueError('Work item is absent from this answer context')
            if type(request['revision']) is not int or row[0] != request['revision']:
                raise ValueError('Work changed in another window. Reload before advancing.')
            item = json.loads(row[1])
            if item['patient_id'] not in c['patient_ids'] or item['state'] not in ('needs_review', 'in_review'):
                raise ValueError('Select active work within this saved cohort')
            sim = item.get('simulation') or {'scenario': request['scenario'], 'receipts': [], 'status': 'running',
                    'synthetic': True, 'model_calls': 0, 'api_cost_usd': 0,
                    'basis': 'Scripted demonstration using simulated counterparties; not a prediction or clinical decision.'}
            if sim['scenario'] != request['scenario']:
                raise ValueError('A started simulation keeps its scenario and history')
            if sim['status'] != 'running':
                return A.inspect(path, c['id'])
            steps = STEPS[item['program']]
            step = steps[len(sim['receipts'])]
            # Named scenarios are deliberately scripted, not claimed as observed success rates.
            program = next(p for p in catalog.load()['programs'] if p['id'] == 'pharmacy_gdmt_titration')
            program['id'] = 'answer_demo_' + item['program']
            program['name'] = A.PROGRAMS[item['program']]['label'] + ' demo agent'
            program['counterparty'] = {'outreach': {'p_reach': 1}, 'p_decline': int(sim['scenario'] == 'patient_declines'),
                'chart_review': {'p_records_needed': int(sim['scenario'] == 'records_needed')},
                'p_eligibility_denied': 0, 'p_coverage_denied': 0, 'p_scheduling_failed': 0,
                'p_rescheduled': 0, 'p_no_show': 0, 'p_shipping_failed': 0, 'p_not_activated': 0}
            cp = SimulatedCounterparty(program, None, workspace=c['id'], patient_id=item['patient_id'],
                                       as_of=c['source']['as_of'], scenario=sim['scenario'])
            rid = item['id'] + ':demo:' + str(len(sim['receipts']) + 1)
            receipt = cp.submit({'request_id': rid, 'step': step, 'day': len(sim['receipts']),
                                 'attempt': 1, 'not_before': sim['receipts'][-1]['at'] if sim['receipts'] else None})
            if step == 'chart_review' and receipt['outcome'] == 'reviewed':
                receipt['reason'] = {'pharmd': 'Simulated pharmacist review of therapy evidence',
                                     'rpm': 'Simulated monitoring-team review of monitoring evidence',
                                     'custom': 'Simulated care-team review of the selected evidence'}[item['program']]
            receipt['clinical_eligibility_established'] = False
            receipt['external_dispatch_performed'] = False
            sim['receipts'].append(receipt)
            sim['status'] = ('complete' if len(sim['receipts']) == len(steps) else 'running') if receipt['outcome'] in SUCCESS else 'needs_attention'
            sim['next_step'] = steps[len(sim['receipts'])] if sim['status'] == 'running' else None
            sim['follow_up'] = ('Request missing source records' if receipt['outcome'] == 'records_needed' else
                                'Return to the care team; preserve the patient preference' if receipt['outcome'] == 'declined' else
                                'Review the simulated exception') if sim['status'] == 'needs_attention' else None
            item.update(simulation=sim, revision=row[0]+1, updated_at=A.now())
            db.execute('INSERT INTO work_requests VALUES (?,?,?)', (rid, A.fingerprint([c['id'], item['id'], step]), json.dumps(receipt)))
            db.execute('UPDATE work_items SET revision=?,payload=? WHERE id=?', (item['revision'], json.dumps(item), item['id']))
            db.execute('INSERT INTO work_events VALUES (?,?,?)', (item['id'], item['revision'], json.dumps({'action': 'simulated_agent_step', 'item': item})))
    finally:
        db.close()
    return A.inspect(path, c['id'])
