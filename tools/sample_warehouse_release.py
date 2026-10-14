"""Write a small, stratified patient sample of a warehouse release for smoke builds and disk/time estimates.

Usage: .venv/bin/python tools/sample_warehouse_release.py --release <release dir> --output <new dir> --per-segment 8
       .venv/bin/python tools/sample_warehouse_release.py --release <release dir> --output <new dir> --fraction 0.35

The source release is opened read-only. The sample keeps the public tables the adapters read (patients,
source_records for the sampled patients, organizations, clinicians, data_dictionary) and nothing else: no
private/ files and no evaluation tables. Patients are picked deterministically per data_segment (sha256 of the
patient id), so repeated runs give the same sample. The manifest is the release manifest with the sample
file checksum and a `sample` block (source release, source sha256, per-segment sizes, record counts).
Outside PDFs (v2.3+: documents/ files named by the sampled patients' scanned_documents records) are copied with their
checksums; other release files (private/ included) are not listed in the sample manifest.
"""
import argparse, hashlib, json, math, shutil, sqlite3
from collections import Counter
from pathlib import Path

PUBLIC = ('patients', 'organizations', 'clinicians', 'data_dictionary')


def sha256(path):
    with open(path, 'rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()


def sample(release, output, per_segment=None, panel=None, fraction=None):
    release, output = Path(release).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((release/'manifest.json').read_text())
    src = sqlite3.connect(f'file:{release/"warehouse.sqlite"}?mode=ro', uri=True)
    try:
        cols = {r[1] for r in src.execute('PRAGMA table_info(patients)')}
        seg = 'data_segment' if 'data_segment' in cols else "'deep_chart'"
        by_segment = {}
        for pid, s in src.execute(f'SELECT patient_id, {seg} FROM patients'):
            by_segment.setdefault(s or 'deep_chart', []).append(pid)
        chosen = []
        for s, ids in sorted(by_segment.items()):
            if s == 'deep_chart' and panel is not None: n = panel
            elif fraction is not None: n = len(ids) if s == 'deep_chart' else math.ceil(fraction * len(ids))
            else: n = per_segment
            chosen += sorted(ids, key=lambda p: hashlib.sha256(p.encode()).hexdigest())[:n]
        out = sqlite3.connect(f'file:{output/"warehouse.sqlite"}', uri=True)  # uri=True so ATTACH accepts a read-only URI
        for (sql,) in src.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name IN (%s)" % ','.join('?' * (len(PUBLIC) + 1)),
                                  (*PUBLIC, 'source_records')):
            out.execute(sql)
        out.execute('CREATE TEMP TABLE chosen(patient_id TEXT PRIMARY KEY)')
        out.executemany('INSERT INTO chosen VALUES (?)', [(p,) for p in chosen])
        out.commit()
        out.execute('ATTACH DATABASE ? AS src', (f'file:{release/"warehouse.sqlite"}?mode=ro',))
        out.execute('INSERT INTO main.patients SELECT * FROM src.patients WHERE patient_id IN (SELECT patient_id FROM chosen)')
        out.execute('INSERT INTO main.source_records SELECT * FROM src.source_records WHERE patient_id IN (SELECT patient_id FROM chosen)')
        for t in ('organizations', 'clinicians', 'data_dictionary'):
            if out.execute("SELECT 1 FROM src.sqlite_master WHERE name=?", (t,)).fetchone():
                out.execute(f'INSERT INTO main.{t} SELECT * FROM src.{t}')
        out.commit()
        counts = dict(out.execute('SELECT source_table, count(*) FROM main.source_records GROUP BY 1'))
        documents = sorted({p for (p,) in out.execute("SELECT json_extract(facts, '$.relative_path') FROM main.source_records "
                                                       "WHERE source_table = 'scanned_documents'") if isinstance(p, str)})
        picked = set(chosen); segments = Counter(s for s, ids in by_segment.items() for p in ids if p in picked)
        out.execute('DETACH DATABASE src'); out.close()
    finally:
        src.close()
    source_sha = manifest['files'].get('warehouse.sqlite')  # pinned by the release manifest
    files = {'warehouse.sqlite': sha256(output/'warehouse.sqlite')}
    copied = 0
    for rel in documents:  # outside PDFs stay byte-identical; never anything under private/
        src_file = (release/rel).resolve()
        if not rel.startswith('documents/') or (release/'private') in src_file.parents or not src_file.is_file(): continue
        (output/rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src_file, output/rel)
        files[rel] = sha256(output/rel); copied += 1
    manifest['files'] = files
    manifest['sample'] = {'source_release': str(release), 'source_sha256': source_sha,
                          'per_segment': per_segment, 'fraction_of_population_segments': fraction, 'segments': dict(segments), 'patients': len(chosen),
                          'records': sum(counts.values()), 'source_tables': counts, 'documents_copied': copied, 'synthetic': True}
    (output/'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest['sample']


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--release', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--per-segment', type=int, default=8); p.add_argument('--panel', type=int, help='deep-chart patients (default --per-segment)')
    p.add_argument('--fraction', type=float, help='keep every deep-chart patient and this fraction of each other segment (overrides --per-segment)')
    a = p.parse_args()
    s = sample(a.release, a.output, a.per_segment, a.panel, a.fraction)
    print(json.dumps({k: s[k] for k in ('patients', 'records', 'segments')}, indent=2))
