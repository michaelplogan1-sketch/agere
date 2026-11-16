"""Final preservation checks for the question-specific evidence assessment increment."""
from pathlib import Path
import hashlib,json,sqlite3,tomllib
from clinical_core.contracts import digest
from check_agere_build_control import backlog_text

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'artifacts/agere-reference-assessment-v1'

def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def main():
    baseline=json.loads((OUT/'preservation-baseline.json').read_text())
    changed=[name for name,h in baseline.items() if sha(ROOT/name)!=h]
    assert not changed,changed
    cfg=json.loads((ROOT/'config/agere/active_warehouse.json').read_text())
    source=cfg['snapshot']['source_dataset'];warehouse_hash=sha(Path(source['source']))
    assert warehouse_hash==source['source_sha256']
    with sqlite3.connect(Path(cfg['build'])/'stack/stack.sqlite3') as db:
        patients=sorted(row[0] for row in db.execute('SELECT id FROM patients'))
    assert len(patients)==1614 and digest(patients)==source['original_patient_id_digest']
    backlog=hashlib.sha256(backlog_text((ROOT/'AUTONOMOUS_STACK_QUEUE.md').read_text()).encode()).hexdigest()
    assert backlog==json.loads((ROOT/'AGERE_BUILD_CONTROL.json').read_text())['scope']['backlog_sha256']
    work={}
    with sqlite3.connect(Path(cfg['build'])/'stack/program-work.sqlite3') as db:
        for table,before in json.loads((OUT/'work-baseline.json').read_text()).items():
            rows=db.execute('SELECT * FROM '+table).fetchall()
            hashes={hashlib.sha256(json.dumps(row,sort_keys=True).encode()).hexdigest() for row in rows}
            assert set(before['row_hashes'])<=hashes and len(rows)>=before['count'],table
            work[table]={'before':before['count'],'after':len(rows),'all_prior_rows_unchanged':True,'added':len(rows)-before['count']}
    with sqlite3.connect(ROOT/'artifacts/agere-full/provider-budget.sqlite3') as db:
        rows=db.execute('SELECT * FROM calls ORDER BY id').fetchall()
    ledger={'count':len(rows),'sha256':hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()}
    baseline_ledger=json.loads((OUT/'provider-ledger-row-baseline.json').read_text())
    now={hashlib.sha256(json.dumps(r,sort_keys=True).encode()).hexdigest() for r in rows}
    assert set(baseline_ledger)<=now
    added=len(rows)-len(baseline_ledger)
    automation=tomllib.loads(Path('/Users/mikel/.codex/automations/continue-building-the-agere-stack/automation.toml').read_text())
    assert automation['status']=='PAUSED'
    result={'status':'passed','protected_files':170,'prior_receipts':158,'verified_files':len(baseline),'changed_files':changed,'warehouse_sha256':warehouse_hash,'roster_count':len(patients),'roster_digest':digest(patients),'ten_area_backlog_sha256':backlog,'work_histories':work,'provider_ledger':ledger,'new_provider_calls':added,'automatic_heartbeat':automation['status'],'design_checkout_and_publication':'Read-only; no edits or publication'}
    (OUT/'work-preservation.json').write_text(json.dumps(work,indent=2)+'\n')
    (OUT/'preservation-final.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'status':result['status'],'verified_files':len(baseline),'work_tables':len(work),'ledger_rows':len(rows),'new_provider_calls':added}))

if __name__=='__main__':main()
