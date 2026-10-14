"""A claims-data-lake fixture (X12 837/835 flattened, snake_case abbreviations, compact YYYYMMDD dates,
claim adjustments as repeated claim numbers, single-letter status codes, member surrogate keys).

Written after the matcher was tuned on the OMOP and Clarity-like fixtures and scored once, unchanged,
as a check on generalization. It has no encounter table, so `visit` is expected to stay unmapped.
"""
from pathlib import Path
import random
import sqlite3

from .fixtures import _npi

DDL = """
CREATE TABLE mbr_dim(mbr_sk INTEGER PRIMARY KEY, member_key TEXT, gndr_cd TEXT, dob_dt TEXT, mbr_zip5 TEXT, st_cd TEXT, race_desc TEXT);
CREATE TABLE elig_span(elig_sk INTEGER PRIMARY KEY, mbr_sk INTEGER, lob_desc TEXT, eff_from_dt TEXT, eff_thru_dt TEXT, elig_stat TEXT);
CREATE TABLE prov_dim(prov_sk INTEGER PRIMARY KEY, rendering_npi TEXT, prov_tin TEXT, prov_spclty_desc TEXT, prov_nm TEXT);
CREATE TABLE clm_hdr_837(src_row_key TEXT PRIMARY KEY, clm_nbr TEXT, clm_freq_cd TEXT, adj_seq INTEGER, mbr_sk INTEGER, bill_prov_sk INTEGER,
  from_dos TEXT, clm_stat_cd TEXT, chrg_amt_usd REAL, alwd_amt_usd REAL, pd_amt_usd REAL, mbr_liab_usd REAL, adjud_dt TEXT, rcvd_dt TEXT,
  payer_nm TEXT);
CREATE TABLE clm_ln_837(clm_nbr TEXT, ln_nbr INTEGER, proc_cd TEXT, proc_mod TEXT, rev_cd TEXT, svc_unit_qty REAL, ln_chrg_usd REAL, ln_from_dos TEXT);
CREATE TABLE dx_837(clm_nbr TEXT, mbr_sk INTEGER, dx_seq INTEGER, icd10_dx_cd TEXT, from_dos TEXT);
CREATE TABLE rx_clm(rx_clm_nbr TEXT PRIMARY KEY, mbr_sk INTEGER, ndc11 TEXT, drug_nm TEXT, fill_dt TEXT, days_sply INTEGER, rx_pd_amt_usd REAL);
CREATE TABLE lab_rslt(lab_rslt_sk INTEGER PRIMARY KEY, mbr_sk INTEGER, loinc_cd TEXT, tst_nm TEXT, rslt_num REAL, rslt_unit TEXT, coll_dt TEXT);
CREATE TABLE cpt_ref(proc_cd TEXT PRIMARY KEY, short_desc TEXT);
"""

TRUTH = {
    'patient': ('mbr_dim', {'patient_id': 'member_key', 'sex_reported': 'gndr_cd', 'birth_date': 'dob_dt', 'zip': 'mbr_zip5', 'state': 'st_cd',
                            'race': 'race_desc'}),
    'coverage': ('elig_span', {'patient_id': 'mbr_sk', 'coverage_type': 'lob_desc', 'event_date': 'eff_from_dt', 'status': 'elig_stat'}),
    'clinician': ('prov_dim', {'clinician_id': 'prov_sk', 'npi': 'rendering_npi', 'specialty': 'prov_spclty_desc'}),
    'claim': ('clm_hdr_837', {'record_id': 'src_row_key', 'claim_id': 'clm_nbr', 'patient_id': 'mbr_sk', 'service_date': 'from_dos',
                              'status': 'clm_stat_cd', 'billed_cents': 'chrg_amt_usd', 'allowed_cents': 'alwd_amt_usd',
                              'payer_paid_cents': 'pd_amt_usd', 'patient_responsibility_cents': 'mbr_liab_usd', 'event_date': 'adjud_dt',
                              'available_date': 'rcvd_dt', 'payer': 'payer_nm'}),
    'claim_line': ('clm_ln_837', {'claim_id': 'clm_nbr', 'line_number': 'ln_nbr', 'service_code': 'proc_cd', 'revenue_code': 'rev_cd',
                                  'units': 'svc_unit_qty', 'charge_cents': 'ln_chrg_usd', 'service_date': 'ln_from_dos'}),
    'condition': ('dx_837', {'patient_id': 'mbr_sk', 'icd10': 'icd10_dx_cd', 'event_date': 'from_dos'}),
    'medication': ('rx_clm', {'record_id': 'rx_clm_nbr', 'patient_id': 'mbr_sk', 'ndc': 'ndc11', 'drug_name': 'drug_nm', 'event_date': 'fill_dt'}),
    'measurement': ('lab_rslt', {'patient_id': 'mbr_sk', 'loinc': 'loinc_cd', 'name': 'tst_nm', 'value': 'rslt_num', 'unit': 'rslt_unit',
                                 'event_date': 'coll_dt'}),
}
UNMAPPED_TABLES = ['cpt_ref']


