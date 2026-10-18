"""Source-to-model parity for native workspaces: did each delivered feed reach the modeled semantic layer?

    check(landing_path, semantic_path, warehouse_path=None) -> report dict

For every source feed / instance / delivery year and each modeled family (labs, vitals, notes, encounters,
appointments, problem list, claims, fills, enrollment spans), compare the native landing rows with the landing rows
that reached a modeled canonical record (through the record's _lineage: landing table, file, row) and the persons
behind them. A group whose row or person coverage falls short by more than GAP_THRESHOLD (5%) is flagged with the
source named (feed, instance, year, landing table), so a feed the staging silently drops shows up at build time.

Reading of the numbers:
- landing_rows: rows of the landing table in that group. When the table mixes record kinds (FHIR Observation
  categories, flowsheet rows, document classes), kinds that no record of the family uses anywhere are left out of the
  denominator and reported as rows_of_unmodeled_kinds, so a family is not flagged for rows it never models.
- covered_rows: of those, rows named in the lineage of a record of the family (duplicates collapsed into one record keep
  every copy's lineage, so they count as covered). Records come from the received warehouse when present (every
  version, superseded ones included), else from the semantic layer's record table.
- landing_persons / covered_persons: distinct native patient keys (the table's own patient column) among all rows and
  among covered rows; modeled_persons: distinct Agere person ids of the covering records.
- year: the delivery-period year of the file (period folder, else the date in its name), not the event year.
The check annotates: it never blocks or alters a build.
"""
from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
import sqlite3

VERSION = 'source-parity/1'
GAP_THRESHOLD = 0.05
MIN_ROWS = 20  # smaller groups are reported but not flagged

# family -> (canonical streams, [(landing table, native patient column or None, record-kind column or None)])
FAMILIES = {
    'labs': (('labs',), [('ehr_epic_order_results', 'pat_id', 'component_id'),
                         ('ehr_athena_clinicalresultobservation', None, None),
                         ('ehr_ecw_labdatadetail', None, None),
                         ('hl7_observation', 'patient_id', None),
                         ('fhir_observation', 'patient_id', 'category_code')]),
    'vitals': (('vitals',), [('ehr_epic_ip_flwsht_meas', None, 'flo_meas_id'),
                             ('ehr_athena_vitalsign', None, 'key'),
                             ('ehr_ecw_vitals', None, 'propid'),
                             ('fhir_observation', 'patient_id', 'category_code')]),
    'notes': (('notes',), [('ehr_epic_hno_info', 'pat_id', 'ip_note_type_c'),
                           ('ehr_athena_document', 'patientid', 'documentclass'),
                           ('fhir_documentreference', 'patient_id', None)]),
    'encounters': (('encounters',), [('ehr_epic_pat_enc', 'pat_id', 'enc_type_c'),
                                     ('ehr_athena_clinicalencounter', 'patientid', None),
                                     ('ehr_ecw_enc', 'patientid', None),
                                     ('fhir_encounter', 'patient_id', None)]),
    'appointments': (('appointments',), [('ehr_epic_f_sched_appt', 'pat_id', None),
                                         ('ehr_ecw_enc', 'patientid', None),
                                         ('fhir_appointment', 'patient_id', None)]),
    'problem_list': (('conditions',), [('ehr_epic_problem_list', 'pat_id', None),
                                       ('ehr_athena_patientproblem', 'patientid', None),
                                       ('ehr_ecw_problemlist', 'patientid', None),
                                       ('fhir_condition', 'patient_id', 'category_code')]),
    'claims': (('payer_medical_claims', 'payer_medical_claim_lines', 'medical_claim_submissions'),
               [('x12_837_claim', 'subscriber_member_id', None),
                ('payers_medical', 'member_id', None),
                ('cclf_parta_claims_header', 'bene_mbi_id', None),
                ('cclf_partb_physicians', 'bene_mbi_id', None)]),
    'fills': (('payer_pharmacy_claims',), [('ncpdp_claim', 'cardholder_id', None),
                                           ('payers_pharmacy', 'member_id', None),
                                           ('cclf_partd_claims', 'bene_mbi_id', None)]),
    'spans': (('enrollment_spans',), [('x12_834_member', 'member_id', None),
                                      ('payers_eligibility', 'member_id', None),
                                      ('cclf_enrollment', 'current_bene_mbi_id', None)]),
}


def _ro(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)


def _coverage(source, streams_by_family, tables_by_family, from_warehouse):
    """{(family, table): {(file, row): person id}} from record lineage."""
    family_of_stream = defaultdict(list)
    for fam, streams in streams_by_family.items():
        for s in streams:
            family_of_stream[s].append(fam)
    streams = sorted(family_of_stream)
    q = ("SELECT stream, patient, json_extract(payload, '$._lineage') FROM records WHERE stream IN ({})" if from_warehouse else
         "SELECT stream, patient_id, json_extract(payload, '$._lineage') FROM record WHERE stream IN ({})")
    cov = defaultdict(dict)
    files = {}
    for stream, pid, lin in source.execute(q.format(','.join('?' * len(streams))), streams):
        if not lin:
            continue
        try:
            entries = json.loads(lin)
        except ValueError:
            continue
        for e in entries if isinstance(entries, list) else []:
            if not isinstance(e, dict):
                continue
            t, f = e.get('table'), e.get('file', e.get('_file'))  # clinical adapters write file/row, claims _file/_row
            r = e.get('row', e.get('_row'))
            if t is None or f is None or r is None:
                continue
            f = files.setdefault(f, f)
            for fam in family_of_stream[stream]:
                if t in tables_by_family[fam]:
                    cov[(fam, t)][(f, int(r))] = pid
    return cov


