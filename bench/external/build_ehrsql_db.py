"""Build the EHRSQL 2024 benchmark databases and MANIFEST.json from the downloaded sources.

Inputs (downloaded once, with the user's approval, into artifacts/external/ehrsql-2024/):
  download/ehrsql-2024-f9e1aa0.tar.gz          github.com/glee4810/ehrsql-2024 @ f9e1aa0 (CC-BY-4.0)
  download/mimic-iv-clinical-database-demo-2.2.zip  physionet.org mimic-iv-demo 2.2 (ODbL 1.0)
  ehrsql-repo/, mimic-iv-demo-2.2/             their extracted contents

Outputs (same folder):
  mimic_iv.sqlite        the organisers' preprocessed MIMIC-IV demo (time-shifted to 2100, EHRSQL schema),
                         copied byte for byte. Reference SQL is SQLite dialect and the gold answers were
                         computed against this file, so it is the scoring database.
  mimic_iv.duckdb        the same 17 tables and rows, loaded into DuckDB with the declared column types.
  mimic_iv_demo_raw.duckdb  the raw PhysioNet demo CSVs (31 tables, original MIMIC-IV schema), for row-count
                         provenance; not used for scoring.
  MANIFEST.json          licences, sources, sha256 and row counts. A copy is committed as
                         bench/external/ehrsql_manifest.json.

Stdlib + duckdb only. Run: .venv/bin/python -I bench/external/build_ehrsql_db.py [--root DIR]
"""
from pathlib import Path
import argparse, csv, gzip, hashlib, json, shutil, sqlite3, tempfile

import duckdb

REPO = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = Path('/Users/mikel/clinical-insights-local/artifacts/external/ehrsql-2024')
EHRSQL_COMMIT = 'f9e1aa02160d39e3f8df52bf5c69c5cf2e472499'
TYPES = {'INT': 'BIGINT', 'INTEGER': 'BIGINT', 'DOUBLE PRECISION': 'DOUBLE', 'TIMESTAMP(0)': 'TIMESTAMP'}


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''): h.update(block)
    return h.hexdigest()


def duck_type(declared):
    declared = declared.upper().strip()
    if declared.startswith('VARCHAR'): return 'VARCHAR'
    return TYPES.get(declared, 'VARCHAR')


def sqlite_to_duckdb(src, dst):
    """Copy every table with its declared types; rows go through a temporary CSV (no pandas/pyarrow)."""
    dst.unlink(missing_ok=True)
    lite = sqlite3.connect(f'file:{src}?mode=ro', uri=True)
    lite.text_factory = lambda b: b.decode(errors='ignore')
    duck = duckdb.connect(str(dst))
    counts = {}
    with tempfile.TemporaryDirectory() as tmp:
        for (table,) in lite.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY rowid"):
            cols = [(name, duck_type(kind)) for _, name, kind, *_ in lite.execute(f'PRAGMA table_info("{table}")')]
            duck.execute(f'CREATE TABLE "{table}" ({", ".join(f"{n} {t}" for n, t in cols)})')
            path = Path(tmp) / f'{table}.csv'
            with open(path, 'w', newline='') as f:
                writer = csv.writer(f)
                for row in lite.execute(f'SELECT * FROM "{table}"'): writer.writerow(['\\N' if v is None else v for v in row])
            duck.execute(f"COPY \"{table}\" FROM '{path}' (HEADER false, NULLSTR '\\N')")
            counts[table] = {'sqlite': lite.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0],
                             'duckdb': duck.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]}
    duck.close(); lite.close()
    return counts


def raw_demo_to_duckdb(demo, dst):
    dst.unlink(missing_ok=True)
    duck = duckdb.connect(str(dst))
    counts = {}
    for path in sorted(demo.glob('*/*.csv.gz')):
        table = f'{path.parent.name}_{path.name.removesuffix(".csv.gz")}'
        duck.execute(f"CREATE TABLE {table} AS SELECT * FROM read_csv('{path}', header = true, sample_size = -1)")
        with gzip.open(path, 'rt') as f: lines = sum(1 for _ in csv.reader(f)) - 1
        counts[table] = {'csv_rows': lines, 'duckdb': duck.execute(f'SELECT count(*) FROM {table}').fetchone()[0]}
    duck.close()
    return counts


def split_stats(data_dir):
    out = {}
    for split in ('train', 'valid', 'test'):
        items = json.loads((data_dir / split / 'data.json').read_text())['data']
        labels = json.loads((data_dir / split / 'label.json').read_text())
        out[split] = {'questions': len(items), 'unanswerable': sum(labels[i['id']] == 'null' for i in items),
                      'files': {name: {'sha256': sha256(data_dir / split / name), 'bytes': (data_dir / split / name).stat().st_size}
                                for name in ('data.json', 'label.json', 'answer.json', 'annotated.json')}}
    return out


