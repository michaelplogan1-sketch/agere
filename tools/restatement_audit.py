"""Migration audit for a RESTATED warehouse release: same roster, no prior rows dropped, every changed row counted.

Usage: .venv/bin/python tools/restatement_audit.py --build artifacts/<new build> --previous <prior release dir>
Writes <build>/migration-audit.json (kind=restatement) for activate_healthcare_warehouse.py --restatement.
"""
import argparse, hashlib, json, sqlite3
from pathlib import Path


def digest(ids):
    return hashlib.sha256(json.dumps(sorted(ids)).encode()).hexdigest()


def main():
    p = argparse.ArgumentParser(); p.add_argument('--build', type=Path, required=True); p.add_argument('--previous', type=Path, required=True)
    a = p.parse_args()
    connection = json.loads((a.build / 'connection.json').read_text())
    dataset = connection['snapshot']['source_dataset']
    new, old = Path(dataset['source']), a.previous / 'warehouse.sqlite'
    db = sqlite3.connect(f'file:{new}?mode=ro', uri=True); db.execute(f"ATTACH 'file:{old}?mode=ro' AS previous")
    cols = [r[1] for r in db.execute('PRAGMA previous.table_info(source_records)')]
    same = ' AND '.join(f'p."{c}" IS n."{c}"' for c in cols)
    missing = db.execute('SELECT count(*) FROM previous.source_records p LEFT JOIN main.source_records n USING(record_id) WHERE n.record_id IS NULL').fetchone()[0]
    by_table = dict(db.execute(f'SELECT p.source_table, count(*) FROM previous.source_records p JOIN main.source_records n ON p.record_id=n.record_id '
                               f'WHERE NOT ({same}) GROUP BY p.source_table ORDER BY 2 DESC').fetchall())
    prior = db.execute('SELECT count(*) FROM previous.source_records').fetchone()[0]
    roster_new = [r[0] for r in db.execute('SELECT patient_id FROM main.patients')]
    roster_old = [r[0] for r in db.execute('SELECT patient_id FROM previous.patients')]
    audit = {'synthetic': True, 'kind': 'restatement', 'prior_release': str(a.previous), 'new_release': connection['release'],
             'as_of': connection['snapshot']['as_of'], 'patients': len(roster_new), 'identical_roster': sorted(roster_new) == sorted(roster_old),
             'patient_id_digest': dataset['original_patient_id_digest'], 'prior_received_rows': prior,
             'new_received_rows': connection['records'], 'prior_rows_missing': missing, 'prior_rows_changed': sum(by_table.values()),
             'changed_by_table': by_table, 'prior_rows_missing_or_changed': missing + sum(by_table.values()),
             'manifest_source_sha256': dataset['source_sha256'], 'private_files_read': False}
    (a.build / 'migration-audit.json').write_text(json.dumps(audit, indent=2) + '\n')
    print(json.dumps({k: audit[k] for k in ('identical_roster', 'prior_rows_missing', 'prior_rows_changed', 'new_received_rows')}, indent=2))
    print('top changed tables:', list(by_table.items())[:8])


if __name__ == '__main__':
    main()
