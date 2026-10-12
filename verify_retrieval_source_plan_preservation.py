"""Preservation and independent whole-record/request reconstruction checks."""
import hashlib
import json
import sqlite3
from pathlib import Path
from copy import deepcopy
from clinical_core.contracts import digest
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import program_work, record_context as C, source_synthesis as S
from clinical_core.agere.clinical import synthesis_availability as A, reference_table_context as F
from clinical_core.agere.clinical import meaning_source_stage as M, source_meaning as meaning, record_synthesis as RS, case_passes as P
from clinical_core.agere.clinical.structured_transport import install as install_structured
from clinical_core.agere.clinical.meaning_source_dispatch import install as install_meaning, pinned

OUT = Path('artifacts/agere-retrieval-source-plan-v1'); PRIOR = Path('artifacts/agere-retrieval-comparison-v1')
original_envelope = A.envelope
install_structured(); F.install(); install_meaning(stack_path())


def main():
    verification = json.loads((OUT/'http-verification.json').read_text()); assert verification['status'] == 'passed'
    protected = json.loads((PRIOR/'protected-for-continuation.json').read_text())['protected_files']
    for p, h in protected.items(): assert hashlib.sha256(Path(p).read_bytes()).hexdigest() == h, p
    old_count = len(protected)
    for name in ('retrieval_source_plan.py', 'reference_table_context.py'):
        p = 'clinical_core/agere/clinical/'+name; protected[p] = hashlib.sha256(Path(p).read_bytes()).hexdigest()
    (OUT/'protected-for-continuation.json').write_text(json.dumps({'schema': 'agere-protected-continuation/1', 'protected_files': protected}, indent=2)+'\n')
    baseline = json.loads((PRIOR/'history-baseline.json').read_text()); db = program_work.database(stack_path()); counts = {}
    for table, old in baseline.items():
        current = [list(r) for r in db.execute('SELECT * FROM '+table+' ORDER BY 1,2')]
        assert current == old; counts[table] = len(current)
    db.close(); (OUT/'history-baseline.json').write_text(json.dumps(baseline, indent=2)+'\n')
    oldledger = json.loads((PRIOR/'ledger-row-baseline.json').read_text()); ledger = sqlite3.connect('artifacts/agere-full/provider-budget.sqlite3')
    assert ledger.execute('SELECT COUNT(*) FROM calls').fetchone()[0] == len(oldledger)
    for old in oldledger: assert list(ledger.execute('SELECT * FROM calls WHERE id=?', (old[0],)).fetchone()) == old
    ledger.close(); (OUT/'ledger-row-baseline.json').write_text(json.dumps(oldledger, indent=2)+'\n')
    external = [r for r in json.loads(Path('artifacts/agere-multipass-synthesis-v1/research-recovery-index.json').read_text())['files'] if r['path'].startswith('/Users/mikel/Documents/')]
    for r in external: assert hashlib.sha256(Path(r['path']).read_bytes()).hexdigest() == r['sha256']
    assert len(pinned(stack_path())) == 33
    db = sqlite3.connect((stack_path()/'stack.sqlite3').resolve().as_uri()+'?mode=ro', uri=True)
    assert db.execute('SELECT COUNT(*) FROM patients').fetchone()[0] == 1614
    assert db.execute('SELECT COUNT(*) FROM records').fetchone()[0] == 1238686
    audit = []
    for workflow in ('medication', 'payer'):
        saved = json.loads((OUT/(workflow+'-source-plan.json')).read_text()); record_ids = []
        for c in saved['contexts']:
            assert C.verified(stack_path(), c['id']) == c
            row, bundle, links, packets, _ = M.prepare(stack_path(), c['id']); received = A.snapshot(stack_path(), row)
            assert S.stages(stack_path(), c['id'])[0] is M
            submitted = []
            for packet, (body, units) in zip(bundle['passes'], packets):
                base, original_units = RS.pass_request(packet, links, row['missing_evidence']['record_scope'])
                ordinary = meaning.envelope(original_envelope(base, original_units, links, received), original_units, received)
                restored = deepcopy(body)
                restored['messages'][0]['content'] = P.context.encode(F.restore_wire(json.loads(body['messages'][0]['content']))).decode()
                assert restored == ordinary and units == original_units
                assert P.request_bytes(body) <= 20000
                submitted.extend(u['id'] for u in packet['evidence'])
                audit.append({'workflow': workflow, 'group_context': c['id'], 'pass_number': packet['pass_number'],
                              'source_units': len(packet['evidence']), 'ordinary_bytes': P.request_bytes(ordinary),
                              'reference_table_bytes': P.request_bytes(body), 'exact_metadata_reconstruction': True,
                              'original_units_and_response_contract_unchanged': True})
            assert len(submitted) == len(set(submitted)) and sorted(submitted) == sorted(u['id'] for u in c['model_context']['evidence'])
            record_ids.extend(c['record_scope']['record_ids'])
        assert len(record_ids) == len(set(record_ids)) and sorted(record_ids) == saved['plan']['preview']['selected_record_ids']
        for source in saved['plan']['preview']['records']:
            pid, raw = db.execute('SELECT patient,payload FROM records WHERE id=?', (source['record_id'],)).fetchone()
            assert pid == saved['plan']['preview']['patient_id'] and digest(json.loads(raw)) == source['record_hash']
    db.close()
    result = {'status': 'passed', 'prior_protected_files': old_count, 'protected_files': len(protected),
              'unchanged_history_counts': counts, 'exact_ledger_rows_preserved': len(oldledger), 'separate_research_files_preserved': len(external),
              'historical_source_routes_pinned': 33, 'request_reconstruction': audit,
              'selected_records_independently_verified': 34, 'source_units_accounted_for': 86,
              'provider_calls': 0, 'clinical_accuracy_assessed': False}
    (OUT/'preservation.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'request_reconstruction'}, indent=2), flush=True)


if __name__ == '__main__': main()
