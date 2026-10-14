"""Migration audit for a RESTATED and/or EXPANDED warehouse release.

Restatement (default): same roster, no prior rows dropped, every changed row counted per table.
Expansion (--expansion): every prior patient still present, no prior rows dropped, changed prior rows counted per
table (activation then also needs --restatement when any changed), new patients and their rows counted.
Retraction (--retraction, with either): prior rows may be missing only when each is explained by the declared rule
(clinical_core/agere/warehouse_retraction.py: post-death/1, death taken from the new release's client-visible streams);
retracted rows are counted by stream and unexplained ones listed by stream and reason with examples.
Always recorded: prior record_ids that now name a different patient or stream (record-id reuse is not a restatement).

Usage: .venv/bin/python tools/restatement_audit.py --build artifacts/<new build> --previous <prior release dir> [--expansion] [--retraction]
Writes <build>/migration-audit.json (kind=restatement | expansion) for activate_healthcare_warehouse.py
--restatement / --expansion / --retraction.
"""
import argparse, json, sqlite3, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clinical_core.contracts import digest as roster_digest  # same digest the adapters record for a roster
from clinical_core.agere import warehouse_retraction


def audit(build, previous, expansion=False, retraction=False):
    connection = json.loads((Path(build) / 'connection.json').read_text())
    dataset = connection['snapshot']['source_dataset']
    new, old = Path(dataset['source']), Path(previous) / 'warehouse.sqlite'
    db = sqlite3.connect(f'file:{new}?mode=ro', uri=True); db.execute(f"ATTACH 'file:{old}?mode=ro' AS previous")
    try:
        cols = [r[1] for r in db.execute('PRAGMA previous.table_info(source_records)')]
        same = ' AND '.join(f'p."{c}" IS n."{c}"' for c in cols)
        missing = db.execute('SELECT count(*) FROM previous.source_records p LEFT JOIN main.source_records n USING(record_id) WHERE n.record_id IS NULL').fetchone()[0]
        by_table = dict(db.execute(f'SELECT p.source_table, count(*) FROM previous.source_records p JOIN main.source_records n ON p.record_id=n.record_id '
                                   f'WHERE NOT ({same}) GROUP BY p.source_table ORDER BY 2 DESC').fetchall())
        moved = dict(db.execute('SELECT p.source_table, count(*) FROM previous.source_records p JOIN main.source_records n ON p.record_id=n.record_id '
                                'WHERE p.patient_id IS NOT n.patient_id OR p.source_table IS NOT n.source_table GROUP BY 1 ORDER BY 2 DESC').fetchall())
        prior = db.execute('SELECT count(*) FROM previous.source_records').fetchone()[0]
        roster_new = sorted(r[0] for r in db.execute('SELECT patient_id FROM main.patients'))
        roster_old = sorted(r[0] for r in db.execute('SELECT patient_id FROM previous.patients'))
        added = sorted(set(roster_new) - set(roster_old))
        rows_new_patients = 0
        if expansion and added:
            db.execute('CREATE TEMP TABLE added(patient_id TEXT PRIMARY KEY)'); db.executemany('INSERT INTO added VALUES (?)', [(p,) for p in added])
            rows_new_patients = db.execute('SELECT count(*) FROM main.source_records WHERE patient_id IN (SELECT patient_id FROM added)').fetchone()[0]
        retracted = (warehouse_retraction.classify(warehouse_retraction.missing_rows(db), warehouse_retraction.death_dates(db), set(roster_new))
                     if retraction else None)
    finally:
        db.close()
    result = {'synthetic': True, 'kind': 'expansion' if expansion else 'restatement', 'prior_release': str(previous),
              'new_release': connection['release'], 'as_of': connection['snapshot']['as_of'], 'patients': len(roster_new),
              'identical_roster': roster_new == roster_old, 'patient_id_digest': dataset['original_patient_id_digest'],
              'prior_received_rows': prior, 'new_received_rows': connection['records'], 'prior_rows_missing': missing,
              'prior_rows_changed': sum(by_table.values()), 'changed_by_table': by_table,
              'prior_rows_missing_or_changed': missing + sum(by_table.values()),
              'prior_rows_reassigned': sum(moved.values()), 'reassigned_by_table': moved,
              'manifest_source_sha256': dataset['source_sha256'], 'private_files_read': False, 'retraction': bool(retraction)}
    if retraction:
        result.update(retracted)
    if expansion:
        result.update({'prior_patients': len(roster_old), 'prior_patient_id_digest': roster_digest(roster_old),
                       'prior_patients_missing': len(set(roster_old) - set(roster_new)), 'new_patients': len(added),
                       'new_patient_id_digest': roster_digest(added), 'rows_for_new_patients': rows_new_patients,
                       'new_rows_for_prior_patients': connection['records'] - rows_new_patients - (prior - missing)})
    return result


def main():
    p = argparse.ArgumentParser(); p.add_argument('--build', type=Path, required=True); p.add_argument('--previous', type=Path, required=True)
    p.add_argument('--expansion', action='store_true', help='The release adds patients; every prior patient must remain')
    p.add_argument('--retraction', action='store_true', help='Prior rows may be missing when explained by the declared post-death rule')
    a = p.parse_args()
    result = audit(a.build, a.previous, a.expansion, a.retraction)
    (a.build / 'migration-audit.json').write_text(json.dumps(result, indent=2) + '\n')
    keys = ('identical_roster', 'prior_rows_missing', 'prior_rows_changed', 'new_received_rows') + (
        ('prior_patients', 'prior_patients_missing', 'new_patients', 'rows_for_new_patients') if a.expansion else ()) + (
        ('prior_rows_retracted', 'retracted_patients', 'prior_rows_unexplained', 'unexplained_by_reason') if a.retraction else ()) + ('prior_rows_reassigned',)
    print(json.dumps({k: result[k] for k in keys}, indent=2))
    print('top changed tables:', list(result['changed_by_table'].items())[:8])
    if a.retraction: print('retracted by table:', list(result['retracted_by_table'].items())[:12])


if __name__ == '__main__':
    main()