def main(root):
    repo, demo = root / 'ehrsql-repo', next((root / 'mimic-iv-demo-2.2').iterdir())
    data_dir = repo / 'data' / 'mimic_iv'
    sqlite_out, duck_out, raw_out = root / 'mimic_iv.sqlite', root / 'mimic_iv.duckdb', root / 'mimic_iv_demo_raw.duckdb'
    shutil.copyfile(data_dir / 'mimic_iv.sqlite', sqlite_out)
    assert sha256(sqlite_out) == sha256(data_dir / 'mimic_iv.sqlite')
    ehrsql_counts = sqlite_to_duckdb(sqlite_out, duck_out)
    raw_counts = raw_demo_to_duckdb(demo, raw_out)
    bad = {t: c for t, c in ehrsql_counts.items() if c['sqlite'] != c['duckdb']} | \
          {t: c for t, c in raw_counts.items() if c['csv_rows'] != c['duckdb']}
    if bad: raise SystemExit(f'row count mismatch: {bad}')
    sums = dict(line.split(maxsplit=1)[::-1] for line in (demo / 'SHA256SUMS.txt').read_text().splitlines() if line.strip())
    verified = all(sha256(demo / name.strip()) == digest for name, digest in sums.items())
    manifest = {
        'name': 'EHRSQL 2024 shared task (MIMIC-IV demo)',
        'built_by': 'bench/external/build_ehrsql_db.py',
        'policy': 'Public data only. MIMIC-IV demo is the open 100-patient subset (ODbL), not credentialed MIMIC-IV. '
                  'The EHRSQL test split is on bench/external/ehrsql_test_denylist.json and must never be trained on.',
        'sources': {
            'ehrsql_2024': {'url': f'https://github.com/glee4810/ehrsql-2024/tree/{EHRSQL_COMMIT}', 'commit': EHRSQL_COMMIT,
                            'archive': 'download/ehrsql-2024-f9e1aa0.tar.gz', 'archive_sha256': sha256(root / 'download/ehrsql-2024-f9e1aa0.tar.gz'),
                            'licence': 'CC-BY-4.0', 'citation': 'Lee et al., Overview of the EHRSQL 2024 Shared Task on Reliable '
                            'Text-to-SQL Modeling on Electronic Health Records, ClinicalNLP 2024, arXiv:2405.06673',
                            'homepage': 'https://sites.google.com/view/ehrsql-2024'},
            'mimic_iv_demo': {'url': 'https://physionet.org/content/mimic-iv-demo/2.2/', 'version': '2.2',
                              'archive': 'download/mimic-iv-clinical-database-demo-2.2.zip',
                              'archive_sha256': sha256(root / 'download/mimic-iv-clinical-database-demo-2.2.zip'),
                              'licence': 'ODbL-1.0 (Open Data Commons Open Database License v1.0)',
                              'citation': 'Johnson A, Bulgarelli L, Pollard T, Horng S, Celi LA, Mark R. MIMIC-IV Clinical '
                                          'Database Demo (version 2.2). PhysioNet (2023). https://doi.org/10.13026/dp1f-ex47',
                              'physionet_sha256sums_verified': verified, 'patients': 100},
        },
        'files': {
            'mimic_iv.sqlite': {'sha256': sha256(sqlite_out), 'bytes': sqlite_out.stat().st_size, 'licence': 'ODbL-1.0',
                                'origin': 'ehrsql-repo/data/mimic_iv/mimic_iv.sqlite (organisers\' preprocessing of the demo: '
                                          'time-shifted to 2100, value dedup); scoring database, SQLite dialect'},
            'mimic_iv.duckdb': {'sha256': sha256(duck_out), 'bytes': duck_out.stat().st_size, 'licence': 'ODbL-1.0',
                                'origin': 'mimic_iv.sqlite loaded table by table with declared types'},
            'mimic_iv_demo_raw.duckdb': {'sha256': sha256(raw_out), 'bytes': raw_out.stat().st_size, 'licence': 'ODbL-1.0',
                                         'origin': 'raw PhysioNet demo CSVs (hosp_*, icu_*), provenance only'},
        },
        'row_counts': {'ehrsql_schema': ehrsql_counts, 'raw_demo': raw_counts},
        'splits': split_stats(data_dir),
        'answerable_note': "label.json holds the reference SQL ('null' = unanswerable); answer.json the executed result.",
    }
    text = json.dumps(manifest, indent=2, sort_keys=False) + '\n'
    (root / 'MANIFEST.json').write_text(text)
    (REPO / 'bench' / 'external' / 'ehrsql_manifest.json').write_text(text)
    print(json.dumps({k: v['questions'] for k, v in manifest['splits'].items()}), 'checksums verified:', verified)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    main(parser.parse_args().root)
