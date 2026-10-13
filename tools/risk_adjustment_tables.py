"""Extract the public CMS-HCC model tables Agere's risk_adjustment engine needs into config/agere/risk_adjustment/.

Inputs are the unmodified zip archives downloaded from cms.gov (public domain, U.S. Government work). Every written table
records its source archive, member path, the archive's sha256 and the payment year it belongs to (manifest.json).

    python -I tools/risk_adjustment_tables.py --downloads DIR [--out config/agere/risk_adjustment]

DIR must contain (cms.gov file names; the URLs are in SOURCES below):
    2026-midyear-final-model-software-python.zip   (CMS-HCC V28 PY2026 Python package, payment-year 2026 midyear/final)
    2025-midyear_final-model-software.zip          (SAS software: CMS-HCC V24 V2425.86.P1 and V28 V2825.115.T1, PY2025)
    2025-midyear_final-icd-10-mappings.zip-0       (PY2025 midyear/final ICD-10-CM mappings; only checked, not copied)
    2026-midyear-final-icd-10-mappings.zip         (PY2026 final ICD-10-CM mappings; only checked, not copied)
    cpt2025.zip / cpt2026.zip                      (Medicare Risk Adjustment Eligible CPT/HCPCS lists, CY2025 Q4 / CY2026 Q3)

Only code values are copied from the CPT/HCPCS lists (no AMA descriptors). Nothing here is invented: when an expected member
is missing the script stops.
"""
import argparse, csv, hashlib, io, json, re, sys, zipfile
from pathlib import Path

SOURCES = {
    'py2026_python': ('2026-midyear-final-model-software-python.zip',
                      'https://www.cms.gov/files/zip/2026-midyear-final-model-software-python.zip'),
    'py2025_sas': ('2025-midyear_final-model-software.zip', 'https://www.cms.gov/files/zip/2025-midyear/final-model-software.zip'),
    'py2025_mappings': ('2025-midyear_final-icd-10-mappings.zip-0',
                        'https://www.cms.gov/files/zip/2025-midyear/final-icd-10-mappings.zip-0'),
    'py2026_mappings': ('2026-midyear-final-icd-10-mappings.zip', 'https://www.cms.gov/files/zip/2026-midyear-final-icd-10-mappings.zip'),
    'cpt_cy2025': ('cpt2025.zip', 'https://www.cms.gov/files/zip/2025-medicare-risk-adjustment-eligible-cpt-hcpcs-codes.zip'),
    'cpt_cy2026': ('cpt2026.zip', 'https://www.cms.gov/files/zip/2026-medicare-risk-adjustment-eligible-cpt-hcpcs-codes.zip-0'),
}
PAGES = {
    'py2026': 'https://www.cms.gov/medicare/payment/medicare-advantage-rates-statistics/risk-adjustment/2026-model-software-icd-10-mappings',
    'py2025': 'https://www.cms.gov/medicare/payment/medicare-advantage-rates-statistics/risk-adjustment/2025-model-software/icd-10-mappings',
    'cpt': 'https://www.cms.gov/medicare/health-plans/medicareadvtgspecratestats/risk-adjustors-items/cpt-hcpcs',
}


def sha(b): return hashlib.sha256(b).hexdigest()


def inner(zf, name):
    """Member bytes; nested zip members are addressed as 'outer.zip/member'."""
    if '/' in name and name.split('/')[0].endswith('.zip'):
        head, rest = name.split('/', 1)
        return inner(zipfile.ZipFile(io.BytesIO(zf.read(head))), rest)
    return zf.read(name)


