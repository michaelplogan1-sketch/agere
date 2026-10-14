"""Acute episodes (ED visits, inpatient admissions, observation stays) with 30-day readmission and planned-admission flags.

Episodes come from the semantic layer's utilization_event view, which merges EHR encounters/stays, ADT visits and payer
claims (POS 23, ED revenue codes, TOB 011x, observation revenue) into one row per real event (client_data.utilization).

Planned admissions follow the structure of the CMS Planned Readmission Algorithm v4.0 (Horwitz et al., Yale CORE;
https://qualitynet.cms.gov, Hospital-Wide Readmission measure methodology):
  always planned      principal diagnosis of maintenance chemotherapy or radiotherapy (Z51.0, Z51.1x), rehabilitation /
                      other specified aftercare (Z51.89), organ-donor admissions (Z52.x), transplant aftercare (Z48.2x);
                      MS-DRGs of transplants (001, 002, 005-008, 010, 014, 016, 017, 652) and chemotherapy (837-839, 846-849).
  potentially planned an elective-type surgical MS-DRG (joint replacement 461-462, 469-470, 483; spinal fusion 453-460;
                      hysterectomy 742-743; bariatric 619-621) whose principal diagnosis is not acute (ACUTE_PREFIXES).
The algorithm's ICD-10-PCS procedure lists are not used (no procedure codes on the received facility claims); the
DRG stands in for the procedure. The principal diagnosis / DRG comes from the paid TOB 011x payer claim admitted within
+-1 day of the episode, else the EHR visit's principal diagnosis.

An admission is an unplanned 30-day readmission when it starts 1-30 days after the discharge of an earlier inpatient
admission of the same patient and is not planned (a same-day admission is a transfer, part of the same episode) (orientation 'readmission'); orientation 'index' instead flags the
earlier admission that was followed by such a readmission. Episodes without a known discharge cannot anchor a window.
"""
from collections import defaultdict
from datetime import date

VERSION = 'acute-episodes/1'
KINDS = {'ed_visit': 'ed', 'inpatient': 'inpatient', 'observation': 'observation'}
ALWAYS_PLANNED_DX = ('Z510', 'Z511', 'Z5189', 'Z52', 'Z482')
ALWAYS_PLANNED_DRG = {1, 2, 5, 6, 7, 8, 10, 14, 16, 17, 652, 837, 838, 839, 846, 847, 848, 849}
ELECTIVE_DRG = {461, 462, 469, 470, 483, 453, 454, 455, 456, 457, 458, 459, 460, 742, 743, 619, 620, 621}
ACUTE_PREFIXES = ('S', 'T', 'A4', 'I21', 'I22', 'I46', 'I60', 'I61', 'I62', 'I63', 'J96', 'K35', 'K56', 'K57', 'K92', 'N17', 'R57', 'M8445')
READMIT_DAYS = 30


def _d(s): return date.fromisoformat(s[:10])


def _key(code): return str(code or '').replace('.', '').upper()


def planned(principal, drg):
    dx = _key(principal)
    try: g = int(str(drg).strip()) if drg not in (None, '') else None
    except ValueError: g = None
    if dx.startswith(ALWAYS_PLANNED_DX) or g in ALWAYS_PLANNED_DRG: return True
    return bool(g in ELECTIVE_DRG and dx and not dx.startswith(ACUTE_PREFIXES))


def episodes(db, start='0000-01-01', end='9999-12-31', orientation='readmission'):
    """[{'patient_id', 'kind', 'start', 'end', 'sources', 'planned', 'unplanned_readmission_30d'}] sorted."""
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    claims = defaultdict(list)
    if 'payer_claim' in tables:
        for pid, a, dx, drg in db.execute("SELECT patient_id, substr(coalesce(admission_date, service_from),1,10), principal_diagnosis, drg FROM payer_claim "
                                         "WHERE substr(ltrim(type_of_bill,'0'),1,2) = '11' AND final_status = 'paid' AND coalesce(counts_in_metrics,1) = 1"):
            if a: claims[pid].append((a, dx, drg))
    ehr = defaultdict(list)
    if 'visit' in tables:
        for pid, a, dx in db.execute("SELECT patient_id, substr(event_date,1,10), principal_icd10 FROM visit "
                                     "WHERE type_concept = 'inpatient_admission' AND principal_icd10 IS NOT NULL"):
            if a: ehr[pid].append((a, dx, None))
    out = []
    for pid, kind, s, e, src in db.execute("SELECT patient_id, kind, substr(event_date,1,10), substr(end_date,1,10), sources FROM utilization_event "
                                           "WHERE event_date IS NOT NULL ORDER BY patient_id, event_date"):
        if kind not in KINDS: continue
        row = {'patient_id': pid, 'kind': KINDS[kind], 'start': s, 'end': e, 'sources': src}
        if row['kind'] == 'inpatient':
            match = [c for c in list(claims.get(pid, [])) + list(ehr.get(pid, [])) if abs((_d(c[0]) - _d(s)).days) <= 1]
            dx, drg = (match[0][1], match[0][2]) if match else (None, None)
            row['principal_diagnosis'], row['drg'] = dx, drg
            row['planned'] = planned(dx, drg)
        out.append(row)
    by = defaultdict(list)
    for r in out:
        if r['kind'] == 'inpatient': by[r['patient_id']].append(r)
    for rows in by.values():
        rows.sort(key=lambda r: r['start'])
        for r in rows: r['unplanned_readmission_30d'] = False
        for i, r in enumerate(rows):
            if any(q['end'] and _d(q['end']) == _d(r['start']) for q in rows[:i]): continue  # transfer: same episode
            prior = [q for q in rows[:i] if q['end'] and 1 <= (_d(r['start']) - _d(q['end'])).days <= READMIT_DAYS]
            if prior and not r['planned']:
                (prior[-1] if orientation == 'index' else r)['unplanned_readmission_30d'] = True
    return [r for r in out if start <= r['start'] <= end]