def check(landing_path, semantic_path, warehouse_path=None, *, threshold=GAP_THRESHOLD, min_rows=MIN_ROWS, families=None):
    from ..ingest.delivery import describe_path
    fams = {k: v for k, v in FAMILIES.items() if families is None or k in families}
    land = _ro(landing_path)
    try:
        land_tables = {r[0] for r in land.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        warehouse = Path(warehouse_path) if warehouse_path and Path(warehouse_path).is_file() else None
        src = _ro(warehouse) if warehouse else _ro(semantic_path)
        try:
            cov = _coverage(src, {f: v[0] for f, v in fams.items()}, {f: {t for t, _, _ in v[1]} for f, v in fams.items()},
                            bool(warehouse))
        finally:
            src.close()
        year_cache = {}

        def year_of(f):
            if f not in year_cache:
                p = describe_path(f).get('period') if f else None
                year_cache[f] = p[:4] if p else None
            return year_cache[f]

        groups, unmodeled = [], []
        for fam, (streams, sources) in fams.items():
            for table, pcol, kcol in sources:
                if table not in land_tables:
                    continue
                cols = {r[1] for r in land.execute(f'PRAGMA table_info("{table}")')}
                pcol = pcol if pcol in cols else None
                kcol = kcol if kcol in cols else None
                covered = cov.get((fam, table), {})
                sel = ['_feed', '_instance', '_file', '_row', pcol or 'NULL', kcol or 'NULL']
                rows = land.execute(f'SELECT {", ".join(sel)} FROM "{table}"').fetchall()
                kinds = None
                if kcol:
                    kinds = {k for f_, i_, fl, rw, p_, k in rows if (fl, rw) in covered}
                agg = defaultdict(lambda: {'landing_rows': 0, 'covered_rows': 0, 'unmodeled_kind_rows': 0,
                                           'lp': set(), 'cp': set(), 'mp': set()})
                for feed, inst, fl, rw, pk, k in rows:
                    g = agg[(feed, inst, year_of(fl))]
                    if kinds is not None and k not in kinds:
                        g['unmodeled_kind_rows'] += 1
                        continue
                    g['landing_rows'] += 1
                    if pk is not None:
                        g['lp'].add(pk)
                    hit = covered.get((fl, rw))
                    if (fl, rw) in covered:
                        g['covered_rows'] += 1
                        if pk is not None:
                            g['cp'].add(pk)
                        if hit is not None:
                            g['mp'].add(hit)
                if kinds is not None:
                    left = sorted({str(k) for *_, k in rows if k not in kinds})
                    if left:
                        unmodeled.append({'family': fam, 'table': table, 'kind_column': kcol, 'kinds': left[:25],
                                          'kinds_total': len(left)})
                for (feed, inst, year), g in sorted(agg.items(), key=lambda kv: tuple(str(x) for x in kv[0])):
                    n, c = g['landing_rows'], g['covered_rows']
                    lp, cp = len(g['lp']), len(g['cp'])
                    row_gap = round(1 - c / n, 4) if n else None
                    person_gap = round(1 - cp / lp, 4) if lp else None
                    flagged = bool(n >= min_rows and ((row_gap or 0) > threshold or (person_gap or 0) > threshold))
                    groups.append({'family': fam, 'streams': list(streams), 'table': table, 'feed': feed, 'instance': inst,
                                   'year': year, 'landing_rows': n, 'covered_rows': c, 'row_gap': row_gap,
                                   'landing_persons': lp if pcol else None, 'covered_persons': cp if pcol else None,
                                   'person_gap': person_gap, 'modeled_persons': len(g['mp']),
                                   'rows_of_unmodeled_kinds': g['unmodeled_kind_rows'], 'flagged': flagged,
                                   'source': f'{feed}/{inst or "-"}/{year or "-"} {table}'})
    finally:
        land.close()
    flagged = [g for g in groups if g['flagged']]
    by_family = {}
    for g in groups:
        b = by_family.setdefault(g['family'], {'landing_rows': 0, 'covered_rows': 0, 'groups': 0, 'flagged': 0})
        b['landing_rows'] += g['landing_rows']
        b['covered_rows'] += g['covered_rows']
        b['groups'] += 1
        b['flagged'] += int(g['flagged'])
    for b in by_family.values():
        b['row_gap'] = round(1 - b['covered_rows'] / b['landing_rows'], 4) if b['landing_rows'] else None
    return {'version': VERSION, 'threshold': threshold, 'min_rows': min_rows,
            'lineage_source': 'received warehouse' if warehouse else 'semantic record table',
            'groups': groups, 'flagged': [g['source'] + f" [{g['family']}] rows {g['covered_rows']}/{g['landing_rows']}"
                                          + (f", persons {g['covered_persons']}/{g['landing_persons']}" if g['landing_persons'] else '')
                                          for g in sorted(flagged, key=lambda g: -(g['landing_rows'] - g['covered_rows']))],
            'flagged_count': len(flagged), 'by_family': by_family, 'unmodeled_kinds': unmodeled}


def summary(report, limit=40):
    """The compact form recorded in the workspace verify step."""
    return {k: report.get(k) for k in ('version', 'threshold', 'min_rows', 'lineage_source', 'flagged_count', 'by_family')} | \
        {'flagged': (report.get('flagged') or [])[:limit]}