def text(b):
    try: s = b.decode('utf-8-sig')
    except UnicodeDecodeError: s = b.decode('cp1252')  # some CMS SAS-era files are Windows-1252 (en dashes in labels)
    return s.replace('\r\n', '\n').replace('\r', '\n')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--downloads', required=True)
    ap.add_argument('--out', default='config/agere/risk_adjustment')
    a = ap.parse_args()
    d, out = Path(a.downloads), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    archives, digests = {}, {}
    for key, (fname, url) in SOURCES.items():
        p = d / fname
        if not p.exists(): sys.exit(f'missing {p} ({url})')
        raw = p.read_bytes()
        digests[key] = {'file': fname, 'url': url, 'sha256': sha(raw), 'bytes': len(raw)}
        archives[key] = zipfile.ZipFile(io.BytesIO(raw))
    tables = {}

    def write(name, data, source, member, payment_year, note=None):
        (out / name).write_bytes(data)
        tables[name] = {'source': source, 'member': member, 'payment_year': payment_year, 'sha256': sha(data),
                        'archive_sha256': digests[source]['sha256'], 'url': digests[source]['url'], **({'note': note} if note else {})}

    # ---- V28 (2024 CMS-HCC model), PY2026 Python package: tables copied byte for byte ----------------------------------
    pkg = 'CMS_HCC_v28_2026_T_package_v3.zip'
    base = 'software/CMS_HCC_v28/data/input/internal/'
    for member, name in [('ICD10_CC_mappings_CMS_HCC_2026_v28.csv', 'v28_icd10_cc_py2026.csv'),
                         ('V28_HCC_Hierarchies.csv', 'v28_hierarchies.csv'), ('V28_CE_Relative_Factors.csv', 'v28_relative_factors.csv'),
                         ('V28_Diagnosis_Categories.csv', 'v28_diagnosis_categories.csv'), ('V28_Interactions.csv', 'v28_interactions.csv')]:
        write(name, inner(archives['py2026_python'], f'{pkg}/{base}{member}'), 'py2026_python', f'{pkg}/{base}{member}', 2026)

    # ---- PY2025 SAS packages: V24 (2020 CMS-HCC model) and V28 labels / coefficient cross-check ----------------------
    v24z, v28z = 'CMS-HCC software V2425.86.P1.zip', 'CMS-HCC software V2825.115.T1.zip'
    sas = archives['py2025_sas']
    rows = ['ICD10,CC,ASSIGNMENT']
    for line in text(inner(sas, f'{v24z}/F2425P1M.TXT')).split('\n'):
        if not line.strip(): continue
        parts = line.split('\t')
        rows.append(f'{parts[0].strip()},{int(parts[1])},{(parts[2].strip() if len(parts) > 2 else "") or "1"}')
    write('v24_icd10_cc_py2025.csv', ('\n'.join(rows) + '\n').encode(), 'py2025_sas', f'{v24z}/F2425P1M.TXT', 2025,
          'Tab-separated SAS format source (ICD10, CC, D = second assignment) rewritten as CSV; codes and CCs unchanged.')
    write('v24_relative_factors.csv', text(inner(sas, f'{v24z}/C2419P1M.csv')).encode(), 'py2025_sas', f'{v24z}/C2419P1M.csv', 2025,
          'CRLF line endings normalised.')
    hier = ['HCC,EXCLUDES']
    for m in re.finditer(r'%SET0\(CC=(\d+)\s*,\s*HIER=%STR\(([^)]*)\)\s*\)', text(inner(sas, f'{v24z}/V24H86H1.TXT'))):
        hier.append(f'HCC{m.group(1)},' + ' '.join(f'HCC{x.strip()}' for x in m.group(2).split(',')))
    if len(hier) < 30: sys.exit('V24 hierarchy parse failed')
    write('v24_hierarchies.csv', ('\n'.join(hier) + '\n').encode(), 'py2025_sas', f'{v24z}/V24H86H1.TXT', 2025,
          'Parsed from the %SET0 statements in SAS order (order matters: SAS zeroes sequentially).')
    for z, member, name, py in [(v24z, 'V24H86L1.TXT', 'v24_labels.csv', 2025), (v28z, 'V28115L3.TXT', 'v28_labels.csv', 2025)]:
        labels = ['HCC,LABEL'] + [f'HCC{n},"{lab.strip()}"' for n, lab in re.findall(r'HCC(\d+)\s*="([^"]*)"', text(inner(sas, f'{z}/{member}')))]
        write(name, ('\n'.join(labels) + '\n').encode(), 'py2025_sas', f'{z}/{member}', py, 'HCC labels parsed from the SAS LABEL statements.')
    # V24 model structure (diagnostic categories, interactions, mandatory edits) is SAS code, not a table: copy the source text.
    for member in ('V2425P1M.TXT', 'V2425P1P.TXT', 'V24I0ED3.TXT'):
        write(f'v24_sas_{member.lower()}', text(inner(sas, f'{v24z}/{member}')).encode(), 'py2025_sas', f'{v24z}/{member}', 2025,
              'SAS source kept for audit; risk_adjustment.py transcribes its categories, interactions and edits.')
    write('v28_sas_v2825t1m.txt', text(inner(sas, f'{v28z}/V2825T1M.TXT')).encode(), 'py2025_sas', f'{v28z}/V2825T1M.TXT', 2025,
          'SAS source kept for audit (CC223 heart-failure patch, categories, count variables).')

    # cross-check: PY2025 SAS V28 community coefficients equal the PY2026 Python ones (same 2024 model calibration)
    sas_coef = {r['_NAME_']: float(r['COL1']) for r in csv.DictReader(io.StringIO(text(inner(sas, f'{v28z}/C2824T2N.csv'))))}
    py_coef = list(csv.DictReader(io.StringIO(text((out / 'v28_relative_factors.csv').read_bytes()))))
    seg = {'COMMUNITY_NA': 'CNA_', 'COMMUNITY_ND': 'CND_', 'COMMUNITY_FBA': 'CFA_', 'COMMUNITY_FBD': 'CFD_', 'COMMUNITY_PBA': 'CPA_',
           'COMMUNITY_PBD': 'CPD_', 'INSTITUTIONAL': 'INS_'}
    checked = mism = 0
    for r in py_coef:
        for col, pre in seg.items():
            if r[col] in ('', None): continue
            k = pre + r['Variable'].strip()
            if k in sas_coef:
                checked += 1
                if abs(sas_coef[k] - float(r[col])) > 1e-9: mism += 1
    if mism: sys.exit(f'V28 coefficients differ between PY2025 SAS and PY2026 Python: {mism} of {checked}')

    # ---- eligible CPT/HCPCS (codes only) --------------------------------------------------------------------------------
    for key, year in (('cpt_cy2025', 2025), ('cpt_cy2026', 2026)):
        z = archives[key]
        member = next(n for n in z.namelist() if n.lower().endswith('.csv'))
        lines = list(csv.reader(io.StringIO(z.read(member).decode('latin-1'))))
        start = next(i for i, r in enumerate(lines) if r and r[0].startswith('HCPCS/CPT Code')) + 1
        codes = sorted({r[0].strip() for r in lines[start:] if r and r[0].strip() and (len(r) < 3 or r[2].strip().lower() == 'yes')})
        if len(codes) < 1000: sys.exit(f'{member}: only {len(codes)} codes parsed')
        write(f'eligible_cpt_hcpcs_cy{year}.txt', ('\n'.join(codes) + '\n').encode(), key, member, year,
              f'{len(codes)} codes marked included; descriptors omitted (CPT descriptors are AMA copyright).')

    # ---- the mapping workbooks are checked against the model mappings (code sets agree), not copied ------------------
    checks = {'v28_py2025_sas_vs_py2026_python_coefficients': {'compared': checked, 'mismatches': mism}}
    for key, col in (('py2025_mappings', 'CMS-HCC Model Category V24'), ('py2026_mappings', 'CMS-HCC Model Category V28')):
        z = archives[key]
        member = next(n for n in z.namelist() if n.lower().endswith('.csv'))
        rows = list(csv.reader(io.StringIO(z.read(member).decode('latin-1'))))
        hdr = [c.replace('\n', ' ').strip() for c in rows[3]]
        ci, pi = hdr.index(col), next(i for i, h in enumerate(hdr) if h.startswith(col + ' for '))
        pay = {(r[0], r[ci]) for r in rows[4:] if r and r[0] and r[ci] and r[pi] == 'Yes'}
        model = {(r['ICD10'], str(int(float(r['CC'])))) for r in csv.DictReader(io.StringIO(text((out / (
            'v24_icd10_cc_py2025.csv' if 'V24' in col else 'v28_icd10_cc_py2026.csv')).read_bytes())))}
        checks[f'{key}_{col.split()[-1]}'] = {'workbook_payment_pairs': len(pay), 'software_pairs': len(model),
                                               'workbook_not_in_software': len(pay - model), 'software_not_in_workbook': len(model - pay),
                                               'member': member}
    manifest = {
        'schema': 'agere-risk-adjustment-tables/1',
        'status': 'Public-domain CMS tables (U.S. Government work) extracted unmodified or reformatted only as noted.',
        'archives': digests, 'pages': PAGES, 'tables': tables, 'checks': checks,
        'payment_parameters': json.loads((out / 'payment_parameters.json').read_text()) if (out / 'payment_parameters.json').exists() else None,
    }
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=1) + '\n')
    print(json.dumps(checks, indent=1))


if __name__ == '__main__':
    main()