def claims_lake(path, n_members=30, seed=11):
    rng = random.Random(seed)
    path = Path(path)
    path.unlink(missing_ok=True)
    db = sqlite3.connect(path)
    db.executescript(DDL)
    dt = lambda y, m, d: f'{y}{m:02d}{d:02d}'
    provs = [(i + 1, _npi(rng), f'{rng.randint(10, 99)}{rng.randint(1000000, 9999999)}', rng.choice(['FAMILY PRACTICE', 'CARDIOLOGY', 'INTERNAL MEDICINE']),
              f'PROV {i}') for i in range(8)]
    db.executemany('INSERT INTO prov_dim VALUES (?,?,?,?,?)', provs)
    cpts = [('99213', 'OFFICE O/P EST LOW'), ('99214', 'OFFICE O/P EST MOD'), ('83036', 'HEMOGLOBIN GLYCOSYLATED A1C'), ('G0439', 'ANNUAL WELLNESS'),
            ('93000', 'ECG ROUTINE')]
    db.executemany('INSERT INTO cpt_ref VALUES (?,?)', cpts)
    dxs = ['E119', 'E1165', 'I10', 'E785', 'N183', 'Z0000']
    drugs = [('00093104801', 'METFORMIN HCL 500 MG TAB'), ('68180051301', 'LISINOPRIL 10 MG TAB'), ('00071015523', 'ATORVASTATIN 40 MG TAB')]
    row = lab = rx = 0
    for i in range(n_members):
        sk = 9000 + i * 3
        db.execute('INSERT INTO mbr_dim VALUES (?,?,?,?,?,?,?)', (sk, f'M{120000 + i:07d}', rng.choice('FM'), dt(rng.randint(1940, 2004), rng.randint(1, 12), rng.randint(1, 28)),
                                                                  f'3{rng.randint(1000, 9999)}', 'TN', rng.choice(['WHITE', 'BLACK', 'ASIAN', 'UNKNOWN'])))
        db.execute('INSERT INTO elig_span VALUES (?,?,?,?,?,?)', (i + 1, sk, rng.choice(['COMMERCIAL', 'MEDICARE ADVANTAGE', 'MEDICAID MANAGED CARE']),
                                                                  dt(2027, 1, 1), dt(2028, 12, 31), 'A'))
        for c in range(rng.randint(2, 5)):
            clm = f'{2028}{i:03d}{c:02d}'
            y, m, d = 2028, rng.randint(1, 11), rng.randint(1, 28)
            chg = round(rng.uniform(90, 3000), 2); alw = round(chg * 0.6, 2)
            versions = [('1', 'P')] if rng.random() < 0.8 else [('1', 'D'), ('7', 'P')]
            for seq, (freq, stat) in enumerate(versions, 1):
                row += 1
                pd = round(alw * 0.8, 2) if stat == 'P' else 0.0
                db.execute('INSERT INTO clm_hdr_837 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (f'R{row:08d}', clm, freq, seq, sk, rng.choice(provs)[0], dt(y, m, d), stat, chg, alw, pd, round(alw - pd, 2) if stat == 'P' else 0.0,
                            dt(y, m + 1, min(d, 28)), dt(y, m, min(d + 2, 28)), rng.choice(['ACME HEALTH PLAN', 'STATE MEDICAID'])))
            n = rng.randint(1, 3)
            for ln in range(1, n + 1):
                db.execute('INSERT INTO clm_ln_837 VALUES (?,?,?,?,?,?,?,?)', (clm, ln, rng.choice(cpts)[0], rng.choice([None, '25']),
                                                                               rng.choice([None, '0510']), 1.0, round(chg / n, 2), dt(y, m, d)))
            for s, code in enumerate(rng.sample(dxs, 2), 1):
                db.execute('INSERT INTO dx_837 VALUES (?,?,?,?,?)', (clm, sk, s, code, dt(y, m, d)))
            if rng.random() < 0.5:
                rx += 1
                ndc, name = rng.choice(drugs)
                db.execute('INSERT INTO rx_clm VALUES (?,?,?,?,?,?,?)', (f'RX{rx:06d}', sk, ndc, name, dt(y, m, d), 30, round(rng.uniform(4, 40), 2)))
            for loinc, name, unit, lo, hi in rng.sample([('4548-4', 'HBA1C', '%', 5, 11), ('2345-7', 'GLUCOSE', 'mg/dL', 70, 250), ('2160-0', 'CREAT', 'mg/dL', 0.6, 2.4)], 2):
                lab += 1
                db.execute('INSERT INTO lab_rslt VALUES (?,?,?,?,?,?,?)', (lab, sk, loinc, name, round(rng.uniform(lo, hi), 1), unit, dt(y, m, d)))
    db.commit(); db.close()
    return TRUTH
