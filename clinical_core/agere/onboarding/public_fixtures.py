"""Public synthetic datasets as onboarding fixtures: a foreign claims layout (CMS DE-SynPUF) and OMOP CDM 5.3 (OHDSI Eunomia GiBleed).

    fetch(which=('synpuf', 'eunomia'), dest=None)     download the pinned zips (sha256 checked) and extract them
    synpuf_sqlite(out, dest=None, beneficiaries=500)  DE-SynPUF sample 1 (2008 beneficiary summary + inpatient claims of the
                                                      chosen beneficiaries) in its own column names, as one SQLite source
    eunomia_sqlite(out, dest=None)                    Eunomia GiBleed (OMOP CDM 5.3, all tables) as one SQLite source
    onboard(kind, source, work_dir)                   profile -> propose -> approve -> apply -> invariants: the mapping coverage
                                                      per entity, the unmapped tables and the first answers from the built layer

Why these two: DE-SynPUF is a claims layout our generator did not write (Medicare beneficiary + claim files, ICD-9, YYYYMMDD
dates, 1=yes/2=no flags, dollar amounts); Eunomia is a different OMOP shape from omop_fixture.py (the OHDSI reference CDM,
Synthea-derived). They show that onboarding "sits on any data". Both are public synthetic: structure tests only, no
clinical rate from either is quoted anywhere. They are fixtures, not training data. Files live under
artifacts/public-fixtures/ (git-ignored); nothing here is committed besides this code.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import urllib.request
import zipfile

VERSION = 'public-fixtures/1'
ROOT = Path(__file__).resolve().parents[3]
DEST = ROOT / 'artifacts' / 'public-fixtures'
CMS = 'https://www.cms.gov/Research-Statistics-Data-and-Systems/Downloadable-Public-Use-Files/SynPUFs/Downloads/'
SOURCES = {
    'synpuf': [
        (CMS + 'DE1_0_2008_Beneficiary_Summary_File_Sample_1.zip', 'DE1_0_2008_Beneficiary_Summary_File_Sample_1.zip',
         '2b0c9cbfb07a6eb46d5f96472c0891275a390b686c3fa34daae8d3e53d70b18b'),
        (CMS + 'DE1_0_2008_to_2010_Inpatient_Claims_Sample_1.zip', 'DE1_0_2008_to_2010_Inpatient_Claims_Sample_1.zip',
         'ac25124184d22863aff538607097d2d0c880d19f7d58f882f9d31129c492d893')],
    'eunomia': [
        ('https://github.com/OHDSI/EunomiaDatasets/raw/main/datasets/GiBleed/GiBleed_5.3.zip', 'GiBleed_5.3.zip',
         '081b82e84c90e2904e02c26546e46412b01cce566071c77e0c4a355c2d0612f9')],
}
MAX_ZIP_BYTES = 12 * 1024 * 1024  # these are the small samples; anything larger is refused


def _sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for block in iter(lambda: fh.read(1 << 20), b''): h.update(block)
    return h.hexdigest()


def fetch(which=('synpuf', 'eunomia'), dest=None, *, download=True):
    """-> {which: extracted folder}. A zip already present with the pinned sha256 is not downloaded again."""
    dest = Path(dest or DEST)
    out = {}
    for name in which:
        folder = dest / name
        folder.mkdir(parents=True, exist_ok=True)
        for url, fname, sha in SOURCES[name]:
            z = folder / fname
            if not z.exists() or _sha(z) != sha:
                if not download: raise FileNotFoundError(z)
                with urllib.request.urlopen(url, timeout=120) as r:  # noqa: S310 - pinned https URLs above
                    data = r.read(MAX_ZIP_BYTES + 1)
                if len(data) > MAX_ZIP_BYTES: raise ValueError(f'{fname} is larger than the small-sample bound')
                z.write_bytes(data)
                if _sha(z) != sha: raise ValueError(f'{fname}: sha256 differs from the pinned value')
            target = folder / 'x'
            with zipfile.ZipFile(z) as zf:
                for member in zf.namelist():
                    if member.startswith('__MACOSX') or member.endswith('/') or '..' in Path(member).parts or member.startswith('/'): continue
                    t = target / member
                    if not t.exists():
                        t.parent.mkdir(parents=True, exist_ok=True)
                        t.write_bytes(zf.read(member))
        out[name] = folder / 'x'
    return out


def _load_csv(db, table, path, *, keep=None, key=None, limit=None, lower=False):
    with open(path, newline='', encoding='utf-8-sig', errors='replace') as fh:
        reader = csv.reader(line.replace('\x00', '') for line in fh)
        cols = [c.strip() for c in next(reader)]
        names = [c.lower() if lower else c for c in cols]
        db.execute(f'CREATE TABLE "{table}" (' + ', '.join(f'"{c}" TEXT' for c in names) + ')')
        ki = cols.index(key) if key else None
        n, batch = 0, []
        for row in reader:
            if keep is not None and row[ki] not in keep: continue
            batch.append(row[:len(cols)] + [None] * (len(cols) - len(row)))
            n += 1
            if len(batch) >= 5000:
                db.executemany(f'INSERT INTO "{table}" VALUES (' + ','.join('?' * len(cols)) + ')', batch); batch = []
            if limit and n >= limit: break
        if batch: db.executemany(f'INSERT INTO "{table}" VALUES (' + ','.join('?' * len(cols)) + ')', batch)
    return n


def synpuf_sqlite(out, dest=None, beneficiaries=500):
    folder = fetch(('synpuf',), dest)['synpuf']
    out = Path(out); out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists(): out.unlink()
    db = sqlite3.connect(out)
    try:
        bene_csv = next(folder.glob('*Beneficiary*.csv')); ip_csv = next(folder.glob('*Inpatient*.csv'))
        n = _load_csv(db, 'DE1_0_2008_Beneficiary_Summary_File_Sample_1', bene_csv, limit=beneficiaries)
        ids = {r[0] for r in db.execute('SELECT DESYNPUF_ID FROM DE1_0_2008_Beneficiary_Summary_File_Sample_1')}
        c = _load_csv(db, 'DE1_0_2008_to_2010_Inpatient_Claims_Sample_1', ip_csv, keep=ids, key='DESYNPUF_ID')
        db.commit()
    finally:
        db.close()
    return {'path': str(out), 'beneficiaries': n, 'inpatient_claims': c, 'source': 'CMS DE-SynPUF sample 1 (public synthetic)'}


def eunomia_sqlite(out, dest=None):
    folder = fetch(('eunomia',), dest)['eunomia']
    out = Path(out); out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists(): out.unlink()
    db = sqlite3.connect(out)
    counts = {}
    try:
        for p in sorted(folder.rglob('*.csv')):
            if p.name.startswith('._'): continue  # macOS AppleDouble resource forks inside the zip
            counts[p.stem.lower()] = _load_csv(db, p.stem.lower(), p, lower=True)
        db.commit()
    finally:
        db.close()
    return {'path': str(out), 'tables': counts, 'source': 'OHDSI Eunomia GiBleed 5.3 (public synthetic OMOP CDM)'}


def onboard(kind, source, work_dir, decisions=None):
    """Run the onboarding pipeline on one SQLite source -> report (mapping per entity, unmapped, invariants, first answers)."""
    from . import apply as apply_mod, connectors, contract, matcher, profile, verify
    work = Path(work_dir); work.mkdir(parents=True, exist_ok=True)
    conn = connectors.SQLiteConnector(source)
    try:
        prof = profile.profile_source(conn)
        model = contract.build_model()
        mapping = matcher.propose(model, prof)
        approved = json.loads(json.dumps(mapping))
        approved['approval'] = {'approved': True, 'reviewer': f'public fixture {kind}'}
        for key, change in (decisions or {}).items():  # a reviewer's per-field decisions, e.g. {'claim_line.charge_cents': {'transforms': [...]}}
            entity, field = key.split('.', 1)
            approved['entities'][entity]['fields'][field].update(change)
        sem = work / 'semantic.sqlite3'
        if sem.exists(): sem.unlink()
        apply_mod.apply(approved, conn, sem, model)
        inv = verify.invariants(sem, approved, conn, model=model)
    finally:
        conn.close()
    entities = {}
    for name, e in (mapping.get('entities') or {}).items():
        fields = e.get('fields') or {}
        entities[name] = {'status': e.get('status'), 'source_table': e.get('source_table'),
                          'mapped_fields': sum(1 for f in fields.values() if isinstance(f, dict) and f.get('source')), 'fields': len(fields)}
    db = sqlite3.connect(sem)
    try:
        built = {t: db.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for (t,) in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'raw_%' AND name NOT LIKE 'sqlite_%'")
            if t in ('patient', 'condition', 'measurement', 'medication', 'visit', 'encounter', 'claim', 'claim_line', 'enrollment_span')}
    finally:
        db.close()
    return {'schema': VERSION, 'kind': kind, 'source': str(source), 'semantic': str(sem), 'entities': entities,
            'unmapped_source_tables': mapping.get('unmapped_source_tables'), 'built_rows': built,
            'invariants_passed': bool(inv.get('passed')), 'invariants_failed': inv.get('failed'), 'review_decisions': decisions or {}, 'new_paid_api_calls': 0}
