"""Independent raw request/report oracle and exhaustive persisted-reference audit."""
from collections import Counter, defaultdict
from pathlib import Path
import json
import sqlite3
import zlib
from clinical_core.contracts import digest
from clinical_core.agere.clinical import source_state as ss, interpretation_state as interp, pre_event_state as pre, outcomes

ROOT = Path('artifacts/agere-healthcare-v123/stack')
OUT = Path('artifacts/agere-care-reference-state-v1')
source = sqlite3.connect((ROOT/'stack.sqlite3').resolve().as_uri()+'?mode=ro', uri=True)
state = sqlite3.connect((ROOT/'interpretation-state.sqlite3').resolve().as_uri()+'?mode=ro', uri=True)
roster = sorted(r[0] for r in source.execute('SELECT id FROM patients'))
raw = {}; by_patient = defaultdict(list); inventory = Counter(); admissions = defaultdict(list)
for (payload,) in source.execute("SELECT payload FROM records WHERE stream IN ('referrals','referral_status_updates','outside_report_status','imported_documents','outside_followup_contacts','adt')"):
    r = json.loads(payload); raw[r['record_id']] = r; inventory[r['source_table']] += 1
    by_patient[r['patient_id']].append(r)
    if r['source_table'] == 'adt' and r.get('event') in ('admission', 'A01'): admissions[r['patient_id']].append(r)


def native(r):
    return r.get('_warehouse_source', {}).get('adapter') == 'original-cohort-warehouse-adapter/1'


def completion(r):
    if r['source_table'] != 'outside_report_status' or r.get('performed_service_documented') is not True or r.get('status') != 'received': return False
    request = raw.get(r.get('referral_record_id')); report = raw.get(r.get('imported_record_id'))
    return bool(request and report and native(request) and native(report) and request['source_table'] == 'referrals' and
        report['source_table'] == 'imported_documents' and report.get('referral_record_id') == request['record_id'] and
        request['patient_id'] == report['patient_id'] == r['patient_id'] and
        all(x['available_date'] <= '2029-08-15' for x in (r, request, report)))


references = set(); tested_requests = set(); checked = Counter(); summaries = {}
for case in ('patient', 'population', 'prior_population'):
    captured = json.loads((OUT/('final-'+case+'.json')).read_text()); assert captured['status'] == 200
    answer = captured['response']; assert answer['question'] == captured['request']['question']
    assert not answer['clinical_execution_authorized']
    ids = ['P-1001'] if case == 'patient' else roster
    nodes = {x['node']['id']: x for x in answer['results']}
    for item in answer['results']:
        node, result = item['node'], item['result']; p = node['parameters']
        if node['operation'] not in ('source_state', 'pre_event_state') or p.get('concept') != 'care requested services': continue
        assert result['patients_evaluated'] == len(ids) and result['population_digest'] == digest(ids)
        snap = interp.snapshot(ROOT, ss.scope(p)); ic = interp.code_identity(); projection = {k: v for k, v in p.items() if k != 'filter'}
        temporal = node['operation'] == 'pre_event_state'; code = pre.code_identity() if temporal else ss.code_identity()
        prep = outcomes.prepare(ROOT, nodes[node['inputs'][0]]['node']['parameters']) if temporal else None
        statuses = Counter(); counts = Counter(); reports = Counter(); temporal_positive = []
        for pid in ids:
            iid = ss.receipt_id(snap, pid, interpretation_code=ic)
            key = digest([code, iid, outcomes.receipt_id(prep, pid), projection] if temporal else [code, iid, projection])
            table = 'pre_event_states' if temporal else 'source_states'
            found = state.execute('SELECT payload FROM '+table+' WHERE id=?', (key,)).fetchone(); assert found, (case, pid)
            row = json.loads(zlib.decompress(found[0])); assert row['patient_id'] == pid
            assert not row['current_clinical_state_established']
            requests = [r for r in by_patient[pid] if r['source_table'] == 'referrals' and native(r)]
            completions = [r for r in by_patient[pid] if completion(r)]
            if temporal:
                ap = prep['parameters']; candidates = [r for r in admissions[pid] if
                    r['available_date'] <= ap['available_by'] and ap['index_from'] <= r['event_date'] <= ap['index_through']]
                day = min((r['event_date'] for r in candidates), default=None)
                assert row['temporal_scope']['before_date_exclusive'] == day
                if not day:
                    assert row['status'] == 'temporal_anchor_unresolved'; assert not row['care_accounts']
                else:
                    expected = []
                    for r in completions:
                        deps = (r, raw[r['referral_record_id']], raw[r['imported_record_id']])
                        if all(x['event_date'] < day and x['available_date'] < day and x['available_date'] <= ap['available_by'] for x in deps): expected.append(r)
                    actual = {a['source']['record_id'] for a in row['selected_assertions']}
                    latest = max((r['event_date'] for r in expected), default=None)
                    assert actual == {r['record_id'] for r in expected if r['event_date'] == latest}
                    if actual: temporal_positive.append(pid)
            else:
                # Includes requests lacking any service-type binding.
                assert {a['request_id'] for a in row['care_accounts']} == {r['record_id'] for r in requests}
                known = {r['referral_record_id'] for r in completions}
                assert {a['request_id'] for a in row['care_accounts'] if a['status'] == 'performed_service_documented'} == known
                booked = sum(r['source_table'] == 'referral_status_updates' and r.get('status') == 'appointment_reported_booked' for r in by_patient[pid])
                assert row['care_report_counts'].get('care_booking_report', 0) == booked
            for account in row['care_accounts']:
                tested_requests.add(account['request_id']); assert account['all_requested_care_complete'] is None
                assert not account['clinical_completion_verified']
                for entry in account['reports'] + account.get('mapping_gaps', []):
                    for ref in entry['reference_evidence']:
                        r = raw[ref['record_id']]
                        assert r['patient_id'] == ref['patient_id'] == pid
                        assert digest(r) == ref['record_hash'] and r.get(ref['field']) == ref['value']
                        assert r['event_date'] == ref['source_date'] and r['available_date'] == ref['arrival_date']
                        references.add((r['record_id'], ref['field']))
            statuses[row['status']+'_'+row['value'] if row['status'] == 'documented' else row['status']] += 1
            counts.update(row['care_account_counts']); reports.update(row['care_report_counts']); checked[case] += 1
        assert dict(statuses) == result['status_counts']; assert dict(counts) == result['care_account_counts']; assert dict(reports) == result['care_report_counts']
        summaries[case] = {'patients_checked': len(ids), 'status_counts': dict(statuses), 'request_counts': dict(counts),
            'report_counts': dict(reports), 'prior_completion_patients': temporal_positive if temporal else None}

output = {'status': 'passed', 'method': 'Independent literal raw-source joins, request denominator, booking count, event selector, strict prior source/arrival clocks and all persisted exact field/hash references.',
    'raw_stream_inventory': dict(inventory), 'raw_qualified_completion_reports': sum(completion(r) for r in raw.values()),
    'unique_request_accounts_checked': len(tested_requests), 'unique_source_fields_checked': len(references),
    'cases': summaries, 'synthetic': True, 'clinical_model_or_causal_qualification': False}
with (OUT/'source-audit.json').open('x') as f: json.dump(output, f, indent=2)
print(json.dumps(output, indent=2))
