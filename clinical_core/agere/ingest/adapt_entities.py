"""Clinician and organization reference rows for native-feed staging.

    entities(db)  -> iterator of (kind, id, payload)   kind = 'clinicians' | 'organizations'
    last_report() -> counts by kind / organization_type / source, plus NPI checks

canonical.stage writes these into <workspace>/warehouse.sqlite3 source_entities(release, kind, id, payload), the table the
semantic layer reads into its clinician and organization tables (semantic_layer._entities). Payloads match v2.8:
    clinicians    {clinician_id, organization_id, display_name, specialty, npi, taxonomy_code}
    organizations {organization_id, organization_type, display_name, npi, taxonomy_code, tin, address_line, city, state,
                   postal_code}

Ids are the ones the adapters emit, so references resolve:
    * clinician_id = the 10-digit NPI when the source has one (adapt_clinical uses the NPI as attending / author /
      ordering clinician id), else the vendor-qualified id adapt_clinical falls back to: epic:<instance>:<prov_id>,
      athena:<contextid>:<providerid | clinicalproviderid>, ecw:<practice>:<doctorID>;
    * organization_id = WHN-PRACTICE-<ST>-<n> (roster practice_id, Epic department / identity names, athena DEPARTMENT,
      eCW folder / edi_facilities.code, FHIR folder, ORU authority PRAC..), WHN-HOSPITAL-<ST>-<n> (ADT HOSP.. codes),
      WH-PRACTICE-<n-1> / WH-HOSPITAL-<n-1> (EPIC-DEEPCHART departments, as in v2.8), WH-PAYER-* (payer folders and
      payer ids), WHN-SDOH-VENDOR, REFLAB (ORU performing lab). Organizations known only by an NPI (pharmacies, claim
      billing NPIs) get organization_id 'NPI-<npi>'.
Sources (most authoritative first, per field): ACO provider roster, EHR provider/department tables, HL7, claims
(x12 837, payer flat medical/pharmacy, CCLF, NCPDP). One row per id; NPIs dedupe (an NPI already carried by a named
organization or a clinician never becomes a second row); fields merge, the most authoritative non-empty value winning.
organization_type for NPI-only billing organizations comes from the most frequent bill type / place of service on
their claims (hospital, snf, home_health, hospice, asc, dialysis, urgent_care, lab, ambulance, dme, specialty_group,
independent_outpatient_practice); NPI-only pharmacies are 'pharmacy'. Nothing under private/ is read. stdlib only.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import json
import re

from .adapt_clinical import _c, _org_from_authority, _org_from_name

CLINICIAN_FIELDS = ('clinician_id', 'organization_id', 'display_name', 'specialty', 'npi', 'taxonomy_code')
ORG_FIELDS = ('organization_id', 'organization_type', 'display_name', 'npi', 'taxonomy_code', 'tin', 'address_line', 'city',
              'state', 'postal_code')
PRI = {'roster': 6, 'ehr': 5, 'hl7': 4, 'x12': 3, 'payer_flat': 2, 'cclf': 2, 'ncpdp': 2, 'derived': 1}

TAXONOMY_SPECIALTY = {'207Q00000X': 'Family Medicine', '363LF0000X': 'Family Nurse Practitioner', '363A00000X': 'Physician Assistant',
                      '363L00000X': 'Nurse Practitioner', '207R00000X': 'Internal Medicine', '208000000X': 'Pediatrics',
                      '207P00000X': 'Emergency Medicine', '207RC0000X': 'Cardiology', '207RE0101X': 'Endocrinology',
                      '163W00000X': 'Registered Nurse', '183500000X': 'Pharmacist'}
EPIC_SPECIALTY = {'265': 'Internal Medicine', '284': 'Physician Assistant', '349': 'Family Medicine', '433': 'Pediatrics',
                  '461': 'Emergency Medicine', '693': 'Endocrinology', '705': 'Registered Nursing', '736': 'Nurse Practitioner',
                  '941': 'Clinical Pharmacy', '947': 'Cardiology'}
CMS_SPECIALTY = {'01': 'General Practice', '06': 'Cardiology', '08': 'Family Medicine', '11': 'Internal Medicine', '13': 'Neurology',
                 '16': 'Obstetrics/Gynecology', '20': 'Orthopedic Surgery', '29': 'Pulmonary Disease', '30': 'Diagnostic Radiology',
                 '37': 'Pediatrics', '39': 'Nephrology', '46': 'Endocrinology', '50': 'Nurse Practitioner', '93': 'Emergency Medicine',
                 '97': 'Physician Assistant'}
ROSTER_SPECIALTY = {'family medicine': 'Family Medicine', 'family practice': 'Family Medicine', 'fm': 'Family Medicine',
                    'family med': 'Family Medicine', 'fnp': 'Family Nurse Practitioner', 'np-family': 'Family Nurse Practitioner',
                    'nurse practitioner': 'Nurse Practitioner', 'physician assistant': 'Physician Assistant', 'pa': 'Physician Assistant',
                    'pa-c': 'Physician Assistant'}
# place of service -> organization type (v2.8 vocabulary)
# Professional (837P / CMS-1500) claims: a group billing at a hospital, ASC or SNF place of service is still a physician
# group, so only POS that identify the billing entity itself type it; facility types come from institutional bill types.
POS_TYPE = {'81': 'lab', '41': 'ambulance', '42': 'ambulance', '20': 'urgent_care', '57': 'independent_outpatient_practice',
            '58': 'independent_outpatient_practice'}
PROFESSIONAL_DEFAULT = 'specialty_group'
INSTITUTIONAL_WEIGHT = 1000  # an institutional claim outweighs any number of professional ones
# institutional bill type (2 digits: facility + classification) -> organization type
BILL_TYPE = {'11': 'hospital', '12': 'hospital', '13': 'hospital', '14': 'hospital', '18': 'hospital', '85': 'hospital',
             '21': 'snf', '22': 'snf', '23': 'snf', '32': 'home_health', '33': 'home_health', '34': 'home_health',
             '81': 'hospice', '82': 'hospice', '83': 'asc', '72': 'dialysis', '71': 'independent_outpatient_practice',
             '77': 'independent_outpatient_practice', '74': 'independent_outpatient_rehabilitation', '75': 'independent_outpatient_rehabilitation'}
CCLF_FAC = {('1', None): 'hospital', ('2', None): 'snf', ('3', None): 'home_health', ('7', None): 'independent_outpatient_practice',
            ('8', '1'): 'hospice', ('8', '2'): 'hospice', ('8', '3'): 'asc', ('8', '4'): 'hospital', ('8', '9'): 'independent_outpatient_practice'}
TYPE_TAXONOMY = {'primary_care_practice': '261QP2300X', 'primary_care': '261QP2300X', 'hospital': '282N00000X', 'pharmacy': '3336C0003X',
                 'snf': '314000000X', 'home_health': '251E00000X', 'hospice': '251G00000X', 'asc': '261QA1903X', 'dialysis': '261QE0700X',
                 'urgent_care': '261QU0200X', 'lab': '291U00000X', 'ambulance': '341600000X'}

_REPORT: dict = {}


def last_report():
    return json.loads(json.dumps(_REPORT))


def _npi(v):
    v = _c(v)
    return v if v and re.fullmatch(r'\d{10}', v) else None


def npi_valid(npi):
    """Luhn check with the 80840 prefix (CMS NPI check digit)."""
    if not _npi(npi):
        return False
    digits = [int(d) for d in '80840' + npi]
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            d -= 9 if d > 9 else 0
        total += d
    return total % 10 == 0


def _tin(v):
    v = re.sub(r'\D', '', _c(v) or '')
    return f'{v[:2]}-{v[2:]}' if len(v) == 9 else None


def _state_of(org_id):
    m = re.search(r'-([A-Z]{2})-\d+$', org_id or '')
    return m[1] if m else None


def _practice_display(org_id):
    m = re.fullmatch(r'WHN-PRACTICE-([A-Z]{2})-(\d+)', org_id)
    if m:
        return f'Synthetic {m[1]} primary care practice {m[2]}'
    m = re.fullmatch(r'WHN-HOSPITAL-([A-Z]{2})-(\d+)', org_id)
    if m:
        return f'Synthetic {m[1]} hospital {m[2]}'
    m = re.fullmatch(r'WH-PRACTICE-(\d+)', org_id)
    if m:
        return f'Synthetic care practice {int(m[1]) + 1}'
    m = re.fullmatch(r'WH-HOSPITAL-(\d+)', org_id)
    if m:
        return f'Synthetic district {int(m[1]) + 1} hospital care'
    return None


def _org_type_of(org_id):
    if org_id.startswith('WHN-PRACTICE-'):
        return 'primary_care_practice'
    if org_id.startswith('WH-PRACTICE-'):
        return 'primary_care'
    if 'HOSPITAL' in org_id:
        return 'hospital'
    if org_id.startswith('WH-PAYER-'):
        return 'payer'
    return None


def _person_name(first, last, cred=None):
    name = ' '.join(x for x in (_c(first), _c(last)) if x)
    if not name:
        return None
    return f'{name}, {_c(cred)}' if _c(cred) else name


class _Build:
    def __init__(self, db):
        self.db = db
        self.tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.cols = {}
        self.orgs, self.clins = {}, {}
        self.opri, self.cpri = defaultdict(dict), defaultdict(dict)
        self.org_npi, self.clin_npi = {}, set()
        self.sources = defaultdict(Counter)
        self.missing = set()

    # ---- reading helpers
    def has(self, table, *cols):
        if table not in self.tables:
            self.missing.add(table)
            return False
        if table not in self.cols:
            self.cols[table] = {r[1] for r in self.db.execute(f'PRAGMA table_info("{table}")')}
        return all(c in self.cols[table] for c in cols)

    def rows(self, sql, table, *cols):
        if not self.has(table, *cols):
            return []
        cur = self.db.execute(sql)
        names = [d[0] for d in cur.description]
        return (dict(zip(names, r)) for r in cur)

    # ---- merging
    def org(self, oid, source, **f):
        oid = _c(oid)
        if not oid:
            return None
        p = PRI[source]
        cur = self.orgs.setdefault(oid, {k: None for k in ORG_FIELDS})
        cur['organization_id'] = oid
        for k, v in f.items():
            v = _c(v) if isinstance(v, str) or v is None else v
            if v is None:
                continue
            if cur.get(k) is None or p > self.opri[oid].get(k, 0):
                cur[k] = v
                self.opri[oid][k] = p
        self.sources['organizations'][source] += 1
        if cur.get('npi') and cur['npi'] not in self.org_npi:
            self.org_npi[cur['npi']] = oid
        return oid

    def clin(self, cid, source, **f):
        cid = _c(cid)
        if not cid:
            return None
        p = PRI[source]
        cur = self.clins.setdefault(cid, {k: None for k in CLINICIAN_FIELDS})
        cur['clinician_id'] = cid
        for k, v in f.items():
            v = _c(v) if isinstance(v, str) or v is None else v
            if v is None:
                continue
            if cur.get(k) is None or p > self.cpri[cid].get(k, 0):
                cur[k] = v
                self.cpri[cid][k] = p
        if cur.get('npi'):
            self.clin_npi.add(cur['npi'])
        self.sources['clinicians'][source] += 1
        return cid

    def named_org(self, oid, source, **f):
        """A WH.../WHN... organization id: type, display name and state follow from the id when the source has none."""
        if not oid:
            return None
        f.setdefault('organization_type', _org_type_of(oid))
        f.setdefault('display_name', _practice_display(oid))
        f.setdefault('state', _state_of(oid))
        return self.org(oid, source, **f)

    # ================================================================ sources
    def roster(self):
        latest = {}
        for r in self.rows('SELECT * FROM rosters_aco_provider_roster', 'rosters_aco_provider_roster', 'provider_npi', 'practice_id'):
            pid = _c(r.get('practice_id'))
            if not (pid and re.fullmatch(r'WH[N]?-[A-Z]+-[A-Z0-9-]+', pid)):
                continue  # column-shifted rows
            self.named_org(pid, 'roster', display_name=_c(r.get('practice_name')), npi=_npi(r.get('group_npi')), tin=_tin(r.get('tin')),
                           taxonomy_code=TYPE_TAXONOMY['primary_care_practice'])
            npi = _npi(r.get('provider_npi'))
            if not npi:
                continue
            o = (0 if _c(r.get('term_date')) else 1, r.get('_drop') or '', r.get('_row') or 0)
            if npi not in latest or o > latest[npi][0]:
                latest[npi] = (o, r)
        for npi, (_, r) in latest.items():
            tax = _c(r.get('taxonomy_code'))
            tax = tax if tax and re.fullmatch(r'[0-9A-Z]{9}X', tax) else None
            spec = TAXONOMY_SPECIALTY.get(tax or '') or ROSTER_SPECIALTY.get((_c(r.get('specialty')) or '').lower()) or _c(r.get('specialty'))
            self.clin(npi, 'roster', organization_id=_c(r.get('practice_id')), npi=npi, taxonomy_code=tax, specialty=spec,
                      display_name=_person_name(r.get('provider_first_name'), r.get('provider_last_name'), r.get('credential')))

    def epic(self):
        # departments / locations -> organizations
        for r in self.rows('SELECT * FROM ehr_epic_clarity_dep', 'ehr_epic_clarity_dep', 'department_name'):
            self.named_org(_org_from_name(r['department_name']), 'ehr')
        for r in self.rows('SELECT loc_name FROM ehr_epic_clarity_loc', 'ehr_epic_clarity_loc', 'loc_name'):
            self.named_org(_org_from_name(r['loc_name']), 'ehr')
        for r in self.rows('SELECT id_type_name FROM ehr_epic_identity_id_type', 'ehr_epic_identity_id_type', 'id_type_name'):
            self.named_org(_org_from_name(r['id_type_name']), 'ehr')
        dep = {}
        for r in self.rows('SELECT * FROM ehr_epic_clarity_dep', 'ehr_epic_clarity_dep', 'department_id', 'department_name'):
            org = _org_from_name(r['department_name'])
            if org:
                dep[((r['_instance'] or '').split('/')[-1], _c(r['department_id']))] = org
        # provider -> organization: the department of most of the provider's encounters
        prov_org = defaultdict(Counter)
        if self.has('ehr_epic_pat_enc', 'visit_prov_id') and self.has('ehr_epic_pat_enc_2', 'department_id'):
            for inst, prov, d, n in self.db.execute(
                    """SELECT e._instance, e.visit_prov_id, p.department_id, count(*) FROM ehr_epic_pat_enc e
                       JOIN ehr_epic_pat_enc_2 p ON p._instance = e._instance AND p.pat_enc_csn_id = e.pat_enc_csn_id
                       GROUP BY 1, 2, 3"""):
                inst = (inst or '').split('/')[-1]
                org = dep.get((inst, _c(d)))
                if org and _c(prov):
                    prov_org[(inst, _c(prov))][org] += n
        inst_orgs = defaultdict(set)
        for (inst, _), org in dep.items():
            inst_orgs[inst].add(org)
        provs = defaultdict(dict)
        for table, cols in (('ehr_epic_clarity_ser', ('prov_name',)), ('ehr_epic_clarity_ser_2', ('npi', 'clinician_title'))):
            for r in self.rows(f'SELECT * FROM {table} ORDER BY _drop, _row', table, 'prov_id', *cols):
                k = ((r['_instance'] or '').split('/')[-1], _c(r['prov_id']))
                if not (k[1] and k[1].isdigit()) or r.get('flat_row_issue'):
                    continue
                for c in cols:
                    if _c(r.get(c)):
                        provs[k][c] = _c(r.get(c))
        for r in self.rows('SELECT * FROM ehr_epic_clarity_ser_spec ORDER BY _drop, _row', 'ehr_epic_clarity_ser_spec', 'prov_id', 'specialty_c'):
            k = ((r['_instance'] or '').split('/')[-1], _c(r['prov_id']))
            if k in provs and EPIC_SPECIALTY.get(_c(r['specialty_c']) or '') and _c(r.get('line')) in (None, '1'):
                provs[k]['specialty'] = EPIC_SPECIALTY[_c(r['specialty_c'])]
        for (inst, prov), p in provs.items():
            npi = _npi(p.get('npi'))
            orgs = prov_org.get((inst, prov))
            org = orgs.most_common(1)[0][0] if orgs else (next(iter(inst_orgs[inst])) if len(inst_orgs[inst]) == 1 else None)
            self.clin(npi or f'epic:{inst}:{prov}', 'ehr', npi=npi, organization_id=org, specialty=p.get('specialty'),
                      display_name=_person_name(*(p.get('prov_name', '').split(' ', 1) + [''])[:2], p.get('clinician_title')) if p.get('prov_name') else None)

    def vendor_ids_on_rows(self):
        """Providers referenced on encounters / orders / notes but missing from the provider tables keep the vendor id
        adapt_clinical gives them (no NPI known), with the practice they were seen at."""
        known_epic = {(k.split(':')[1], k.split(':')[2]) for k in self.clins if k.startswith('epic:')}
        npi_prov = set()
        if self.has('ehr_epic_clarity_ser_2', 'npi'):
            npi_prov = {((i or '').split('/')[-1], _c(p)) for i, p, n in self.db.execute('SELECT _instance, prov_id, npi FROM ehr_epic_clarity_ser_2') if _npi(n)}
        for t, c in (('ehr_epic_pat_enc', 'visit_prov_id'), ('ehr_epic_order_med', 'authrzing_prov_id'),
                     ('ehr_epic_hno_info', 'current_author_id'), ('ehr_epic_f_sched_appt', 'prov_id'), ('ehr_epic_order_proc', 'authrzing_prov_id')):
            if not self.has(t, c):
                continue
            for inst, prov in self.db.execute(f'SELECT DISTINCT _instance, "{c}" FROM "{t}"'):
                inst, prov = (inst or '').split('/')[-1], _c(prov)
                if prov and prov.isdigit() and (inst, prov) not in known_epic and (inst, prov) not in npi_prov:
                    self.clin(f'epic:{inst}:{prov}', 'derived')
                    known_epic.add((inst, prov))
        for t, c in (('ehr_ecw_enc', 'doctorid'), ('ehr_ecw_rxhub_scriptlog', 'doctorid'), ('ehr_ecw_labdata', 'ordphyid')):
            if not self.has(t, c):
                continue
            for inst, doc in self.db.execute(f'SELECT DISTINCT _instance, "{c}" FROM "{t}"'):
                practice, doc = (inst or '').split('/')[-1], _c(doc)
                cid = f'ecw:{practice}:{doc}'
                if doc and doc.isdigit() and doc != '0' and cid not in self.clins and not self._ecw_has_npi(practice, doc):
                    self.clin(cid, 'derived', organization_id=_org_from_name(practice))

    def _ecw_has_npi(self, practice, doc):
        if not hasattr(self, '_ecw_npi'):
            self._ecw_npi = set()
            if self.has('ehr_ecw_doctors', 'npi'):
                self._ecw_npi = {((i or '').split('/')[-1], _c(d)) for i, d, n in self.db.execute('SELECT _instance, doctorid, npi FROM ehr_ecw_doctors') if _npi(n)}
        return (practice, doc) in self._ecw_npi

    def athena(self):
        ctx_org = {}
        for r in self.rows('SELECT * FROM ehr_athena_department ORDER BY _drop, _row', 'ehr_athena_department', 'departmentname'):
            org = _org_from_name(r['departmentname'])
            if not org:
                continue
            ctx_org.setdefault(_c(r.get('contextid')) or (r['_instance'] or '').split('/')[-1], org)
            self.named_org(org, 'ehr', address_line=r.get('departmentaddress'), city=r.get('departmentcity'),
                           state=r.get('departmentstate'), postal_code=r.get('departmentzip'))
        for table, idc, npic, first, last, cred in (('ehr_athena_provider', 'providerid', 'providernpinumber', 'providerfirstname',
                                                     'providerlastname', 'providertype'),
                                                    ('ehr_athena_clinicalprovider', 'clinicalproviderid', 'npi', 'firstname', 'lastname', 'title')):
            for r in self.rows(f'SELECT * FROM {table} ORDER BY _drop, _row', table, idc):
                ctx, pid = _c(r.get('contextid')) or (r['_instance'] or '').split('/')[-1], _c(r.get(idc))
                if not (pid and pid.isdigit()) or r.get('flat_row_issue'):
                    continue
                npi = _npi(r.get(npic))
                cid = npi or f'athena:{ctx}:{pid}'
                self.clin(cid, 'ehr', npi=npi, organization_id=ctx_org.get(ctx),
                          display_name=_person_name(r.get(first), r.get(last), r.get(cred)),
                          specialty={'MD': None, 'DO': None, 'NP': 'Nurse Practitioner', 'PA': 'Physician Assistant',
                                     'PA-C': 'Physician Assistant'}.get(_c(r.get(cred)) or ''))

    def ecw(self):
        fac = {}
        for r in self.rows('SELECT * FROM ehr_ecw_edi_facilities ORDER BY _drop, _row', 'ehr_ecw_edi_facilities', 'code'):
            practice = (r['_instance'] or '').split('/')[-1]
            org = _org_from_name(r.get('code')) or _org_from_name(practice)
            fac[(practice, _c(r.get('id')))] = org
            self.named_org(org, 'ehr', npi=_npi(r.get('npi')), address_line=r.get('addressline1'), city=r.get('city'),
                           state=r.get('state'), postal_code=r.get('zip'))
        for (inst,) in self.db.execute('SELECT DISTINCT _instance FROM ehr_ecw_patients') if self.has('ehr_ecw_patients') else []:
            self.named_org(_org_from_name((inst or '').split('/')[-1]), 'ehr')
        for r in self.rows('SELECT * FROM ehr_ecw_doctors ORDER BY _drop, _row', 'ehr_ecw_doctors', 'doctorid'):
            practice, did = (r['_instance'] or '').split('/')[-1], _c(r.get('doctorid'))
            if not (did and did.isdigit()) or r.get('flat_row_issue'):
                continue
            npi = _npi(r.get('npi'))
            tax = _c(r.get('specialitycode'))
            tax = tax if tax and re.fullmatch(r'[0-9A-Z]{9}X', tax) else None
            self.clin(npi or f'ecw:{practice}:{did}', 'ehr', npi=npi, taxonomy_code=tax, display_name=_c(r.get('printname')),
                      specialty=TAXONOMY_SPECIALTY.get(tax or '') or _c(r.get('speciality')),
                      organization_id=fac.get((practice, _c(r.get('facilityid')))) or _org_from_name(practice))

    def fhir_hl7(self):
        for t in ('fhir_patient', 'fhir_encounter'):
            if self.has(t):
                for (inst,) in self.db.execute(f'SELECT DISTINCT _instance FROM "{t}"'):
                    self.named_org(_org_from_name((inst or '').split('/')[-1]), 'ehr')
        if self.has('hl7_message', 'sending_facility'):
            for code, auth, recv, mcode in self.db.execute(
                    'SELECT DISTINCT sending_facility, patient_id_assigning_authority, receiving_facility, message_code FROM hl7_message'):
                for c in (code, auth, recv):
                    self.named_org(_org_from_authority(c), 'hl7')
                if mcode == 'ORU' and _c(code):
                    self.org(_c(code), 'hl7', organization_type='lab', display_name='Synthetic reference laboratory',
                             taxonomy_code=TYPE_TAXONOMY['lab'])

    def payers(self):
        ids = set()
        for t in ('payers_eligibility', 'payers_medical', 'payers_pharmacy', 'x12_834_member', 'ncpdp_claim', 'programs_care_gaps',
                  'programs_risk_scores'):
            if self.has(t):
                ids |= {(i or '').split('/')[-1] for (i,) in self.db.execute(f'SELECT DISTINCT _instance FROM "{t}"')}
        for t, c in (('x12_837_claim', 'payer_id'), ('x12_835_claim', 'payer_name'), ('x12_835_payment', 'payer_name'),
                     ('x12_834_member', 'sponsor_name'), ('ncpdp_claim', 'file_sender')):
            if self.has(t, c):
                ids |= {_c(v) for (v,) in self.db.execute(f'SELECT DISTINCT "{c}" FROM "{t}"')}
        if any(t.startswith('cclf_') or t.startswith('alr_') for t in self.tables):
            ids.add('WH-PAYER-MSSP')
        for pid in sorted(i for i in ids if i and re.fullmatch(r'WH-PAYER-[A-Z0-9]+', i)):
            self.org(pid, 'x12', organization_type='payer', display_name=f'Synthetic health plan {pid[len("WH-PAYER-"):]}')
        if self.has('sdoh_vendor'):
            self.org('WHN-SDOH-VENDOR', 'derived', organization_type='data_vendor', display_name='Synthetic SDOH data vendor')

    def named_billing(self):
        """837 billing / pay-to / 835 payee loops that carry an organization id as the name give that org's NPI and TIN."""
        for t, name, npi, tin in (('x12_837_claim', 'billing_provider_name', 'billing_provider_npi', 'billing_provider_tin'),
                                  ('x12_837_claim', 'pay_to_name', 'pay_to_npi', None),
                                  ('x12_837_claim', 'service_facility_name', 'service_facility_npi', None),
                                  ('x12_835_claim', 'payee_name', 'payee_npi', None), ('x12_835_payment', 'payee_name', 'payee_npi', None)):
            cols = [name, npi] + ([tin] if tin else [])
            if not self.has(t, *cols):
                continue
            for row in self.db.execute(f'SELECT DISTINCT {", ".join(cols)} FROM "{t}"'):
                org = _org_from_name(row[0]) if row[0] and re.match(r'WHN?-', row[0]) else None
                if org:
                    self.named_org(org, 'x12', npi=_npi(row[1]), tin=_tin(row[2]) if tin else None)

    def claims(self):
        """NPIs on claims not otherwise known: billing NPIs -> organizations (typed by bill type / POS), rendering /
        attending / prescriber NPIs -> clinicians (organization = the billing organization they bill under most)."""
        btype = defaultdict(Counter)    # billing npi -> org type votes
        btin = {}
        render_org = defaultdict(Counter)
        prescribers, pharmacies, renderers = set(), {}, {}
        if self.has('x12_837_claim', 'billing_provider_npi', 'transaction_type', 'facility_code'):
            for npi, tt, fc, tin, n in self.db.execute(
                    """SELECT billing_provider_npi, transaction_type, facility_code, billing_provider_tin, count(*) FROM x12_837_claim
                       GROUP BY 1, 2, 3, 4"""):
                npi = _npi(npi)
                if not npi:
                    continue
                inst = tt == '837I'
                t = BILL_TYPE.get(_c(fc) or '') if inst else POS_TYPE.get(_c(fc) or '', PROFESSIONAL_DEFAULT)
                if t:
                    btype[npi][t] += n * (INSTITUTIONAL_WEIGHT if inst else 1)
                if _tin(tin):
                    btin[npi] = _tin(tin)
            for c in ('rendering_npi', 'attending_npi', 'operating_npi', 'referring_npi'):
                if self.has('x12_837_claim', c):
                    for rn, bn, n in self.db.execute(f'SELECT {c}, billing_provider_npi, count(*) FROM x12_837_claim WHERE {c} IS NOT NULL GROUP BY 1, 2'):
                        if _npi(rn):
                            renderers.setdefault(_npi(rn), None)
                            if _npi(bn) and c != 'referring_npi':
                                render_org[_npi(rn)][_npi(bn)] += n
        if self.has('payers_medical', 'billing_npi', 'claim_type', 'bill_type', 'place_of_service'):
            for npi, ct, bt, pos, n in self.db.execute(
                    'SELECT billing_npi, claim_type, bill_type, place_of_service, count(*) FROM payers_medical GROUP BY 1, 2, 3, 4'):
                npi = _npi(npi)
                if not npi:
                    continue
                inst = ct == 'I'
                t = BILL_TYPE.get((_c(bt) or '')[1:3]) if inst else POS_TYPE.get(_c(pos) or '', PROFESSIONAL_DEFAULT)
                if t:
                    btype[npi][t] += n * (INSTITUTIONAL_WEIGHT if inst else 1)
            for c in ('rendering_npi', 'rendering_provider_npi'):
                if self.has('payers_medical', c):
                    for rn, bn, n in self.db.execute(f'SELECT {c}, billing_npi, count(*) FROM payers_medical WHERE {c} IS NOT NULL GROUP BY 1, 2'):
                        if _npi(rn):
                            renderers.setdefault(_npi(rn), None)
                            if _npi(bn):
                                render_org[_npi(rn)][_npi(bn)] += n
        if self.has('cclf_parta_claims_header', 'fac_prvdr_npi_num', 'clm_bill_fac_type_cd', 'clm_bill_clsfctn_cd'):
            for npi, ft, cl, n in self.db.execute(
                    'SELECT fac_prvdr_npi_num, clm_bill_fac_type_cd, clm_bill_clsfctn_cd, count(*) FROM cclf_parta_claims_header GROUP BY 1, 2, 3'):
                npi = _npi(npi)
                t = CCLF_FAC.get((_c(ft), _c(cl))) or CCLF_FAC.get((_c(ft), None))
                if npi and t:
                    btype[npi][t] += n * INSTITUTIONAL_WEIGHT
            for c in ('atndg_prvdr_npi_num', 'oprtg_prvdr_npi_num', 'othr_prvdr_npi_num'):
                if self.has('cclf_parta_claims_header', c):
                    for rn, bn, n in self.db.execute(f'SELECT {c}, fac_prvdr_npi_num, count(*) FROM cclf_parta_claims_header GROUP BY 1, 2'):
                        if _npi(rn):
                            renderers.setdefault(_npi(rn), None)
                            if _npi(bn):
                                render_org[_npi(rn)][_npi(bn)] += n
        if self.has('cclf_partb_physicians', 'rndrg_prvdr_npi_num', 'clm_prvdr_spclty_cd'):
            for rn, sp, tin, n in self.db.execute(
                    'SELECT rndrg_prvdr_npi_num, clm_prvdr_spclty_cd, clm_rndrg_prvdr_tax_num, count(*) FROM cclf_partb_physicians GROUP BY 1, 2, 3'):
                if _npi(rn):
                    renderers[_npi(rn)] = renderers.get(_npi(rn)) or CMS_SPECIALTY.get(_c(sp) or '')
        if self.has('cclf_partb_dme', 'payto_prvdr_npi_num'):
            for npi, n in self.db.execute('SELECT payto_prvdr_npi_num, count(*) FROM cclf_partb_dme GROUP BY 1'):
                if _npi(npi):
                    btype[_npi(npi)]['dme'] += n
            if self.has('cclf_partb_dme', 'ordrg_prvdr_npi_num'):
                for (rn,) in self.db.execute('SELECT DISTINCT ordrg_prvdr_npi_num FROM cclf_partb_dme'):
                    if _npi(rn):
                        renderers.setdefault(_npi(rn), None)
        # pharmacies and prescribers
        if self.has('ncpdp_claim', 'service_provider_id', 'service_provider_id_qualifier'):
            for npi, q, n in self.db.execute('SELECT service_provider_id, service_provider_id_qualifier, count(*) FROM ncpdp_claim GROUP BY 1, 2'):
                if _npi(npi) and (_c(q) or '01') == '01':
                    pharmacies[_npi(npi)] = 'ncpdp'
            if self.has('ncpdp_claim', 'prescriber_id', 'prescriber_id_qualifier'):
                for npi, q in self.db.execute('SELECT DISTINCT prescriber_id, prescriber_id_qualifier FROM ncpdp_claim'):
                    if _npi(npi) and (_c(q) or '01') == '01':
                        prescribers.add(_npi(npi))
        if self.has('payers_pharmacy', 'pharmacy_npi'):
            for (npi,) in self.db.execute('SELECT DISTINCT pharmacy_npi FROM payers_pharmacy'):
                if _npi(npi):
                    pharmacies.setdefault(_npi(npi), 'payer_flat')
            if self.has('payers_pharmacy', 'prescriber_npi'):
                prescribers |= {_npi(v) for (v,) in self.db.execute('SELECT DISTINCT prescriber_npi FROM payers_pharmacy') if _npi(v)}
        if self.has('cclf_partd_claims', 'clm_srvc_prvdr_gnrc_id_num', 'prvdr_srvc_id_qlfyr_cd'):
            for npi, q in self.db.execute('SELECT DISTINCT clm_srvc_prvdr_gnrc_id_num, prvdr_srvc_id_qlfyr_cd FROM cclf_partd_claims'):
                if _npi(npi) and _c(q) == '01':
                    pharmacies.setdefault(_npi(npi), 'cclf')
            if self.has('cclf_partd_claims', 'clm_prsbng_prvdr_gnrc_id_num'):
                prescribers |= {_npi(v) for (v,) in self.db.execute('SELECT DISTINCT clm_prsbng_prvdr_gnrc_id_num FROM cclf_partd_claims') if _npi(v)}

        def org_for_npi(npi):
            return self.org_npi.get(npi) or (f'NPI-{npi}' if f'NPI-{npi}' in self.orgs else None)

        for npi, src in sorted(pharmacies.items()):
            if npi in self.org_npi or npi in self.clin_npi:
                continue
            self.org(f'NPI-{npi}', src, organization_type='pharmacy', npi=npi, taxonomy_code=TYPE_TAXONOMY['pharmacy'],
                     display_name=f'Pharmacy NPI {npi}')
        for npi, votes in sorted(btype.items()):
            if npi in self.org_npi or npi in self.clin_npi or npi in renderers or npi in prescribers:
                continue  # a named org already, or an individual's NPI
            t = votes.most_common(1)[0][0] if votes and votes.most_common(1)[0][1] else 'specialty_group'
            self.org(f'NPI-{npi}', 'x12', organization_type=t, npi=npi, tin=btin.get(npi), taxonomy_code=TYPE_TAXONOMY.get(t),
                     display_name=f'{t.replace("_", " ").capitalize()} NPI {npi}')
        for npi in sorted(set(renderers) | prescribers):
            orgs = render_org.get(npi)
            org = next((org_for_npi(b) for b, _ in orgs.most_common() if org_for_npi(b)), None) if orgs else None
            if npi in self.clin_npi:
                if org and not self.clins[npi].get('organization_id'):
                    self.clin(npi, 'derived', organization_id=org)
                continue
            self.clin(npi, 'x12' if npi in renderers else 'ncpdp', npi=npi, organization_id=org, specialty=renderers.get(npi))

    # ---- driver
    def run(self):
        for step in (self.roster, self.epic, self.athena, self.ecw, self.vendor_ids_on_rows, self.fhir_hl7, self.payers, self.named_billing, self.claims):
            step()
        for o in self.orgs.values():  # a type's standard taxonomy when the source gave none
            if not o.get('taxonomy_code') and o.get('organization_type') in TYPE_TAXONOMY:
                o['taxonomy_code'] = TYPE_TAXONOMY[o['organization_type']]
        return self

    def report(self):
        npis = [c['npi'] for c in self.clins.values() if c.get('npi')] + [o['npi'] for o in self.orgs.values() if o.get('npi')]
        return {'clinicians': len(self.clins), 'organizations': len(self.orgs),
                'organization_types': dict(Counter(o.get('organization_type') or 'unknown' for o in self.orgs.values()).most_common()),
                'clinicians_with_npi': sum(1 for c in self.clins.values() if c.get('npi')),
                'clinicians_with_organization': sum(1 for c in self.clins.values() if c.get('organization_id')),
                'clinician_id_kind': dict(Counter('npi' if re.fullmatch(r'\d{10}', k) else k.split(':')[0] for k in self.clins)),
                'npis_failing_check_digit': sum(1 for n in npis if not npi_valid(n)),
                'source_rows': {k: dict(v) for k, v in self.sources.items()}, 'missing_tables': sorted(self.missing)}


def entities(db):
    """(kind, id, payload) for every clinician and organization found in the landing database (read-only use)."""
    global _REPORT
    b = _Build(db).run()
    _REPORT = b.report()
    for cid in sorted(b.clins):
        yield 'clinicians', cid, {k: b.clins[cid].get(k) for k in CLINICIAN_FIELDS}
    for oid in sorted(b.orgs):
        yield 'organizations', oid, {k: b.orgs[oid].get(k) for k in ORG_FIELDS}
