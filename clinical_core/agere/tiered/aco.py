"""The ACO job's governed tables: CMS-HCC risk scores and contract terms, readable by SQL like any semantic-layer table.

  risk_score    one row per patient and diagnosis (collection) year for members with a Medicare or dual member month that
                year: V28 and V24 raw scores, the payment-year risk score, the demographic-only score, the count of V28
                payment HCCs and the segment used. Computed by the CMS-HCC engine (clinical/risk_adjustment.py, public CMS
                model tables listed in config/agere/risk_adjustment/manifest.json), not by a model.
  patient_hcc   one row per patient, year and V28 payment HCC, with its label and the ICD-10 codes behind it.
  aco_contract  one row per contract and performance year from config/agere/contracts/*.json (schema agere-aco-contract/1):
                payer, benchmark PMPM in dollars, minimum savings rate, sharing rate, whether the quality standard is met,
                and whether the terms are authored for a demonstration.

The two risk tables are computed once per warehouse version and kept in a cache file beside the saved recipes; a new
warehouse version gets a new file. They are built from the semantic layer itself, so on a mapped warehouse they are not
available (the engine reads this layer's tables directly) and recipes that read them do not apply there.

    python -m clinical_core.agere.tiered.aco build     # (re)build the risk-score cache for the current warehouse
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

from . import store

CACHE = Path('artifacts/agere-tiered-core/governed')
CONTRACTS = Path('config/agere/contracts')
YEARS = (2026, 2027, 2028, 2029, 2030)
PAYMENT_YEAR = 2026   # the latest payment-year parameters in config/agere/risk_adjustment/payment_parameters.json


def _version():
    """The warehouse's own version, read from its meta table directly (a store connection would install this module again)."""
    raw = sqlite3.connect(Path(store.path()).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        rows = dict(raw.execute("SELECT key, value FROM meta WHERE key IN ('version', 'source_digest')"))
    finally:
        raw.close()
    return f"{rows.get('version')}:{str(rows.get('source_digest'))[:16]}"


def cache_path():
    safe = re.sub(r'[^A-Za-z0-9._-]+', '_', _version())
    return CACHE / f'risk-{safe}.sqlite3'


def build(path=None):
    """Score every member with a Medicare or dual member month, year by year. -> the cache file."""
    from ..clinical import risk_adjustment as ra
    out = Path(path or cache_path())
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix('.building')
    if tmp.exists(): tmp.unlink()
    src = sqlite3.connect(Path(store.path()).resolve().as_uri() + '?mode=ro', uri=True)
    db = sqlite3.connect(tmp)
    db.execute('CREATE TABLE risk_score(patient_id TEXT, year TEXT, payment_year INTEGER, segment TEXT, v28_raw REAL, v24_raw REAL, '
               'payment_raf REAL, demographic_raf REAL, hcc_count INTEGER, PRIMARY KEY (patient_id, year))')
    db.execute('CREATE TABLE patient_hcc(patient_id TEXT, year TEXT, hcc INTEGER, label TEXT, codes TEXT, PRIMARY KEY (patient_id, year, hcc))')
    for year in YEARS:
        months = src.execute("SELECT patient_id, MAX(line_of_business = 'dual') FROM member_month WHERE substr(month, 1, 4) = ? "
                             "AND line_of_business IN ('medicare', 'dual') GROUP BY patient_id", (str(year),)).fetchall()
        for segment, ids in (('community_non_dual', [p for p, d in months if not d]), ('community_full_dual', [p for p, d in months if d])):
            if not ids: continue
            report = ra.execute(store.path(), ids, {'year': year, 'coverage': 'all', 'segment': segment, 'payment_year': PAYMENT_YEAR, 'top': 500})
            for p in report.get('patients', []):
                db.execute('INSERT OR REPLACE INTO risk_score VALUES (?,?,?,?,?,?,?,?,?)',
                           (p['patient_id'], str(year), PAYMENT_YEAR, p.get('segment'), p['raw'].get('V28'), p['raw'].get('V24'),
                            p.get('payment_raf'), p.get('demographic_payment_raf'), len(p['hccs'].get('V28', []))))
                for h in p['hccs'].get('V28', []):
                    db.execute('INSERT OR REPLACE INTO patient_hcc VALUES (?,?,?,?,?)',
                               (p['patient_id'], str(year), h['hcc'], h['label'], ','.join(h['codes'])))
    db.execute("CREATE TABLE built(key TEXT, value TEXT)")
    db.executemany('INSERT INTO built VALUES (?, ?)', [('schema_version', _version()), ('payment_year', str(PAYMENT_YEAR)),
                                                       ('engine', 'clinical_core/agere/clinical/risk_adjustment.py')])
    db.commit(); db.close(); src.close()
    tmp.replace(out)
    return out


def contracts():
    """-> rows (contract_id, payer_id, year, benchmark_pmpm_dollars, minimum_savings_rate, sharing_rate, quality_standard_met, authored_synthetic)."""
    rows = []
    for f in sorted(CONTRACTS.glob('*.json')):
        try:
            c = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if c.get('schema') != 'agere-aco-contract/1': continue
        for year, t in sorted((c.get('years') or {}).items()):
            rows.append((c['id'], c['payer_id'], str(year), float(t['benchmark_pmpm_dollars']), float(t['minimum_savings_rate']),
                         float(t['sharing_rate']), int(bool(t.get('quality_standard_met'))), int(bool(c.get('authored_synthetic')))))
    return rows


_ready = {}


def install(db, cohort=None):
    """Create the governed ACO views on one connection (before it is made read-only). Called by store.connect."""
    db.execute('CREATE TEMP TABLE aco_contract(contract_id TEXT, payer_id TEXT, year TEXT, benchmark_pmpm_dollars REAL, '
               'minimum_savings_rate REAL, sharing_rate REAL, quality_standard_met INTEGER, authored_synthetic INTEGER)')
    db.executemany('INSERT INTO aco_contract VALUES (?,?,?,?,?,?,?,?)', contracts())
    if store.mapping() or store.foreign(): return   # the risk engine reads this semantic layer's own tables
    path = cache_path()
    key = str(path)
    if key not in _ready:
        try:
            if not path.exists(): build(path)
            _ready[key] = True
        except (sqlite3.Error, KeyError, ValueError):   # a warehouse without member months or diagnoses: no risk tables
            _ready[key] = False
            path.with_suffix('.building').unlink(missing_ok=True)
    if not _ready[key]: return
    db.execute(f"ATTACH DATABASE '{path.resolve().as_uri()}?mode=ro' AS gov")
    inside = ' WHERE patient_id IN (SELECT patient_id FROM cohort)' if cohort is not None else ''
    db.execute('CREATE TEMP VIEW risk_score AS SELECT * FROM gov.risk_score' + inside)
    db.execute('CREATE TEMP VIEW patient_hcc AS SELECT * FROM gov.patient_hcc' + inside)


if __name__ == '__main__':
    if sys.argv[1:2] == ['build']:
        print(build())
    else:
        print(__doc__)
