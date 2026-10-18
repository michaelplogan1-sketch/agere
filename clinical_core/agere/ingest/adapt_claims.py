"""Payer-side adapter: landing tables -> Agere canonical enrollment, medical claim, pharmacy claim and SDOH records.

    records(db, resolve) -> iterator of canonical record dicts       (contract: ingest/canonical.py)
    last_report()        -> duplicates dropped by reason, records per stream, cross-checks of the last records() run

Streams and their source of truth (docs/agere/NATIVE_FEED_INVENTORY.md sections 2, 3 and 5):

enrollment_spans
    x12_834 (FULL quarterly + CHG monthly) for every payer that sends it. One span per (payer, member id, coverage start);
    every change of a span (term date set or moved, retro-term, reinstatement, product change) is a new version whose
    supersedes_record_id names the previous version. INS03 021 add / 024 term / 030 audit; DTP 356/348 begin, 357/349 end.
    A term whose end date is earlier than a file that still reported the member active is a retroactive_termination.
    CHG loops apply before a FULL of the same date (FULL is authoritative on its date). Flat ELIGIBILITY fills plan_id,
    PCP (attribution_npi / attribution_practice_id = PCP_NAME, which holds the practice id), product and a missing term
    date; flat-only members get spans from the flat file. Medicare FFS (MSSP): CCLF enrollment spans (+ CCLF8 ranges for
    beneficiaries without one), dual status / entitlement reason from CCLF8, attribution NPI from the latest MSSP QALR,
    MBI re-issues (CCLF9 obsolete date + same name/DOB in CCLF8) as member_id_reissued with previous_member_id.
payer_medical_claims / payer_medical_claim_lines (diagnoses inside the claim record)
    payer-side 837P/837I (x12_payer) for the claim header, lines and diagnoses; payer-side 835 CLPs for adjudication
    events, one record per event (adjudication_sequence 1..n in paid-date order; supersedes_record_id = the previous event;
    CLP02 22 reversal / void as its own event). Ids: WHN-CLM-<hex>[-R|-D|-X|-R-X] / WHN-PCLM-<hex>. -R replacements carry
    original_payer_claim_id (REF*F8, else the base id) and their first event supersedes the original's last non-void event;
    -D duplicate submissions are dropped (837 loop and 835 events); -X crossover/COB copies are kept as the secondary
    payer's claim (product medigap under SUP1/SUP2, cob_secondary elsewhere). Flat MEDICAL (10 payers) fills network,
    allowed, statuses (denied_upheld, recouped, voided) and header-only lines, and is cross-checked; its C1 quarterly
    FULL_RESTATEMENT rows only fill events the incrementals lack. Provider-side 837/835 (x12/) are kept out of these payer streams as copies of the
    payer claim (their lines fill header-only loops; their bare CLM01 becomes source_billing_claim_id). CCLF Part A/B/DME
    are the Medicare FFS (MSSP) claims: an MSSP X12 claim that matches a CCLF claim (837P: MBI + HCPCS + service date;
    837I: MBI + statement from/thru) is kept once, with CCLF allowed/paid amounts; unmatched CCLF chains (clm_cntl_num tail,
    original / cancel / re-issue) become their own claims.
payer_pharmacy_claims
    NCPDP D.0 transactions: B1 P paid, B1 R rejected (reject_code = first 511-FB), B2 reversal (transaction_code B2,
    supersedes the paid B1), -R resubmissions are their own paid claims (original_rx_claim_id = base id). Joined to flat
    PHARMACY on WHN-RXC claim id (drug_name, plan/member paid, recoupments, flat-only claims). MSSP: CCLF7 (Part D) matched
    to MSSP NCPDP on MBI + Rx number + fill number is kept once; unmatched PDE chains become their own claims.
    drug_name falls back to the NDC's name in any flat pharmacy file; pharmacy_type comes from CCLF7 by pharmacy NPI.
medical_claim_submissions / medical_claim_lines / medical_claims / provider_remittance_advice (practice revenue cycle)
    Provider-side x12/837p and x12/835 under each WHN-PRACTICE-* folder: the same claims as the payer streams, but the
    practice's own billing and remittance feed (the payer streams above are unchanged). One submission per CLM
    (submission_kind original | corrected (CLM05-3 7, original_submission_record_id = the earlier submission of the
    claim) | void (8)); lines for the current submission of each claim (SV1/SV2); one medical_claims adjudication and
    one provider_remittance_advice per provider 835 CLP (CLP02 1 paid, 4 denied, 22 reversed as its own event with
    original_adjudication_record_id = the payment it takes back), with CAS adjustments and financial_deltas_cents in
    the v2.8 shape. claim_id = the practice CLM01 (bare hex; the payer claim id is WHN-CLM-<claim_id>). These streams
    are versioned by claim_id in the semantic layer, so they carry no supersedes_record_id.
sdoh_vendor_attributes
    sdoh_vendor rows (one per member per delivery). Care gaps, risk scores and MSSP assignment have no canonical stream
    (docs/agere/canonical_streams.json); QALR only feeds MSSP enrollment attribution.

Every record: envelope fields, a stable record_id ('AGR-' + sha256(stream | business key | version)), and _lineage =
[{table, _file, _row}] for the landing rows that fed it. People resolve through resolve(source_key) with
identity.py's helpers: member_key(payer folder id, member id), then mbi_key(id); SDOH rows use sdoh_key(id)
(member:sdoh_vendor:<id>);
unresolved people are yielded with patient_id None. Delivery duplicates (resent interchanges = same ISA13 in the same
payer/practice folder, _RESEND files, originals of _v2 files, repeated monthly rows) are dropped and counted.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import copy
from datetime import date, timedelta
import hashlib
import json
import re

from . import identity

STREAMS = ('enrollment_spans', 'payer_medical_claims', 'payer_medical_claim_lines', 'payer_pharmacy_claims',
           'sdoh_vendor_attributes', 'medical_claim_submissions', 'medical_claim_lines', 'medical_claims',
           'provider_remittance_advice', 'death_records')

MBI = re.compile(r'^[1-9][AC-HJKMNP-RT-Y][AC-HJKMNP-RT-Y0-9][0-9][AC-HJKMNP-RT-Y][AC-HJKMNP-RT-Y0-9][0-9]'
                 r'[AC-HJKMNP-RT-Y]{2}[0-9]{2}$')
CLAIM_ID = re.compile(r'^(?P<base>WHN-P?CLM-[0-9a-fA-F]+)(?P<suffix>(?:-R)?(?:-D)?(?:-X)?)$')
RXC_ID = re.compile(r'^(?P<base>WHN-RXC-[0-9a-fA-F]+)(?P<suffix>-R)?$')
VARIANT = re.compile(r'_(resend|v\d+)(?=(\.[^./]*)?$)', re.I)

PRODUCT_834 = {'COMMERCIAL': 'commercial', 'MEDICAID': 'medicaid', 'MEDICARE_FFS': 'medicare_ffs',
               'MEDICARE_ADVANTAGE': 'medicare_advantage', 'DUAL_MA': 'dual_ma', 'MARKETPLACE': 'marketplace',
               'MEDIGAP': 'medigap', 'COB_SECONDARY': 'cob_secondary', 'DUAL_FFS': 'dual_ffs'}
PRODUCTS = set(PRODUCT_834.values())
PAYER_PRODUCT = {'MSSP': 'medicare_ffs', 'SUP1': 'medigap', 'SUP2': 'medigap', 'DSNP1': 'dual_ma',
                 'MA1': 'medicare_advantage', 'MA2': 'medicare_advantage', 'MA3': 'medicare_advantage',
                 'MCD1': 'medicaid', 'MCD2': 'medicaid', 'X1': 'marketplace', 'C1': 'commercial', 'C2': 'commercial',
                 'C3': 'commercial'}
FLAT_MED_STATUS = {'paid', 'denied', 'denied_upheld', 'voided', 'reversed', 'recouped'}
FLAT_RX_STATUS = {'paid', 'rejected', 'reversed', 'recouped'}
# CMS / UB-04 patient discharge status -> canonical discharge_status vocabulary
DISCHARGE = {'01': 'home', '06': 'home_health', '03': 'snf', '62': 'irf', '02': 'acute_hospital', '05': 'other_facility',
             '07': 'left_ama', '20': 'expired', '30': 'still_patient', '50': 'hospice_home', '51': 'hospice_facility',
             '63': 'ltch', '64': 'nursing_facility', '65': 'psychiatric_facility'}
DUAL = {'01': 'partial_benefit', '02': 'full_benefit', '03': 'partial_benefit', '04': 'full_benefit',
        '05': 'partial_benefit', '06': 'partial_benefit', '08': 'full_benefit'}
ENTITLEMENT = {'0': 'age', '1': 'disability', '2': 'esrd', '3': 'disability_and_esrd'}
PHARMACY_TYPE = {'01': 'retail_pharmacy', '06': 'mail_order_pharmacy', '08': 'specialty_pharmacy',
                 '05': 'long_term_care_pharmacy', '03': 'home_infusion_pharmacy', '04': 'institutional_pharmacy'}
INPATIENT_TOB = {'011', '018', '021', '028', '041', '065', '066', '086'}  # 0 + facility + classification (inpatient)
CLP_PAID = {'1', '2', '3', '19', '20', '21'}
ROUTINE_CARC = {'1', '2', '3', '45'}  # deductible, coinsurance, copay, contractual: not a denial/adjustment reason

_REPORT: dict = {}


def last_report():
    """Counters of the last records() run: dropped[reason], emitted[stream], cross_check[...], unmapped[field]."""
    return copy.deepcopy(_REPORT)


# ---------------------------------------------------------------------------------------------------------------- utils
def _s(v):
    if v is None:
        return None
    v = str(v).strip()
    return v or None


def _num(v):
    v = _s(v)
    if v is None:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _int(v):
    n = _num(v)
    return None if n is None else int(n)


def _money(v):
    n = _num(v)
    return None if n is None else round(n, 2)


def _d(v):
    """Any delivered date form -> ISO YYYY-MM-DD (YYYYMMDD, MMDDYYYY, MM/DD/YYYY, YYYY-MM-DD[...]); else None."""
    v = _s(v)
    if v is None:
        return None
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})', v)
    if m:
        y, mo, d = m.groups()
    elif re.fullmatch(r'\d{1,2}/\d{1,2}/\d{4}', v):
        mo, d, y = v.split('/')
    elif re.fullmatch(r'\d{8}', v):
        if 1800 <= int(v[:4]) <= 9999 and 1 <= int(v[4:6]) <= 12:
            y, mo, d = v[:4], v[4:6], v[6:]
        elif 1 <= int(v[:2]) <= 12 and 1800 <= int(v[4:]) <= 9999:
            mo, d, y = v[:2], v[2:4], v[4:]
        else:
            return None
    else:
        return None
    try:
        if y.startswith('9999'):
            return '9999-12-31'
        return date(int(y), int(mo), int(d)).isoformat()
    except ValueError:
        return None


def _file_date(path):
    """Delivery date written in a file name: ..._YYYYMMDD[...] or CMS .DYYMMDD."""
    name = (path or '').rsplit('/', 1)[-1]
    m = re.findall(r'(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)', name)
    for y, mo, d in reversed(m):
        try:
            return date(int(y), int(mo), int(d)).isoformat()
        except ValueError:
            continue
    m = re.search(r'\.D(\d{2})(\d{2})(\d{2})', name)
    if m:
        try:
            return date(2000 + int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return None
    return None


def _rid(stream, *key):
    basis = '|'.join([stream] + ['' if k is None else str(k) for k in key])
    return 'AGR-' + hashlib.sha256(basis.encode()).hexdigest()[:24]


def _lin(table, row):
    return (table, row.get('_file'), row.get('_row'))


def _lineage(items):
    out, seen = [], set()
    for t in items:
        if t in seen:
            continue
        seen.add(t)
        out.append({'table': t[0], '_file': t[1], '_row': t[2]})
    return out


def _payer_id(v):
    return identity.payer_id(v)


def _short(payer_id):
    return (payer_id or '').replace('WH-PAYER-', '')


def _days(a, b):
    try:
        return (date.fromisoformat(b) - date.fromisoformat(a)).days
    except (TypeError, ValueError):
        return None


def _near(a, b, days=31):
    n = _days(a, b)
    return n is not None and abs(n) <= days


def _minus_day(v):
    try:
        return (date.fromisoformat(v) - timedelta(days=1)).isoformat()
    except (TypeError, ValueError):
        return None


def _tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")}


def _rows(db, table, cols, where=''):
    """Rows of a landing table as dicts with the requested columns (absent columns -> None). Missing table -> []."""
    try:
        have = [r[1] for r in db.execute(f'PRAGMA table_info("{table}")')]
    except Exception:  # noqa: BLE001
        return []
    if not have:
        return []
    want = ['_feed', '_instance', '_drop', '_file', '_row'] + [c for c in cols if c not in
                                                                 ('_feed', '_instance', '_drop', '_file', '_row')]
    sel = [c for c in want if c in have]
    out = []
    for r in db.execute(f'SELECT {", ".join(chr(34) + c + chr(34) for c in sel)} FROM "{table}" {where}'):
        d = dict.fromkeys(want)
        d.update(zip(sel, r))
        out.append(d)
    return out


def _variant(path):
    m = VARIANT.search((path or '').rsplit('/', 1)[-1])
    return m.group(1).lower() if m else None


def _keep_files(rep, table, rows, variant_col=None):
    """Drop rows of _RESEND copies (when the original arrived) and of originals that a _v2 replaces."""
    groups = defaultdict(set)
    for r in rows:
        f = r.get('_file') or ''
        groups[VARIANT.sub('', f)].add(f)
    keep = set()
    for base, files in groups.items():
        v2 = {f for f in files if (_variant(f) or '').startswith('v')}
        orig = {f for f in files if _variant(f) is None}
        if v2:
            keep |= v2
        elif orig:
            keep |= orig
        else:
            keep |= files
    out = []
    for r in rows:
        f = r.get('_file') or ''
        if f in keep:
            out.append(r)
        else:
            rep['dropped']['superseded_by_v2' if _variant(f) is None else 'resend_file'] += 1
            rep['dropped_by_table'][f'{table}:{"superseded_by_v2" if _variant(f) is None else "resend_file"}'] += 1
    return out


def _isa_filter(rep, table, rows):
    """Same ISA13 interchange control number in the same sender folder = a resent interchange: keep the first file."""
    owner = {}
    for f, isa in sorted({(r.get('_file') or '', r.get('isa_control_number')) for r in rows},
                         key=lambda x: (_variant(x[0]) is not None, x[0])):
        key = ('/'.join(f.split('/')[:-2]), isa)
        owner.setdefault(key, f)
    out = []
    for r in rows:
        f = r.get('_file') or ''
        if owner.get(('/'.join(f.split('/')[:-2]), r.get('isa_control_number'))) == f:
            out.append(r)
        else:
            rep['dropped']['resent_interchange'] += 1
            rep['dropped_by_table'][f'{table}:resent_interchange'] += 1
    return out


def _new_report():
    return {'dropped': Counter(), 'dropped_by_table': Counter(), 'emitted': Counter(), 'cross_check': Counter(),
            'unmapped': {}}


class _Ctx:
    def __init__(self, db, resolve):
        self.db = db
        self.resolve = resolve
        self.rep = _new_report()
        self.tables = _tables(db)
        self.cache = {}
        self.spans = defaultdict(list)      # (payer_id, member_id) -> [(start, end, product, plan_id)]
        self.member_payers = defaultdict(set)  # member id -> payer ids

    def person(self, payer_id, member_id):
        member_id = _s(member_id)
        if not member_id:
            return None
        key = (payer_id, member_id)
        if key in self.cache:
            return self.cache[key]
        # identity.py conventions: member:<WH-PAYER-..>:<id> and mbi:<MBI> (helpers normalise and refuse junk ids)
        keys = [k for k in (identity.member_key(payer_id, member_id) if payer_id else None,
                            identity.mbi_key(member_id)) if k]
        pid = None
        for k in keys:
            pid = self.resolve(k)
            if pid:
                break
        self.cache[key] = pid
        return pid

    def product(self, payer_id, member_id, when=None):
        spans = self.spans.get((payer_id, member_id)) or []
        if when:
            cover = [s for s in spans if s[0] and s[0] <= when and (not s[1] or s[1] >= when) and s[2]]
            if cover:
                return max(cover, key=lambda s: s[0] or '')[2]
        dated = [s for s in spans if s[2]]
        if dated:
            return max(dated, key=lambda s: s[0] or '')[2]
        return PAYER_PRODUCT.get(_short(payer_id))

    def emit(self, rec):
        self.rep['emitted'][rec['source_table']] += 1
        return rec


def _envelope(ctx, stream, rid, payer_id, member_id, event_date, recorded, available, lineage, supersedes=None,
              source_system=None, person=None):
    pid = person if person is not None else ctx.person(payer_id, member_id)
    return {'record_id': rid, 'patient_id': pid, 'source_table': stream, 'source_system': source_system or payer_id,
            'organization_id': source_system or payer_id, 'event_date': event_date, 'recorded_date': recorded or event_date,
            'available_date': available or recorded or event_date, 'encounter_id': None, 'source_patient_id': member_id,
            'supersedes_record_id': supersedes, 'event_date_precision': 'day', 'synthetic': True,
            'identity_link_basis': 'mbi' if member_id and MBI.match(member_id) else 'payer_member_id',
            '_lineage': _lineage(lineage)}


# ----------------------------------------------------------------------------------------------------------- entry point
def records(db, resolve):
    global _REPORT
    ctx = _Ctx(db, resolve)
    _REPORT = ctx.rep
    yield from _enrollment(ctx)
    yield from _medical(ctx)
    yield from _pharmacy(ctx)
    yield from _sdoh(ctx)
    yield from _provider(ctx)
    yield from _deaths(ctx)
    ctx.rep['unmapped'] = UNMAPPED
    _REPORT = ctx.rep


UNMAPPED = {
    'payer_medical_claims.network_status': 'only the 10 flat payers send NETWORK; X12-only payers (0, 1, 2, MSSP, SUP1, SUP2) '
                                           'and CCLF claims have no network indicator',
    'payer_medical_claims.claim_received_date': 'no DTM*050 in the payer 835; the 837 interchange date (ISA09) is used; '
                                                'CCLF claims have none (NULL)',
    'payer_medical_claims.allowed_amount': 'the payer 835 sends no AMT*AU / SVC allowed: flat line ALLOWED, CCLF allowed, '
                                           'else paid + patient responsibility for paid events (0 for denials)',
    'payer_medical_claims.total_charge (CCLF Part B/DME)': 'CCLF5/6 carry no submitted charge (NULL)',
    'payer_pharmacy_claims.rxcui': 'no feed carries RxNorm for fills (NCPDP, flat pharmacy and CCLF7 send NDC only)',
    'payer_pharmacy_claims.pharmacy_name': 'NCPDP and flat pharmacy send the pharmacy NPI only',
    'payer_pharmacy_claims.pharmacy_type': 'only CCLF7 (clm_phrmcy_srvc_type_cd) carries it; filled by pharmacy NPI',
    'payer_pharmacy_claims.formulary_tier': 'not sent by NCPDP, flat pharmacy or CCLF7',
    'enrollment_spans.attribution_npi (834)': '834 NM1*P3 PCP loop is absent; PCP comes from flat ELIGIBILITY only',
    'medical_claims.allowed_cents (provider 835)': 'no AMT*AU: paid + patient responsibility (0 for denials)',
    'medical_claims.encounter_id / account_id / care_record_id': 'the practice 837 carries no encounter, account or EHR '
                                                                 'record reference (REF*EA / REF*D9 absent)',
    'claim_corrections / claim_acknowledgements / payer_cash_allocations': 'no 277CA acknowledgements or EFT allocation '
                                                                           'records in the delivery; frequency-7 '
                                                                           'resubmissions carry no correction reason',
    'care gaps / risk scores / MSSP assignment': 'no canonical stream in canonical_streams.json; QALR only fills MSSP '
                                                 'attribution_npi',
}


# =========================================================================================================== enrollment
def _enrollment(ctx):
    rep = ctx.rep
    out = []
    cclf8 = _cclf8(ctx)
    death = {m: b['death'] for m, b in cclf8.items() if b.get('death')}
    flat = _flat_eligibility(ctx)
    loops = _rows(ctx.db, 'x12_834_member', [
        'isa_control_number', 'file_type', 'file_date', 'interchange_date', 'maintenance_type_code',
        'maintenance_reason_code', 'member_id', 'subscriber_id', 'eligibility_begin_date', 'eligibility_end_date',
        'benefit_begin_date', 'benefit_end_date', 'plan_coverage_description', 'group_policy_number', 'pcp_npi',
        'pcp_name', 'last_name', 'first_name', 'dob'])
    loops = _isa_filter(rep, 'x12_834_member', _keep_files(rep, 'x12_834_member', loops))
    first_file = {}
    for r in loops:
        p = _payer_id(r['_instance'])
        fd = _d(r['file_date']) or _d(r['interchange_date']) or _file_date(r['_file'])
        r['_payer'], r['_fd'] = p, fd
        if fd and (p not in first_file or fd < first_file[p]):
            first_file[p] = fd
    loops.sort(key=lambda r: (r['_payer'] or '', r['member_id'] or '', r['_fd'] or '',
                              0 if r['file_type'] == 'change' else 1, r['_file'] or '', r['_row'] or 0))
    by_member = defaultdict(list)
    demo = {}
    for r in loops:
        if _s(r['member_id']):
            by_member[(r['_payer'], r['member_id'].strip())].append(r)
            demo.setdefault((r['_payer'], r['member_id'].strip()),
                            ((_s(r['last_name']) or '').lower(), (_s(r['first_name']) or '').lower(), _d(r['dob'])))
    covered = set()
    for (payer, member), rows in by_member.items():
        covered.add((payer, member))
        out += _spans_834(ctx, payer, member, rows, first_file.get(payer), death)
    # flat-only members (no 834 loop at that payer)
    for (payer, member), snaps in flat.items():
        if (payer, member) in covered:
            continue
        rep['cross_check']['flat_eligibility_only_members'] += 1
        out += _spans_flat(ctx, payer, member, snaps)
    # fill from flat eligibility
    for v in out:
        snaps = flat.get((v['payer_id'], v['member_id'])) or []
        same = [s for s in snaps if s['eff'] == v['coverage_start']] or snaps
        if not same:
            continue
        last = max(same, key=lambda s: (s['fd'] or '', s['_file'], s['_row']))
        v.setdefault('plan_id', None)
        v['plan_id'] = v.get('plan_id') or last['plan']
        v['attribution_npi'] = v.get('attribution_npi') or last['pcp_npi']
        v['attribution_practice_id'] = v.get('attribution_practice_id') or last['pcp_name']
        if not v.get('product') and last['product'] in PRODUCTS:
            v['product'] = last['product']
        if v.get('_final') and not v.get('coverage_end') and last['term']:
            v['coverage_end'] = last['term']
            v['coverage_end_source'] = 'flat_eligibility'
            rep['cross_check']['coverage_end_from_flat_eligibility'] += 1
        v['_lineage_extra'] = [_lin('payers_eligibility', s) for s in same]
    # a beneficiary whose Medicare FFS coverage already arrives as an 834 MEDICARE_FFS span (payers 0/1/2, MBI as member
    # id) gets no second span from CCLF enrollment
    ffs_834 = {v['member_id'] for v in out if v.get('product') in ('medicare_ffs', 'dual_ffs')}
    out += _spans_cclf(ctx, cclf8, {(v['payer_id'], v['member_id']) for v in out}, ffs_834)
    _link_payer_reissues(ctx, out, demo)
    _roster_practices(ctx, out)
    for v in out:
        info = cclf8.get(v['member_id'])
        if info and v['product'] in ('medicare_ffs', 'dual_ffs', None):
            v['dual_status'] = v.get('dual_status') or info.get('dual')
            v['medicare_entitlement_reason'] = v.get('medicare_entitlement_reason') or info.get('entitlement')
        ctx.spans[(v['payer_id'], v['member_id'])].append((v['coverage_start'], v.get('coverage_end'), v.get('product'),
                                                            v.get('plan_id')))
        ctx.member_payers[v['member_id']].add(v['payer_id'])
    for v in out:
        yield ctx.emit(_span_record(ctx, v))


def _span_record(ctx, v):
    lineage = v.pop('_lin') + v.pop('_lineage_extra', [])
    rec = _envelope(ctx, 'enrollment_spans', v['_rid'], v['payer_id'], v['member_id'], v['coverage_start'],
                    v['_recorded'], v['_available'], lineage, v.get('_supersedes'))
    for k in ('member_id', 'payer_id', 'product', 'plan_id', 'coverage_start', 'coverage_end', 'enrollment_reason',
              'termination_reason', 'dual_status', 'medicare_entitlement_reason', 'attribution_practice_id',
              'attribution_npi', 'subscriber_id', 'span_version', 'prior_coverage_end', 'previous_member_id',
              'member_id_reissued', 'coverage_end_source', 'maintenance_type_code', 'enrollment_source',
              'attribution_practice_source'):
        rec[k] = v.get(k)
    return rec


def _spans_834(ctx, payer, member, rows, first_file, death):
    rep = ctx.rep
    spans = {}     # coverage_start -> current version dict
    order = []     # all versions in creation order
    last_active = {}  # coverage_start -> latest file date that reported the span open
    for r in rows:
        mt = _s(r['maintenance_type_code'])
        start = _d(r['eligibility_begin_date']) or _d(r['benefit_begin_date'])
        end = _d(r['eligibility_end_date']) or _d(r['benefit_end_date'])
        product = PRODUCT_834.get((_s(r['plan_coverage_description']) or '').upper())
        fd = r['_fd']
        lin = _lin('x12_834_member', r)
        if mt == '024':
            target = spans.get(start)
            if target is None:
                opens = [s for s in spans.values() if (s['coverage_start'] or '') <= (end or '9999')]
                target = max(opens, key=lambda s: s['coverage_start'] or '') if opens else None
            if target is None:
                rep['cross_check']['834_term_without_span'] += 1
                v = _new_span(payer, member, start, end, product, r, fd, 'unknown', lin)
                v['termination_reason'] = 'disenrolled'
                spans[start] = v
                order.append(v)
                continue
            if target['coverage_end'] == end:
                rep['dropped']['repeat_834_loop'] += 1
                target['_lin'].append(lin)
                continue
            retro = bool(end and last_active.get(target['coverage_start']) and end < last_active[target['coverage_start']])
            reason = 'retroactive_termination' if retro else 'disenrolled'
            dd = death.get(member)
            if dd and end and _near(dd, end):
                reason = 'death'
            v = _version(target, fd, lin, coverage_end=end, termination_reason=reason, maintenance_type_code=mt)
            spans[target['coverage_start']] = v
            order.append(v)
            continue
        # 021 add, 030 audit/full snapshot (and anything else that names a span)
        if start in spans:
            s = spans[start]
            changed = {}
            if product and product != s['product']:
                changed['product'] = product
            if mt == '030' and s['coverage_end'] != end:
                changed['coverage_end'] = end
                changed['termination_reason'] = None if end is None else 'disenrolled'
                if end is None:
                    changed['enrollment_reason'] = 'reinstated'
            if changed:
                v = _version(s, fd, lin, maintenance_type_code=mt, **changed)
                spans[start] = v
                order.append(v)
            else:
                rep['dropped']['repeat_834_loop'] += 1
                s['_lin'].append(lin)
        else:
            prev = [s for s in spans.values() if (s['coverage_start'] or '') < (start or '')]
            if mt == '030' and fd == first_file:
                reason = 'baseline'
            elif prev and product and max(prev, key=lambda s: s['coverage_start'] or '')['product'] != product:
                reason = 'coverage_transition'
            elif prev:
                reason = 'regained_coverage'
            else:
                reason = 'new_enrollment'
            v = _new_span(payer, member, start, end, product, r, fd, reason, lin)
            spans[start] = v
            order.append(v)
        if end is None or (fd and end >= fd):
            last_active[start] = max(last_active.get(start) or '', fd or '')
    finals = {id(v) for v in spans.values()}
    for v in order:
        v['_final'] = id(v) in finals
    return order


def _new_span(payer, member, start, end, product, r, fd, reason, lin):
    return {'payer_id': payer, 'member_id': member, 'product': product or PAYER_PRODUCT.get(_short(payer)),
            'coverage_start': start, 'coverage_end': end, 'enrollment_reason': reason, 'termination_reason': None,
            'subscriber_id': _s(r.get('subscriber_id')), 'plan_id': _s(r.get('group_policy_number')),
            'attribution_npi': _s(r.get('pcp_npi')), 'span_version': 1, 'prior_coverage_end': None,
            'maintenance_type_code': _s(r.get('maintenance_type_code')), 'enrollment_source': 'x12_834',
            '_rid': _rid('enrollment_spans', payer, member, start, 1), '_recorded': fd, '_available': fd,
            '_supersedes': None, '_lin': [lin]}


def _version(prev, fd, lin, **changes):
    v = {k: val for k, val in prev.items() if not k.startswith('_')}
    v.update(changes)
    v['span_version'] = prev['span_version'] + 1
    v['prior_coverage_end'] = prev['coverage_end']
    v['_rid'] = _rid('enrollment_spans', v['payer_id'], v['member_id'], v['coverage_start'], v['span_version'])
    v['_recorded'] = v['_available'] = fd
    v['_supersedes'] = prev['_rid']
    v['_lin'] = [lin]
    return v


def _flat_eligibility(ctx):
    rows = _rows(ctx.db, 'payers_eligibility', ['member_id', 'subscriber_id', 'product', 'plan_id', 'eff_date', 'term_date',
                                                 'pcp_npi', 'pcp_name', 'payer', 'file_variant'])
    rows = _keep_files(ctx.rep, 'payers_eligibility', rows)
    out = defaultdict(list)
    seen = set()
    for r in rows:
        member = _s(r['member_id'])
        payer = _payer_id(r['payer'] or r['_instance'])
        if member and not identity.normalize_member_id(member):  # column-shifted rows: product/zip/fragment as id
            ctx.rep['dropped']['malformed_flat_eligibility_row'] += 1
            continue
        product = (_s(r['product']) or '').lower()
        if not member or not payer:
            continue
        if not _d(r['eff_date']):  # a snapshot without an effective date cannot be a span (malformed row)
            ctx.rep['dropped']['malformed_flat_eligibility_row'] += 1
            continue
        snap = {'eff': _d(r['eff_date']), 'term': _d(r['term_date']), 'product': product if product in PRODUCTS else None,
                'plan': _s(r['plan_id']), 'pcp_npi': _s(r['pcp_npi']), 'pcp_name': _s(r['pcp_name']),
                'subscriber': _s(r['subscriber_id']), 'fd': _file_date(r['_file']), '_file': r['_file'], '_row': r['_row']}
        key = (payer, member, snap['fd'], snap['eff'], snap['term'], snap['product'], snap['plan'], snap['pcp_npi'])
        if key in seen:
            ctx.rep['dropped']['repeat_flat_eligibility_row'] += 1
            continue
        seen.add(key)
        out[(payer, member)].append(snap)
    return out


def _spans_flat(ctx, payer, member, snaps):
    out = []
    by_start = defaultdict(list)
    for s in snaps:
        by_start[s['eff']].append(s)
    for start, ss in sorted(by_start.items(), key=lambda x: x[0] or ''):
        ss.sort(key=lambda s: (s['fd'] or '', s['_file'], s['_row']))
        cur = None
        for s in ss:
            lin = ('payers_eligibility', s['_file'], s['_row'])
            if cur and (cur['coverage_end'], cur['product'], cur['plan_id']) == (s['term'], s['product'], s['plan']):
                cur['_lin'].append(lin)
                continue
            if cur is None:
                cur = {'payer_id': payer, 'member_id': member, 'product': s['product'] or PAYER_PRODUCT.get(_short(payer)),
                       'coverage_start': start, 'coverage_end': s['term'], 'enrollment_reason': 'baseline',
                       'termination_reason': None, 'subscriber_id': s['subscriber'], 'plan_id': s['plan'],
                       'attribution_npi': s['pcp_npi'], 'attribution_practice_id': s['pcp_name'], 'span_version': 1,
                       'prior_coverage_end': None, 'enrollment_source': 'flat_eligibility',
                       '_rid': _rid('enrollment_spans', payer, member, start, 1), '_recorded': s['fd'],
                       '_available': s['fd'], '_supersedes': None, '_lin': [lin]}
            else:
                cur = _version(cur, s['fd'], lin, coverage_end=s['term'], product=s['product'] or cur['product'],
                               plan_id=s['plan'] or cur['plan_id'],
                               termination_reason='disenrolled' if s['term'] and not s['term'].startswith('9999') else None)
            out.append(cur)
        if cur:
            cur['_final'] = True
    return out


def _cclf8(ctx):
    rows = _rows(ctx.db, 'cclf_beneficiary_demographics', [
        'bene_mbi_id', 'bene_dob', 'bene_1st_name', 'bene_last_name', 'bene_dual_stus_cd', 'bene_orgnl_entlmt_rsn_cd',
        'bene_death_dt', 'bene_rng_bgn_dt', 'bene_rng_end_dt', 'file_date'])
    rows = _keep_files(ctx.rep, 'cclf_beneficiary_demographics', rows)
    out = {}
    for r in sorted(rows, key=lambda r: (_d(r['file_date']) or _file_date(r['_file']) or '', r['_file'], r['_row'])):
        m = _s(r['bene_mbi_id'])
        if not m:
            continue
        b = out.setdefault(m, {'rows': [], 'begin': None, 'end': None})
        b['rows'].append(_lin('cclf_beneficiary_demographics', r))
        b['dual'] = DUAL.get(_s(r['bene_dual_stus_cd']) or '') if _s(r['bene_dual_stus_cd']) else b.get('dual')
        b['entitlement'] = ENTITLEMENT.get(_s(r['bene_orgnl_entlmt_rsn_cd']) or '', b.get('entitlement'))
        b['death'] = _d(r['bene_death_dt']) or b.get('death')
        bg, en = _d(r['bene_rng_bgn_dt']), _d(r['bene_rng_end_dt'])
        if bg and (not b['begin'] or bg < b['begin']):
            b['begin'] = bg
        b['end'] = en  # latest delivery's range end
        b['name'] = ((_s(r['bene_last_name']) or '').lower(), (_s(r['bene_1st_name']) or '').lower().split(' ')[0],
                     _d(r['bene_dob']))
        b['fd'] = _d(r['file_date']) or _file_date(r['_file'])
    return out


def _spans_cclf(ctx, cclf8, existing, ffs_834=frozenset()):
    rep = ctx.rep
    payer = 'WH-PAYER-MSSP'
    rows = _keep_files(rep, 'cclf_enrollment', _rows(ctx.db, 'cclf_enrollment', [
        'current_bene_mbi_id', 'enrollment_start_date', 'enrollment_end_date', 'file_date']))
    xref = _keep_files(rep, 'cclf_beneficiary_xref', _rows(ctx.db, 'cclf_beneficiary_xref', [
        'crnt_num', 'prvs_num', 'prvs_id_obslt_dt', 'file_date']))
    qalr = _rows(ctx.db, 'programs_mssp_qalr', ['mbi', 'assigned_npi', 'assigned_tin'])
    attrib = {}
    for r in sorted(qalr, key=lambda r: (r['_file'] or '', r['_row'] or 0)):
        if _s(r['mbi']):
            attrib[r['mbi'].strip()] = (_s(r['assigned_npi']), _lin('programs_mssp_qalr', r))
    # MBI re-issue: CCLF9 names the new MBI and the date the old one stopped; the old one is the CCLF8 MBI with the same
    # name and DOB whose range ends the day before (prvs_num is blank in this delivery).
    reissue = {}
    by_name = defaultdict(list)
    for m, b in cclf8.items():
        if b.get('name') and b['name'][2]:
            by_name[b['name']].append(m)
    for r in xref:
        new = _s(r['crnt_num'])
        if not new or new in reissue:
            continue
        old = _s(r['prvs_num'])
        if not old and new in cclf8:
            cands = [m for m in by_name.get(cclf8[new].get('name'), []) if m != new]
            old = cands[0] if len(cands) == 1 else None
        if old:
            reissue[new] = (old, _d(r['prvs_id_obslt_dt']), _lin('cclf_beneficiary_xref', r))
    old_of = {o: (n, d) for n, (o, d, _) in reissue.items()}
    spans = {}
    for r in sorted(rows, key=lambda r: (r['_file'] or '', r['_row'] or 0)):
        m, start = _s(r['current_bene_mbi_id']), _d(r['enrollment_start_date'])
        if not m:
            continue
        # the enrollment file sends 5 re-issued MBIs with one trailing character more than CCLF8/CCLF9 (12 chars);
        # the 11-character MBI is the beneficiary key everywhere else, so the span is keyed on it
        if len(m) == 12 and m[:11] in cclf8 and not identity.normalize_mbi(m):
            rep['cross_check']['cclf_enrollment_mbi_with_extra_character'] += 1
            m = m[:11]
        key = (m, start)
        if key in spans:
            rep['dropped']['repeat_cclf_enrollment_row'] += 1
            spans[key]['_lin'].append(_lin('cclf_enrollment', r))
            if _d(r['enrollment_end_date']):
                spans[key]['coverage_end'] = _d(r['enrollment_end_date'])
            continue
        fd = _d(r['file_date']) or _file_date(r['_file'])
        spans[key] = {'coverage_start': start, 'coverage_end': _d(r['enrollment_end_date']), '_fd': fd,
                      '_lin': [_lin('cclf_enrollment', r)], 'source': 'cclf_enrollment'}
    have = {m for m, _ in spans}
    for m, b in cclf8.items():
        if m in have or not b.get('begin'):
            continue
        spans[(m, b['begin'])] = {'coverage_start': b['begin'], 'coverage_end': b.get('end') if b.get('end') and
                                  m in old_of else None, '_fd': b.get('fd'), '_lin': list(b['rows'][-1:]),
                                  'source': 'cclf8_range'}
        rep['cross_check']['mssp_span_from_cclf8_range'] += 1
    out = []
    for (m, start), s in sorted(spans.items(), key=lambda x: (x[0][0], x[0][1] or '')):
        if (payer, m) in existing:
            continue
        if m in ffs_834:
            rep['dropped']['cclf_span_covered_by_834_medicare_ffs'] += 1
            continue
        info = cclf8.get(m, {})
        dual = info.get('dual')
        v = {'payer_id': payer, 'member_id': m, 'product': 'dual_ffs' if dual else 'medicare_ffs',
             'plan_id': None, 'coverage_start': start, 'coverage_end': s['coverage_end'],
             'enrollment_reason': 'baseline', 'termination_reason': None, 'dual_status': dual,
             'medicare_entitlement_reason': info.get('entitlement'), 'span_version': 1, 'prior_coverage_end': None,
             'enrollment_source': s['source'], '_rid': _rid('enrollment_spans', payer, m, start, 1),
             '_recorded': s['_fd'], '_available': s['_fd'], '_supersedes': None, '_lin': s['_lin'], '_final': True}
        if m in attrib:
            v['attribution_npi'] = attrib[m][0]
            v['_lin'] = v['_lin'] + [attrib[m][1]]
        if info.get('death') and v['coverage_end'] and _near(info['death'], v['coverage_end']):
            v['termination_reason'] = 'death'
        elif v['coverage_end']:
            v['termination_reason'] = 'disenrolled'
        if m in reissue:
            old, obsolete, lin = reissue[m]
            v['previous_member_id'] = old
            v['member_id_reissued'] = True
            v['enrollment_reason'] = 'member_id_reissued'
            v['_lin'] = v['_lin'] + [lin]
            rep['cross_check']['mbi_reissues_linked'] += 1
        if m in old_of:
            v['termination_reason'] = 'member_id_reissued'
            v['member_id_reissued'] = True
            v['coverage_end'] = v['coverage_end'] or _minus_day(old_of[m][1])
        out.append(v)
    # the re-issued MBI's span is linked to the old MBI's span (previous_member_id) but does not supersede it: the old span
    # is real coverage that ended when the new id began (native definitions keep spans followed by a reissue)
    return out


def _roster_practices(ctx, out):
    """attribution_npi without a practice (Medicare FFS / dual FFS: QALR assigns a group NPI) -> practice through the ACO
    roster: group_npi first, else provider_npi; the latest roster snapshot wins."""
    rows = _keep_files(ctx.rep, 'rosters_aco_provider_roster', _rows(ctx.db, 'rosters_aco_provider_roster',
                                                                      ['group_npi', 'provider_npi', 'practice_id']))
    by_group, by_prov = {}, {}
    for r in sorted(rows, key=lambda r: (_file_date(r['_file']) or '', r['_file'], r['_row'])):
        pr = _s(r['practice_id'])
        if not pr or not pr.startswith('WHN-'):
            continue
        if _s(r['group_npi']):
            by_group[r['group_npi'].strip()] = (pr, _lin('rosters_aco_provider_roster', r))
        if _s(r['provider_npi']):
            by_prov[r['provider_npi'].strip()] = (pr, _lin('rosters_aco_provider_roster', r))
    for v in out:
        npi = _s(v.get('attribution_npi'))
        if not npi or v.get('attribution_practice_id'):
            continue
        hit = by_group.get(npi) or by_prov.get(npi)
        if hit:
            v['attribution_practice_id'] = hit[0]
            v['attribution_practice_source'] = 'roster_group_npi' if npi in by_group else 'roster_provider_npi'
            v.setdefault('_lineage_extra', [])
            v['_lineage_extra'] = v['_lineage_extra'] + [hit[1]]
            ctx.rep['cross_check']['attribution_practice_from_roster'] += 1
        else:
            ctx.rep['cross_check']['attribution_npi_not_on_roster'] += 1


def _link_payer_reissues(ctx, out, demo):
    """Payer member-id re-issue: at one payer, the same person (834 last name, first name, DOB) gets a new member id
    whose span starts the day after the old id's span ends. The new span is enrollment_reason member_id_reissued with
    previous_member_id; the old span is kept (termination_reason member_id_reissued), not superseded, because it is the
    coverage before the reissue. Several ids of one person with overlapping or non-contiguous spans (duplicate
    enrollments) stay separate."""
    finals = [v for v in out if v.get('_final') and v.get('enrollment_source') == 'x12_834']
    groups = defaultdict(list)
    for v in finals:
        d = demo.get((v['payer_id'], v['member_id']))
        if d and all(d):
            groups[(v['payer_id'],) + d].append(v)
    firsts = {(v['payer_id'], v['member_id'], v['coverage_start']): v for v in out if v.get('span_version') == 1}
    for vs in groups.values():
        if len({v['member_id'] for v in vs}) < 2:
            continue
        for a in vs:
            end = a.get('coverage_end')
            if not end or end.startswith('9999'):
                continue
            nxt = (date.fromisoformat(end) + timedelta(days=1)).isoformat()
            for b in vs:
                if b['member_id'] == a['member_id'] or b['coverage_start'] != nxt:
                    continue
                first = firsts.get((b['payer_id'], b['member_id'], b['coverage_start']))
                if first is None or first.get('previous_member_id'):
                    continue
                for x in [v for v in out if v['payer_id'] == b['payer_id'] and v['member_id'] == b['member_id']
                          and v['coverage_start'] == b['coverage_start']]:
                    x['enrollment_reason'] = 'member_id_reissued'
                    x['previous_member_id'] = a['member_id']
                    x['member_id_reissued'] = True
                a['termination_reason'] = 'member_id_reissued'
                a['member_id_reissued'] = True
                ctx.rep['cross_check']['payer_member_id_reissues_linked'] += 1
                break


# ========================================================================================================= medical claims
def _split_claim_id(cid):
    m = CLAIM_ID.match(cid or '')
    if not m:
        return cid, ''
    return m.group('base'), m.group('suffix')


def _proc_system(code):
    code = _s(code)
    if not code:
        return None
    return 'HCPCS' if code[0].isalpha() else 'CPT'


def _network(v):
    v = (_s(v) or '').upper()
    if v.startswith('OUT'):
        return 'out_of_network'
    if v.startswith('IN'):
        return 'in_network'
    return None


def _medical(ctx):
    rep = ctx.rep
    db = ctx.db
    # ---- payer-side 837 (header, diagnoses, lines)
    c837 = _rows(db, 'x12_837_claim', [
        'isa_control_number', 'claim_key', 'transaction_type', 'patient_control_number', 'subscriber_member_id',
        'patient_member_id', 'payer_id', 'total_charge', 'facility_code', 'place_of_service', 'frequency_code',
        'original_reference', 'statement_from', 'statement_to', 'admission_date', 'discharge_date', 'admission_type',
        'admission_source', 'patient_status', 'principal_diagnosis', 'drg', 'billing_provider_npi', 'rendering_npi',
        'attending_npi', 'insurance_type', 'group_number', 'claim_filing_indicator', 'interchange_date', 'line_count'],
        "WHERE _feed IN ('x12_payer', 'x12')")
    payer837 = [r for r in c837 if r['_feed'] == 'x12_payer']
    prov837 = [r for r in c837 if r['_feed'] == 'x12']
    payer837 = _isa_filter(rep, 'x12_837_claim', _keep_files(rep, 'x12_837_claim', payer837))
    prov837 = _isa_filter(rep, 'x12_837_claim(provider)', _keep_files(rep, 'x12_837_claim(provider)', prov837))
    keep_files = {r['_file'] for r in payer837} | {r['_file'] for r in prov837}
    dx = defaultdict(list)
    for r in _rows(db, 'x12_837_dx', ['claim_key', 'dx_sequence', 'dx_code', 'is_principal'],
                   "WHERE _feed IN ('x12_payer', 'x12')"):
        if r['_file'] in keep_files:
            dx[(r['_file'], r['claim_key'])].append(r)
    lines = defaultdict(list)
    for r in _rows(db, 'x12_837_line', ['claim_key', 'line_number', 'line_ordinal', 'procedure_code', 'revenue_code',
                                        'charge', 'units', 'service_date', 'service_date_to', 'rendering_npi',
                                        'place_of_service'], "WHERE _feed IN ('x12_payer', 'x12')"):
        if r['_file'] in keep_files:
            lines[(r['_file'], r['claim_key'])].append(r)
    # provider-side copies: dropped, but indexed for line fill and source_billing_claim_id
    prov_by_id = {}
    for r in prov837:
        pcn = _s(r['patient_control_number'])
        if not pcn:
            continue
        cid = pcn if pcn.startswith('WHN-') else 'WHN-CLM-' + pcn
        prov_by_id.setdefault(cid, r)
    # ---- payer-side 837 by claim id
    sub = {}       # claim id -> submission loop (freq 1/7)
    void = {}      # claim id -> void loop (freq 8)
    seen_loop = set()
    for r in sorted(payer837, key=lambda r: (r['interchange_date'] or '', r['_file'], r['_row'])):
        cid = _s(r['patient_control_number'])
        if not cid:
            continue
        freq = _s(r['frequency_code']) or '1'
        if (cid, freq) in seen_loop:
            rep['dropped']['repeat_837_loop'] += 1
            continue
        seen_loop.add((cid, freq))
        (void if freq == '8' else sub)[cid] = r
    # ---- payer-side 835 events
    c835 = _rows(db, 'x12_835_claim', [
        'isa_control_number', 'st_control_number', 'claim_key', 'patient_control_number', 'claim_status_code',
        'total_charge', 'paid_amount', 'patient_responsibility', 'claim_filing_indicator', 'payer_claim_control_number',
        'facility_code', 'frequency_code', 'patient_id', 'patient_id_qualifier', 'insured_id', 'original_reference',
        'statement_from', 'statement_to', 'received_date', 'drg_code', 'patient_status_code', 'remark_codes',
        'rendering_npi'], "WHERE _feed IN ('x12_payer', 'x12')")
    prov835 = [r for r in c835 if r['_feed'] == 'x12']
    rep['dropped']['provider_835_kept_out_of_payer_streams'] += len(prov835)
    rep['dropped_by_table']['x12_835_claim(provider):provider_side_copy'] += len(prov835)
    rep['dropped']['provider_837_kept_out_of_payer_streams'] += len(prov837)
    rep['dropped_by_table']['x12_837_claim(provider):provider_side_copy'] += len(prov837)
    pay835 = [r for r in c835 if r['_feed'] == 'x12_payer']
    pay835 = _isa_filter(rep, 'x12_835_claim', _keep_files(rep, 'x12_835_claim', pay835))
    files835 = {r['_file'] for r in pay835}
    adj = defaultdict(list)
    for r in _rows(db, 'x12_835_adjustment', ['claim_key', 'level', 'cas_sequence', 'group_code', 'reason_code', 'amount'],
                   "WHERE _feed = 'x12_payer'"):
        if r['_file'] in files835:
            adj[(r['_file'], r['claim_key'])].append(r)
    svc = defaultdict(list)
    for r in _rows(db, 'x12_835_line', ['claim_key', 'procedure_code', 'allowed_amount', 'paid_amount', 'charge'],
                   "WHERE _feed = 'x12_payer'"):
        if r['_file'] in files835:
            svc[(r['_file'], r['claim_key'])].append(r)
    paydate = {}
    for r in _rows(db, 'x12_835_payment', ['isa_control_number', 'st_control_number', 'effective_date', 'interchange_date'],
                   "WHERE _feed = 'x12_payer'"):
        paydate[(r['_file'], r['isa_control_number'], r['st_control_number'])] = (_d(r['effective_date']),
                                                                                 _d(r['interchange_date']))
    events = defaultdict(list)
    seen_ev = set()
    for r in sorted(pay835, key=lambda r: (r['_file'], r['_row'])):
        cid = _s(r['patient_control_number'])
        if not cid:
            continue
        key = (cid, _s(r['payer_claim_control_number']), _s(r['claim_status_code']), r['paid_amount'])
        if key in seen_ev:
            rep['dropped']['repeat_835_event'] += 1
            continue
        seen_ev.add(key)
        eff, idate = paydate.get((r['_file'], r['isa_control_number'], r['st_control_number']), (None, None))
        r['_paid_date'] = eff or idate or _file_date(r['_file'])
        r['_available'] = idate or eff
        events[cid].append(r)
    # ---- flat medical (10 payers): events per claim id
    flat = _flat_medical(ctx)
    # ---- CCLF (Medicare FFS)
    cclf = _cclf_claims(ctx)
    # ---- assemble
    # -D duplicate submissions stay their own claims: the payer denied them (CARC 18) and that denial is an event of the
    # payer feed; client_data marks CARC 18 as duplicate_submission so they never count in metrics.
    claim_ids = set(sub) | set(void) | set(events) | set(flat)
    rep['cross_check']['duplicate_submission_claims_kept'] = sum(1 for c in claim_ids if '-D' in _split_claim_id(c)[1])
    built = {}
    for cid in claim_ids:
        built[cid] = _build_claim(ctx, cid, sub.get(cid), void.get(cid), events.get(cid, []), flat.get(cid),
                                  dx, lines, adj, svc, prov_by_id)
    # provider-side claims with no payer copy at all (kept dropped; counted)
    for cid in prov_by_id:
        if cid not in built:
            rep['cross_check']['provider_837_without_payer_copy'] += 1
    # flat cross-check (10 flat payers): ids in flat but not in payer 837, and status agreement
    for cid, f in flat.items():
        if cid not in sub and cid not in void:
            rep['cross_check']['flat_claim_not_in_payer_837'] += 1
    # header-only -X crossover/secondary loops: the services are the primary claim's (same charge and diagnoses)
    for cid, c in built.items():
        base, suffix = _split_claim_id(cid)
        if '-X' not in suffix:
            continue
        prim = built.get(cid.replace('-X', '')) or built.get(base)
        if not prim:
            continue
        h, ph = c['header'], prim['header']
        if not c['lines'] and prim['lines']:
            c['lines'] = [dict(l, allowed_amount=None, paid_amount=None, _lin=list(l['_lin'])) for l in prim['lines']]
            rep['cross_check']['lines_from_primary_claim'] += 1
        for k in ('service_from', 'service_to', 'claim_type', 'place_of_service', 'type_of_bill', 'rendering_provider_npi'):
            if not h.get(k) and ph.get(k):
                h[k] = ph[k]
        if not h.get('diagnosis_codes') and ph.get('diagnosis_codes'):
            h['diagnosis_codes'], h['principal_diagnosis'] = ph['diagnosis_codes'], ph['principal_diagnosis']
    # MSSP X12 <-> CCLF dedupe
    _match_cclf(ctx, built, cclf)
    # -R / -X chains: originals, supersedes
    for cid, c in built.items():
        base, suffix = _split_claim_id(cid)
        if '-R' in suffix:
            orig = c['header'].get('original_payer_claim_id') or (base + ('-X' if '-X' in suffix else ''))
            if orig not in built and '-X' in suffix and base in built:
                orig = base
            c['header']['original_payer_claim_id'] = orig
            o = built.get(orig)
            if o and o['events'] and c['events']:
                first_date = c['events'][0]['paid_date'] or ''
                cands = [e for e in o['events'] if e['claim_status'] != 'voided' and (e['paid_date'] or '') <= first_date]
                tgt = (cands or [e for e in o['events'] if e['claim_status'] != 'voided'] or o['events'])[-1]
                c['events'][0]['_supersedes'] = tgt['_rid']
    for c in cclf['claims'].values():
        if not c.get('matched'):
            built[c['header']['payer_claim_id']] = c
    for cid in sorted(built):
        c = built[cid]
        yield from _emit_claim(ctx, c)


def _flat_medical(ctx):
    rep = ctx.rep
    cols = ['claim_id', 'claim_line', 'member_id', 'claim_type', 'bill_type', 'place_of_service', 'from_date', 'to_date',
            'paid_date', 'admit_date', 'discharge_date', 'drg'] + [f'dx{i}' for i in range(1, 13)] + [
        'procedure_code', 'revenue_code', 'units', 'billed', 'allowed', 'paid', 'member_liability', 'claim_status',
        'frequency_code', 'original_claim_id', 'billing_npi', 'rendering_npi', 'rendering_provider_npi', 'network',
        'payer', 'file_variant']
    rows = _rows(ctx.db, 'payers_medical', cols)
    incr = [r for r in rows if (r['file_variant'] or '') != 'full_restatement']
    restate = [r for r in rows if (r['file_variant'] or '') == 'full_restatement']
    incr = _keep_files(rep, 'payers_medical', incr)
    restate = _keep_files(rep, 'payers_medical(restatement)', restate)
    out = {}
    seen = set()

    def add(r, restatement):
        cid = _s(r['claim_id'])
        status = (_s(r['claim_status']) or '').lower()
        if not cid or status not in FLAT_MED_STATUS:
            rep['dropped']['malformed_flat_medical_row'] += 1
            return
        paid_date = _d(r['paid_date'])
        key = (cid, _s(r['claim_line']), paid_date, status, _s(r['paid']), _s(r['allowed']))
        if key in seen:
            rep['dropped']['full_restatement_repeat' if restatement else 'repeat_flat_medical_row'] += 1
            return
        seen.add(key)
        if restatement:
            rep['cross_check']['full_restatement_row_not_in_incrementals'] += 1
        c = out.setdefault(cid, {'rows': [], 'events': {}})
        c['rows'].append(r)
        ev = c['events'].setdefault((paid_date, status), {'paid_date': paid_date, 'status': status, 'lines': {},
                                                          'row': r, 'fd': _file_date(r['_file'])})
        ev['lines'][_s(r['claim_line']) or str(len(ev['lines']) + 1)] = r

    for r in sorted(incr, key=lambda r: (r['_file'], r['_row'])):
        add(r, False)
    for r in sorted(restate, key=lambda r: (r['_file'], r['_row'])):
        add(r, True)
    return out


def _carc(adjs):
    for a in sorted(adjs, key=lambda a: (a['level'] != 'claim', a['cas_sequence'] or 0)):
        reason = _s(a['reason_code'])
        if reason and reason not in ROUTINE_CARC:
            return f"{_s(a['group_code']) or 'CO'}-{reason}"
    return None


def _build_claim(ctx, cid, sloop, vloop, evs, flat, dx, lines, adj, svc, prov_by_id):
    rep = ctx.rep
    base, suffix = _split_claim_id(cid)
    head = sloop or vloop
    fl = None
    if flat:
        fl = sorted(flat['events'].values(), key=lambda e: (e['paid_date'] or '', e['fd'] or ''))
    frow = fl[0]['row'] if fl else None
    payer = _payer_id((head or {}).get('_instance') if head else None) or \
        _payer_id(evs[0]['_instance'] if evs else None) or _payer_id(frow['payer'] if frow else None)
    member = None
    if head:
        member = _s(head['subscriber_member_id']) or _s(head['patient_member_id'])
    if not member and evs:
        e = evs[0]
        q = _s(e['patient_id_qualifier'])
        member = _s(e['patient_id']) or (q if q and len(q) > 3 else None) or _s(e['insured_id'])
    if not member and frow:
        member = _s(frow['member_id'])
    lin = []
    h = {'payer_claim_id': cid, 'member_id': member, 'payer_id': payer}
    dxs = []
    if head:
        lin.append(_lin('x12_837_claim', head))
        for d in sorted(dx.get((head['_file'], head['claim_key']), []), key=lambda d: d['dx_sequence'] or 0):
            if _s(d['dx_code']):
                dxs.append(d['dx_code'].strip())
            lin.append(_lin('x12_837_dx', d))
        inst = (head['transaction_type'] or '') == '837I'
        h['claim_type'] = 'institutional' if inst else 'professional'
        fac, freq = _s(head['facility_code']), _s(head['frequency_code']) or '1'
        h['type_of_bill'] = ('0' + fac + freq) if inst and fac else None
        h['place_of_service'] = None if inst else (_s(head['place_of_service']) or fac)
        h['service_from'] = _d(head['statement_from'])
        h['service_to'] = _d(head['statement_to'])
        h['admission_date'] = _d(head['admission_date'])
        h['discharge_date'] = _d(head['discharge_date'])
        h['ms_drg'] = _s(head['drg'])
        h['admission_type'] = _s(head['admission_type'])
        h['admission_source'] = _s(head['admission_source'])
        h['discharge_status'] = DISCHARGE.get(_s(head['patient_status']) or '', _s(head['patient_status']))
        h['patient_status'] = _s(head['patient_status'])
        h['billing_provider_npi'] = _s(head['billing_provider_npi'])
        h['rendering_provider_npi'] = _s(head['rendering_npi']) or _s(head['attending_npi'])
        h['total_charge'] = _money(head['total_charge'])
        h['claim_received_date'] = _d(head['interchange_date'])
        h['claim_filing_indicator'] = _s(head['claim_filing_indicator'])
        h['insurance_type'] = _s(head['insurance_type']) or _s(head['group_number'])
        if sloop and _s(sloop['original_reference']):
            h['original_payer_claim_id'] = _s(sloop['original_reference'])
    if frow:
        flat_dx = [_s(frow[f'dx{i}']) for i in range(1, 13) if _s(frow[f'dx{i}'])]
        if not dxs:
            dxs = flat_dx
        h.setdefault('claim_type', {'P': 'professional', 'I': 'institutional'}.get((_s(frow['claim_type']) or '').upper()))
        for k, v in (('type_of_bill', _s(frow['bill_type'])), ('place_of_service', _s(frow['place_of_service'])),
                     ('service_from', _d(frow['from_date'])), ('service_to', _d(frow['to_date'])),
                     ('admission_date', _d(frow['admit_date'])), ('discharge_date', _d(frow['discharge_date'])),
                     ('ms_drg', _s(frow['drg'])), ('billing_provider_npi', _s(frow['billing_npi'])),
                     ('rendering_provider_npi', _s(frow['rendering_npi']) or _s(frow['rendering_provider_npi']))):
            if not h.get(k) and v:
                h[k] = v
        h['network_status'] = _network(frow['network'])
        if _s(frow['original_claim_id']) and not h.get('original_payer_claim_id'):
            h['original_payer_claim_id'] = _s(frow['original_claim_id'])
        if h.get('total_charge') is None:
            billed = [_num(l['billed']) for l in fl[0]['lines'].values() if _num(l['billed']) is not None]
            h['total_charge'] = round(sum(billed), 2) if billed else None
    # 837I never sends DTP*096 (discharge); for an inpatient bill type the statement-through date (DTP*434 end) is the
    # discharge, so it fills discharge_date with discharge_date_source saying it was derived
    if h.get('discharge_date'):
        h['discharge_date_source'] = 'x12_837_dtp096' if head and _d(head['discharge_date']) else 'flat_discharge_date'
    elif (h.get('type_of_bill') or '')[:3] in INPATIENT_TOB and h.get('service_to'):
        h['discharge_date'] = h['service_to']
        h['discharge_date_source'] = 'statement_through'
        rep['cross_check']['inpatient_discharge_from_statement_through'] += 1
    prov = prov_by_id.get(cid) or prov_by_id.get(base)
    if prov:
        h['source_billing_claim_id'] = _s(prov['patient_control_number'])
        if not h.get('rendering_provider_npi'):
            h['rendering_provider_npi'] = _s(prov['rendering_npi'])
    if '-X' in suffix:
        h['cob'] = 'crossover' if _short(payer) in ('SUP1', 'SUP2') else 'secondary'
    h['diagnosis_codes'] = dxs
    h['principal_diagnosis'] = dxs[0] if dxs else None
    # ---- lines
    out_lines = []
    src = None
    for loop in (sloop, vloop):
        if loop and lines.get((loop['_file'], loop['claim_key'])):
            src = [('x12_837_line', l) for l in lines[(loop['_file'], loop['claim_key'])]]
            break
    allowed_by_line = {}
    if fl:
        paid_ev = [e for e in fl if e['status'] == 'paid'] or fl
        for ln, l in paid_ev[-1]['lines'].items():
            allowed_by_line[ln] = _money(l['allowed'])
    if not src and fl:
        last = fl[-1]
        src = [('payers_medical', l) for l in last['lines'].values() if _s(l['procedure_code']) or _s(l['revenue_code'])]
        if src:
            rep['cross_check']['lines_from_flat'] += 1
    if not src and prov and lines.get((prov['_file'], prov['claim_key'])):
        src = [('x12_837_line(provider)', l) for l in lines[(prov['_file'], prov['claim_key'])]]
        rep['cross_check']['lines_from_provider_837'] += 1
    for n, (table, l) in enumerate(src or [], 1):
        if table == 'payers_medical':
            ln = _s(l['claim_line']) or str(n)
            out_lines.append({'line_number': _int(ln) or n, 'procedure_code': _s(l['procedure_code']),
                              'revenue_code': _s(l['revenue_code']), 'units': _num(l['units']),
                              'charge_amount': _money(l['billed']), 'allowed_amount': _money(l['allowed']),
                              'service_date': _d(l['from_date']), '_lin': [_lin(table, l)]})
        else:
            ln = _s(l['line_number']) or str(l['line_ordinal'] or n)
            out_lines.append({'line_number': _int(ln) or n, 'procedure_code': _s(l['procedure_code']),
                              'revenue_code': _s(l['revenue_code']), 'units': _num(l['units']),
                              'charge_amount': _money(l['charge']), 'allowed_amount': allowed_by_line.get(ln),
                              'service_date': _d(l['service_date']) or h.get('service_from'),
                              '_lin': [_lin('x12_837_line', l)]})
    for l in out_lines:
        l['procedure_code_system'] = _proc_system(l['procedure_code'])
    if out_lines:
        dates = [l['service_date'] for l in out_lines if l['service_date']]
        if dates and not h.get('service_from'):
            h['service_from'], h['service_to'] = min(dates), max(dates)
        if not h.get('place_of_service') and h.get('claim_type') == 'professional' and src and src[0][0] != 'payers_medical':
            h['place_of_service'] = _s(src[0][1].get('place_of_service'))
    # ---- events
    evlist = []
    statuses = []
    flat_events = {(e['paid_date'], e['status']): e for e in (fl or [])}
    used_flat = set()
    for e in sorted(evs, key=lambda e: (e['_paid_date'] or '', e['_file'], e['_row'])):
        code = _s(e['claim_status_code'])
        freq = _s(e['frequency_code']) or '1'
        paid = _money(e['paid_amount'])
        a = adj.get((e['_file'], e['claim_key']), [])
        if code == '22':
            status = 'voided' if freq == '8' else 'reversed'
        elif code == '4':
            status = 'denied_upheld' if 'denied' in statuses or 'denied_upheld' in statuses else 'denied'
        elif paid is not None and paid < 0:
            status = 'recouped'
        else:
            status = 'paid'
        # the flat row of this adjudication: compatible status, same paid amount, nearest paid date (the 835 date is
        # the weekly EFT date, the flat PAID_DATE the adjudication date a few days earlier)
        ok = {'paid': ('paid',), 'denied': ('denied', 'denied_upheld'), 'denied_upheld': ('denied_upheld', 'denied'),
              'reversed': ('reversed', 'recouped'), 'voided': ('voided',), 'recouped': ('recouped', 'reversed')}[status]
        f = None
        cands = []
        for k, x in flat_events.items():
            if k in used_flat or k[1] not in ok:
                continue
            gap = _days(x['paid_date'], e['_paid_date'])
            if k[1] in ('reversed', 'recouped', 'voided'):  # take-back rows keep the original PAID_DATE; the file is later
                g2 = _days(x['fd'], e['_paid_date'])
                gap = g2 if gap is None or (g2 is not None and abs(g2) < abs(gap)) else gap
            if gap is None or abs(gap) > 45:
                continue
            fpaid = _money(x['row']['paid'])
            cands.append((0 if fpaid is None or paid is None or abs(fpaid - paid) < 0.01 else 1, ok.index(k[1]), abs(gap),
                          k, x))
        if cands:
            f = min(cands, key=lambda c: c[:3])[4]
        if f is not None:
            used_flat.add((f['paid_date'], f['status']))
            if f['status'] != status:
                rep['cross_check'][f'status_from_flat:{status}->{f["status"]}'] += 1
            status = f['status']
            rep['cross_check']['835_event_matched_flat'] += 1
        elif fl:
            rep['cross_check']['835_event_without_flat_row'] += 1
        statuses.append(status)
        resp = _money(e['patient_responsibility'])
        allowed = None
        if f is not None:
            vals = [_num(l['allowed']) for l in f['lines'].values() if _num(l['allowed']) is not None]
            allowed = round(sum(vals), 2) if vals else None
        if allowed is None:
            sv = [_num(s['allowed_amount']) for s in svc.get((e['_file'], e['claim_key']), []) if _num(s['allowed_amount']) is not None]
            if sv:
                allowed = round(sum(sv), 2)
        if allowed is None:
            allowed = round((paid or 0) + (resp or 0), 2) if status in ('paid', 'reversed', 'recouped') else 0.0
        ev = {'adjudication_source': 'x12_835', 'claim_status': status, 'paid_amount': paid, 'allowed_amount': allowed,
              'patient_responsibility': resp, 'total_charge': _money(e['total_charge']), 'carc': _carc(a),
              'paid_date': f['paid_date'] if f is not None and f['paid_date'] else e['_paid_date'],
              'remit_date': e['_paid_date'], 'claim_frequency_code': freq, 'payer_icn': _s(e['payer_claim_control_number']),
              'claim_filing_indicator': _s(e['claim_filing_indicator']), 'remark_codes': _s(e['remark_codes']),
              '_available': e['_available'] or e['_paid_date'], '_order': len(evlist),
              '_key': ('835', _s(e['payer_claim_control_number']), code, paid),
              '_lin': [_lin('x12_835_claim', e)] + [_lin('x12_835_adjustment', x) for x in a] +
                      ([_lin('payers_medical', l) for l in f['lines'].values()] if f else [])}
        if _s(e['original_reference']) and not h.get('original_payer_claim_id'):
            h['original_payer_claim_id'] = _s(e['original_reference'])
        if not h.get('service_from') and _d(e['statement_from']):
            h['service_from'] = _d(e['statement_from'])
            h['service_to'] = _d(e['statement_to']) or h['service_from']
        if not h.get('ms_drg') and _s(e['drg_code']):
            h['ms_drg'] = _s(e['drg_code'])
        evlist.append(ev)
    # flat events the 835 never carried (or claims with no 835 at all)
    for k, f in sorted(flat_events.items(), key=lambda x: (x[0][0] or '', x[0][1])):
        if k in used_flat:
            continue
        r = f['row']
        vals = [_num(l['allowed']) for l in f['lines'].values() if _num(l['allowed']) is not None]
        billed = [_num(l['billed']) for l in f['lines'].values() if _num(l['billed']) is not None]
        rep['cross_check']['flat_event_not_in_835'] += 1
        evlist.append({'adjudication_source': 'flat_medical', 'claim_status': f['status'], 'paid_amount': _money(r['paid']),
                       'allowed_amount': round(sum(vals), 2) if vals else None,
                       'patient_responsibility': _money(r['member_liability']),
                       'total_charge': round(sum(billed), 2) if billed else None, 'carc': None, 'paid_date': f['paid_date'],
                       'claim_frequency_code': _s(r['frequency_code']) or '1', '_available': f['fd'] or f['paid_date'],
                       '_key': ('flat', f['paid_date'], f['status']),
                       '_lin': [_lin('payers_medical', l) for l in f['lines'].values()]})
    if vloop and not any(e['claim_status'] == 'voided' for e in evlist):
        rep['cross_check']['void_837_without_void_835'] += 1
    if not evlist:
        rep['cross_check']['claim_without_adjudication'] += 1
        evlist.append({'adjudication_source': 'x12_837', 'claim_status': 'pending', 'paid_amount': None,
                       'allowed_amount': None, 'patient_responsibility': None, 'total_charge': h.get('total_charge'),
                       'carc': None, 'paid_date': None, 'claim_frequency_code': _s((head or {}).get('frequency_code')) or '1',
                       '_available': h.get('claim_received_date'), '_key': ('pending',), '_lin': []})
    # same date: the payment or denial first, the take-back (reversal / void / recoupment) after it, as the
    # payer remits a reversal of the earlier payment beside the corrected payment; then file order
    # order by the remittance (835) date when there is one: the flat PAID_DATE of a void/recoupment repeats the original
    # payment's date, so it cannot order events
    evlist.sort(key=lambda e: (e.get('remit_date') or e['paid_date'] or '9999', 1 if e['claim_status'] in ('reversed', 'voided', 'recouped') else 0,
                               0 if e['adjudication_source'] == 'x12_835' else 1, e.get('_order', 0)))
    # a reversal/recoupment delivered before the payment it takes back (a late-period file defect) applies after that
    # payment: move it behind the first later payment of the same amount
    i = 0
    while i < len(evlist):
        e = evlist[i]
        amt = abs(e['paid_amount'] or 0)
        if e['claim_status'] in ('reversed', 'recouped') and amt and not any(
                x['claim_status'] == 'paid' and abs(x['paid_amount'] or 0) == amt for x in evlist[:i]):
            j = next((j for j in range(i + 1, len(evlist)) if evlist[j]['claim_status'] == 'paid'
                      and abs(evlist[j]['paid_amount'] or 0) == amt), None)
            if j is not None:
                evlist.insert(j, evlist.pop(i))
                rep['cross_check']['takeback_before_payment_reordered'] += 1
                continue
        i += 1
    if vloop:
        lin.append(_lin('x12_837_claim', vloop))
    prev = None
    for n, e in enumerate(evlist, 1):
        e['adjudication_sequence'] = n
        e['_rid'] = _rid('payer_medical_claims', cid, *e['_key'])
        e['_supersedes'] = prev
        prev = e['_rid']
    return {'header': h, 'events': evlist, 'lines': out_lines, '_lin': lin}


def _emit_claim(ctx, c):
    h, evs = c['header'], c['events']
    payer, member = h['payer_id'], h['member_id']
    if h.get('cob') == 'crossover':
        product = 'medigap'
    elif h.get('cob') == 'secondary':
        product = 'cob_secondary'
    else:
        product = h.get('product') or ctx.product(payer, member, h.get('service_from'))
    pid = ctx.person(payer, member)
    for e in evs:
        rec = _envelope(ctx, 'payer_medical_claims', e['_rid'], payer, member, e['paid_date'] or h.get('service_from'),
                        e['paid_date'] or h.get('service_from'), e['_available'], c['_lin'] + e['_lin'],
                        e.get('_supersedes'), person=pid)
        rec.update({
            'payer_claim_id': h['payer_claim_id'], 'member_id': member, 'payer_id': payer, 'product': product,
            'claim_type': h.get('claim_type'), 'type_of_bill': h.get('type_of_bill'),
            'place_of_service': h.get('place_of_service'), 'service_from': h.get('service_from'),
            'service_to': h.get('service_to'), 'diagnosis_codes': h.get('diagnosis_codes') or [],
            'principal_diagnosis': h.get('principal_diagnosis'), 'billing_provider_npi': h.get('billing_provider_npi'),
            'rendering_provider_npi': h.get('rendering_provider_npi'), 'network_status': h.get('network_status'),
            'claim_received_date': h.get('claim_received_date'),
            'total_charge': e['total_charge'] if e.get('total_charge') is not None else h.get('total_charge'),
            'adjudication_sequence': e['adjudication_sequence'], 'claim_status': e['claim_status'],
            'allowed_amount': e['allowed_amount'], 'paid_amount': e['paid_amount'],
            'patient_responsibility': e['patient_responsibility'], 'carc': e['carc'], 'paid_date': e['paid_date'],
            'claim_frequency_code': e['claim_frequency_code'], 'admission_date': h.get('admission_date'),
            'discharge_date': h.get('discharge_date'), 'ms_drg': h.get('ms_drg'),
            'discharge_status': h.get('discharge_status'), 'admission_type': h.get('admission_type'),
            'admission_source': h.get('admission_source'), 'patient_status': h.get('patient_status'),
            'discharge_date_source': h.get('discharge_date_source'),
            'source_billing_claim_id': h.get('source_billing_claim_id'),
            'original_payer_claim_id': h.get('original_payer_claim_id') if '-R' in _split_claim_id(h['payer_claim_id'])[1]
            or e['claim_frequency_code'] == '7' else None,
            'claim_filing_indicator': e.get('claim_filing_indicator') or h.get('claim_filing_indicator'),
            'cob': h.get('cob'), 'adjudication_source': e['adjudication_source'], 'payer_icn': e.get('payer_icn'),
            'remit_date': e.get('remit_date'),
            'cclf_claim_id': h.get('cclf_claim_id'), 'x12_paid_amount': e.get('x12_paid_amount'),
            'x12_allowed_amount': e.get('x12_allowed_amount')})
        yield ctx.emit(rec)
    last = evs[-1]
    for l in c['lines']:
        rid = _rid('payer_medical_claim_lines', h['payer_claim_id'], l['line_number'])
        rec = _envelope(ctx, 'payer_medical_claim_lines', rid, payer, member, last['paid_date'] or l['service_date'],
                        last['paid_date'] or l['service_date'], last['_available'], l['_lin'], person=pid)
        rec.update({'payer_claim_id': h['payer_claim_id'], 'line_number': l['line_number'],
                    'procedure_code': l['procedure_code'], 'procedure_code_system': l.get('procedure_code_system') or _proc_system(l['procedure_code']),
                    'revenue_code': l['revenue_code'], 'units': l['units'], 'charge_amount': l['charge_amount'],
                    'allowed_amount': l['allowed_amount'], 'service_date': l['service_date'],
                    'paid_amount': l.get('paid_amount')})
        yield ctx.emit(rec)


# ---- CCLF (Medicare FFS claims for the MSSP population)
def _tail(cntl):
    cntl = _s(cntl) or ''
    return cntl[8:18] if len(cntl) >= 18 else cntl


def _cclf_claims(ctx):
    rep = ctx.rep
    db = ctx.db
    claims = {}

    def dedupe(table, rows, key):
        rows = _keep_files(rep, table, rows)
        out, seen = [], set()
        for r in sorted(rows, key=lambda r: (_d(r.get('file_date')) or '', r['_file'], r['_row'])):
            k = key(r)
            if k in seen:
                rep['dropped']['repeat_cclf_row'] += 1
                continue
            seen.add(k)
            out.append(r)
        return out

    def num_id(v):
        v = _s(v)
        return str(int(v)) if v and v.isdigit() else v

    common = ['cur_clm_uniq_id', 'bene_mbi_id', 'clm_type_cd', 'clm_from_dt', 'clm_thru_dt', 'clm_adjsmt_type_cd',
              'clm_efctv_dt', 'clm_idr_ld_dt', 'clm_cntl_num', 'file_date']
    # Part A
    hdr = dedupe('cclf_parta_claims_header', _rows(db, 'cclf_parta_claims_header', common + [
        'prncpl_dgns_cd', 'clm_pmt_amt', 'bene_ptnt_stus_cd', 'dgns_drg_cd', 'clm_bill_fac_type_cd', 'clm_bill_clsfctn_cd',
        'clm_bill_freq_cd', 'clm_mdcr_instnl_tot_chrg_amt', 'fac_prvdr_npi_num', 'atndg_prvdr_npi_num',
        'clm_blg_prvdr_npi_num', 'clm_atndg_prvdr_npi_num', 'clm_org_cntl_num', 'clm_admsn_type_cd', 'clm_admsn_src_cd']),
        lambda r: (num_id(r['cur_clm_uniq_id']), _s(r['clm_adjsmt_type_cd'])))
    rev = dedupe('cclf_parta_claims_revenue_center_detail', _rows(db, 'cclf_parta_claims_revenue_center_detail', [
        'cur_clm_uniq_id', 'clm_line_num', 'clm_line_from_dt', 'clm_line_prod_rev_ctr_cd', 'clm_line_hcpcs_cd',
        'clm_line_srvc_unit_qty', 'clm_line_cvrd_pd_amt', 'file_date']),
        lambda r: (num_id(r['cur_clm_uniq_id']), num_id(r['clm_line_num'])))
    dgn = dedupe('cclf_parta_diagnosis_code', _rows(db, 'cclf_parta_diagnosis_code', [
        'cur_clm_uniq_id', 'clm_val_sqnc_num', 'clm_dgns_cd', 'file_date']),
        lambda r: (num_id(r['cur_clm_uniq_id']), num_id(r['clm_val_sqnc_num']), _s(r['clm_dgns_cd'])))
    revs, dgns = defaultdict(list), defaultdict(list)
    for r in rev:
        revs[num_id(r['cur_clm_uniq_id'])].append(r)
    for r in dgn:
        dgns[num_id(r['cur_clm_uniq_id'])].append(r)
    versions = defaultdict(list)  # chain key -> [(kind, rows...)]
    for r in hdr:
        adj = _s(r['clm_adjsmt_type_cd'])
        if adj not in ('0', '1', '2'):
            rep['dropped']['malformed_cclf_row'] += 1
            continue
        # original, cancellation and re-issue share the clm_cntl_num tail (chars 9-18), as in Part B
        chain = 'A-' + (_tail(_s(r['clm_org_cntl_num']) or _s(r['clm_cntl_num'])) or num_id(r['cur_clm_uniq_id']))
        versions[chain].append(('A', r, sorted(revs.get(num_id(r['cur_clm_uniq_id']), []),
                                               key=lambda x: _int(x['clm_line_num']) or 0),
                                sorted(dgns.get(num_id(r['cur_clm_uniq_id']), []),
                                       key=lambda x: _int(x['clm_val_sqnc_num']) or 0)))
    # Part B physician + DME (one claim per cur_clm_uniq_id; chain by clm_cntl_num tail)
    for table, extra in (('cclf_partb_physicians', ['clm_pos_cd', 'rndrg_prvdr_npi_num', 'clm_rndrg_prvdr_npi_num',
                                                    'clm_line_dgns_cd', 'clm_disp_cd'] +
                          [f'clm_dgns_{i}_cd' for i in range(1, 13)]),
                         ('cclf_partb_dme', ['clm_pos_cd', 'payto_prvdr_npi_num', 'clm_blg_prvdr_npi_num',
                                             'ordrg_prvdr_npi_num', 'clm_disp_cd'])):
        rows = dedupe(table, _rows(db, table, common + ['clm_line_num', 'clm_line_from_dt', 'clm_line_hcpcs_cd',
                                                        'clm_line_cvrd_pd_amt', 'clm_line_alowd_chrg_amt',
                                                        'clm_line_srvc_unit_qty'] + extra),
                      lambda r: (num_id(r['cur_clm_uniq_id']), num_id(r['clm_line_num']), _s(r['clm_adjsmt_type_cd'])))
        by_claim = defaultdict(list)
        for r in rows:
            if _s(r['clm_adjsmt_type_cd']) not in ('0', '1', '2'):
                rep['dropped']['malformed_cclf_row'] += 1
                continue
            by_claim[num_id(r['cur_clm_uniq_id'])].append(r)
        kind = 'B' if table == 'cclf_partb_physicians' else 'DME'
        for cur, rs in by_claim.items():
            rs.sort(key=lambda x: _int(x['clm_line_num']) or 0)
            versions[f'{kind}-' + (_tail(rs[0]['clm_cntl_num']) or cur)].append((kind, rs[0], rs, []))
    for chain, vs in versions.items():
        vs.sort(key=lambda v: (_d(v[1]['clm_idr_ld_dt']) or '', _d(v[1]['clm_efctv_dt']) or '',
                               _d(v[1]['file_date']) or '', v[1]['_file'], v[1]['_row']))
        claims[chain] = _cclf_claim(ctx, chain, vs)
    idx_b, idx_a = defaultdict(set), defaultdict(set)
    for chain, c in claims.items():
        if chain.startswith(('B-', 'DME-')):
            for l in c['lines']:
                idx_b[(c['header']['member_id'], l['procedure_code'], l['service_date'])].add(chain)
        elif chain.startswith('A-'):
            idx_a[(c['header']['member_id'], c['header']['service_from'], c['header']['service_to'])].add(chain)
    return {'claims': claims, 'idx_b': idx_b, 'idx_a': idx_a}


def _cclf_claim(ctx, chain, vs):
    kind, first, _, _ = vs[0]
    payer = 'WH-PAYER-MSSP'
    member = _s(first['bene_mbi_id'])
    h = {'payer_claim_id': 'CCLF-' + chain, 'member_id': member, 'payer_id': payer, 'cclf_claim_id': chain}
    events, lin = [], []
    cur_lines = []
    prev = None
    issued = False
    for n, (k, r, lines, dgns) in enumerate(vs, 1):
        adj = _s(r['clm_adjsmt_type_cd'])
        table = {'A': 'cclf_parta_claims_header', 'B': 'cclf_partb_physicians', 'DME': 'cclf_partb_dme'}[k]
        rows_lin = [_lin(table, r)] + ([_lin(table, x) for x in lines] if k != 'A' else
                                       [_lin('cclf_parta_claims_revenue_center_detail', x) for x in lines] +
                                       [_lin('cclf_parta_diagnosis_code', x) for x in dgns])
        if k == 'A':
            paid = _money(r['clm_pmt_amt'])
            allowed = None
            charge = _money(r['clm_mdcr_instnl_tot_chrg_amt'])
            dx = [_s(x['clm_dgns_cd']) for x in dgns if _s(x['clm_dgns_cd'])]
            pdx = _s(r['prncpl_dgns_cd']) or (dx[0] if dx else None)
            if pdx and pdx in dx:
                dx.remove(pdx)
            dx = ([pdx] if pdx else []) + dx
            h.update(claim_type='institutional', place_of_service=None,
                     type_of_bill='0' + ''.join(_s(r[c]) or '' for c in ('clm_bill_fac_type_cd', 'clm_bill_clsfctn_cd',
                                                                          'clm_bill_freq_cd')),
                     ms_drg=_s(r['dgns_drg_cd']), billing_provider_npi=_s(r['clm_blg_prvdr_npi_num']) or _s(r['fac_prvdr_npi_num']),
                     rendering_provider_npi=_s(r['atndg_prvdr_npi_num']) or _s(r['clm_atndg_prvdr_npi_num']),
                     discharge_status=DISCHARGE.get(_s(r['bene_ptnt_stus_cd']) or ''),
                     admission_type=_s(r['clm_admsn_type_cd']), admission_source=_s(r['clm_admsn_src_cd']))
            h['patient_status'] = _s(r['bene_ptnt_stus_cd'])
            if _s(r['clm_type_cd']) == '60':
                h['admission_date'], h['discharge_date'] = _d(r['clm_from_dt']), _d(r['clm_thru_dt'])
                h['discharge_date_source'] = 'cclf_clm_thru_dt'
            cur_lines = [{'line_number': _int(x['clm_line_num']) or i, 'procedure_code': _s(x['clm_line_hcpcs_cd']),
                          'revenue_code': _s(x['clm_line_prod_rev_ctr_cd']), 'units': _num(x['clm_line_srvc_unit_qty']),
                          'charge_amount': None, 'allowed_amount': None, 'paid_amount': _money(x['clm_line_cvrd_pd_amt']),
                          'service_date': _d(x['clm_line_from_dt']) or _d(r['clm_from_dt']),
                          '_lin': [_lin('cclf_parta_claims_revenue_center_detail', x)]} for i, x in enumerate(lines, 1)]
        else:
            pd = [_num(x['clm_line_cvrd_pd_amt']) for x in lines if _num(x['clm_line_cvrd_pd_amt']) is not None]
            al = [_num(x['clm_line_alowd_chrg_amt']) for x in lines if _num(x['clm_line_alowd_chrg_amt']) is not None]
            paid = round(sum(pd), 2) if pd else None
            allowed = round(sum(al), 2) if al else None
            charge = None
            dx = []
            if k == 'B':
                for x in lines:
                    for c in [f'clm_dgns_{i}_cd' for i in range(1, 13)] + ['clm_line_dgns_cd']:
                        v = _s(x.get(c))
                        if v and v not in dx:
                            dx.append(v)
            h.update(claim_type='professional', place_of_service=_s(r['clm_pos_cd']), type_of_bill=None,
                     billing_provider_npi=_s(r.get('clm_blg_prvdr_npi_num')) or _s(r.get('payto_prvdr_npi_num')),
                     rendering_provider_npi=_s(r.get('rndrg_prvdr_npi_num')) or _s(r.get('clm_rndrg_prvdr_npi_num')) or
                     _s(r.get('ordrg_prvdr_npi_num')))
            cur_lines = [{'line_number': _int(x['clm_line_num']) or i, 'procedure_code': _s(x['clm_line_hcpcs_cd']),
                          'revenue_code': None, 'units': _num(x['clm_line_srvc_unit_qty']), 'charge_amount': None,
                          'allowed_amount': _money(x['clm_line_alowd_chrg_amt']),
                          'paid_amount': _money(x['clm_line_cvrd_pd_amt']),
                          'service_date': _d(x['clm_line_from_dt']) or _d(r['clm_from_dt']), '_lin': [_lin(table, x)]}
                         for i, x in enumerate(lines, 1)]
        if dx:
            h['diagnosis_codes'], h['principal_diagnosis'] = dx, dx[0]
        h['service_from'], h['service_to'] = _d(r['clm_from_dt']), _d(r['clm_thru_dt'])
        if adj == '1':
            status, freq = 'reversed', '8'
        elif issued:
            status, freq = 'paid', '7'
        else:
            status, freq = 'paid', '1'
        issued = True
        resp = round(allowed - paid, 2) if allowed is not None and paid is not None and k != 'A' else None
        rid = _rid('payer_medical_claims', h['payer_claim_id'], 'cclf', _s(r['cur_clm_uniq_id']), adj)
        events.append({'adjudication_source': 'cclf', 'claim_status': status, 'paid_amount': paid, 'allowed_amount': allowed,
                       'patient_responsibility': resp, 'total_charge': charge, 'carc': None,
                       'paid_date': _d(r['clm_efctv_dt']) or _d(r['clm_idr_ld_dt']), 'claim_frequency_code': freq,
                       'adjudication_sequence': n, '_rid': rid, '_supersedes': prev,
                       '_available': _d(r['file_date']) or _file_date(r['_file']), '_lin': rows_lin,
                       'cclf_cur_clm_uniq_id': _s(r['cur_clm_uniq_id'])})
        prev = rid
    h.setdefault('diagnosis_codes', [])
    h.setdefault('principal_diagnosis', None)
    return {'header': h, 'events': events, 'lines': cur_lines, '_lin': lin, 'matched': False}


def _match_cclf(ctx, built, cclf):
    """A Medicare FFS X12 claim (WH-PAYER-MSSP, or the MBI members of WH-PAYER-0/1/2) == CCLF claim: keep the X12 claim
    (events, ids) and drop the CCLF copy. Professional/DME: MBI + HCPCS + line service date. Institutional: MBI +
    statement from/through, preferring the CCLF claim with the same bill type, billing NPI and total charge. MSSP claims
    take the CCLF allowed/paid amounts; payer 0/1/2 claims keep their own amounts and get the cclf_claim_id link."""
    rep = ctx.rep
    claims = cclf['claims']
    for cid, c in built.items():
        h = c['header']
        payer = _short(h.get('payer_id'))
        if payer not in ('MSSP', '0', '1', '2') or '-X' in _split_claim_id(cid)[1] or not MBI.match(h.get('member_id') or ''):
            continue
        hits = Counter()
        if h.get('claim_type') == 'institutional':
            for ch in cclf['idx_a'].get((h['member_id'], h.get('service_from'), h.get('service_to')), ()):
                ch_h = claims[ch]['header']
                hits[ch] += 1 + (ch_h.get('type_of_bill') == h.get('type_of_bill')) + \
                    (ch_h.get('billing_provider_npi') == h.get('billing_provider_npi')) + \
                    (claims[ch]['events'][0].get('total_charge') == h.get('total_charge'))
        else:
            for l in c['lines']:
                for ch in cclf['idx_b'].get((h['member_id'], l['procedure_code'], l['service_date']), ()):
                    hits[ch] += 1
        hits = Counter({ch: n for ch, n in hits.items() if not claims[ch]['matched']})
        if not hits:
            rep['cross_check'][f'medicare_ffs_x12_without_cclf_match:{payer}'] += 1
            continue
        ch = hits.most_common(1)[0][0]
        cc = claims[ch]
        cc['matched'] = True
        rep['dropped']['cclf_claim_matched_mssp_x12' if payer == 'MSSP' else 'cclf_claim_matched_payer_x12'] += 1
        h['cclf_claim_id'] = ch
        if payer != 'MSSP':
            c['_lin'] = c['_lin'] + cc['events'][-1]['_lin']
            continue
        cur = cc['events'][-1]
        c['_lin'] = c['_lin'] + cc['events'][-1]['_lin']
        for e in c['events']:
            if e['claim_status'] in ('paid', 'reversed', 'recouped'):
                sign = -1 if (e['paid_amount'] or 0) < 0 else 1
                e['x12_paid_amount'], e['x12_allowed_amount'] = e['paid_amount'], e['allowed_amount']
                if cur['paid_amount'] is not None:
                    e['paid_amount'] = round(sign * abs(cur['paid_amount']), 2)
                if cur['allowed_amount'] is not None:
                    e['allowed_amount'] = round(sign * abs(cur['allowed_amount']), 2)
                if cur['patient_responsibility'] is not None:
                    e['patient_responsibility'] = round(sign * abs(cur['patient_responsibility']), 2)
        by_key = defaultdict(list)
        for l in cc['lines']:
            by_key[(l['procedure_code'], l['service_date'])].append(l)
        for l in c['lines']:
            m = by_key.get((l['procedure_code'], l['service_date']))
            if m:
                cl = m.pop(0)
                l['allowed_amount'] = cl['allowed_amount'] if cl['allowed_amount'] is not None else l['allowed_amount']
                l['paid_amount'] = cl.get('paid_amount')
                l['_lin'] = l['_lin'] + cl['_lin']
        if not c['lines'] and cc['lines']:
            c['lines'] = cc['lines']
        if not h.get('diagnosis_codes') and cc['header'].get('diagnosis_codes'):
            h['diagnosis_codes'] = cc['header']['diagnosis_codes']
            h['principal_diagnosis'] = cc['header']['principal_diagnosis']
        for k in ('ms_drg', 'discharge_status', 'admission_date', 'discharge_date', 'discharge_date_source', 'type_of_bill',
                  'patient_status', 'admission_type', 'admission_source'):
            if not h.get(k) and cc['header'].get(k):
                h[k] = cc['header'][k]


# ======================================================================================================== pharmacy claims
def _pharmacy(ctx):
    rep = ctx.rep
    db = ctx.db
    rows = _rows(db, 'ncpdp_claim', [
        'claim_ref', 'file_date', 'bin_number', 'transaction_code', 'pcn', 'service_provider_id_qualifier',
        'service_provider_id', 'date_of_service', 'cardholder_id', 'group_id', 'rx_service_ref_number',
        'product_service_id_qualifier', 'product_service_id', 'quantity_dispensed', 'fill_number', 'days_supply', 'daw_code',
        'prescriber_id_qualifier', 'prescriber_id', 'ingredient_cost_submitted', 'dispensing_fee_submitted',
        'response_status_code', 'patient_pay_amount', 'total_amount_paid', 'ingredient_cost_paid', 'dispensing_fee_paid',
        'reject_codes', 'date_prescription_written'])
    rows = _keep_files(rep, 'ncpdp_claim', rows)
    flat = _flat_pharmacy(ctx)
    ndc_name = {}
    for cid, fr in flat['by_id'].items():
        for r in fr:
            if _s(r['ndc']) and _s(r['drug_name']):
                ndc_name.setdefault(r['ndc'].strip(), r['drug_name'].strip())
    pde = _cclf_partd(ctx)
    npi_type = pde['npi_type']
    seen = set()
    by_claim = defaultdict(list)
    for r in sorted(rows, key=lambda r: (_d(r['file_date']) or '', r['_file'], r['_row'] or 0)):
        cid = _s(r['claim_ref'])
        if not cid:
            rep['dropped']['malformed_ncpdp_row'] += 1
            continue
        key = (cid, _s(r['transaction_code']), _s(r['response_status_code']), _s(r['date_of_service']),
               r['fill_number'], _s(r['rx_service_ref_number']), _s(r['product_service_id']), r['total_amount_paid'])
        if key in seen:
            rep['dropped']['repeat_ncpdp_transaction'] += 1
            continue
        seen.add(key)
        by_claim[cid].append(r)
    emitted_ids = set()
    pending = []
    for cid, txs in by_claim.items():
        txs.sort(key=lambda r: (_s(r['date_of_service']) or '', 1 if _s(r['transaction_code']) == 'B2' else 0,
                                _d(r['file_date']) or '', r['_file'], r['_row'] or 0))
        base_m = RXC_ID.match(cid)
        fr = list(flat['by_id'].get(cid, []))
        used_flat = set()
        prev_rid, last_paid = None, None
        for t in txs:
            code = _s(t['transaction_code'])
            st = _s(t['response_status_code'])
            if code == 'B2':
                status = 'reversed'
            elif st == 'P':
                status = 'paid'
            elif st == 'R':
                status = 'rejected'
            elif st in ('D', 'Q', 'S'):
                status = 'duplicate'
            else:
                status = {'A': 'paid', 'C': 'captured'}.get(st or '', 'unknown')
            if status == 'unknown':  # truncated record without an AM21 response: the flat file says what happened
                f = next((x for x in fr if id(x) not in used_flat), None)
                if f is None:
                    rep['dropped']['ncpdp_without_response'] += 1
                    continue
                status = (_s(f['status']) or '').lower()
            else:
                f = next((x for x in fr if id(x) not in used_flat and (_s(x['status']) or '').lower() == status), None)
            if f is not None:
                used_flat.add(id(f))
            payer = _payer_id(t['_instance'])
            member = _s(t['cardholder_id'])
            dos = _d(t['date_of_service'])
            ndc = _s(t['product_service_id']) if (_s(t['product_service_id_qualifier']) or '03') == '03' else None
            plan_paid = _money(t['total_amount_paid'])
            patient_pay = _money(t['patient_pay_amount'])
            if plan_paid is None and f is not None:
                plan_paid = _money(f['plan_paid'])
            if patient_pay is None and f is not None:
                patient_pay = _money(f['member_paid'])
            if status == 'reversed':
                plan_paid = plan_paid if plan_paid is not None else 0.0
            drug = (_s(f['drug_name']) if f is not None else None) or next(
                (_s(x['drug_name']) for x in fr if _s(x['drug_name'])), None) or ndc_name.get(ndc or '')
            rid = _rid('payer_pharmacy_claims', cid, code, st, _s(t['date_of_service']), plan_paid)
            sup = None
            if status == 'reversed':
                sup = last_paid or prev_rid
            elif prev_rid and status in ('paid', 'rejected'):
                sup = prev_rid
            rec = {'rx_claim_id': cid, 'member_id': member, 'payer_id': payer, 'ndc': ndc, 'rxcui': None,
                   'drug_name': drug, 'days_supply': _int(t['days_supply']), 'quantity_dispensed': _num(t['quantity_dispensed']),
                   'fill_number': _int(t['fill_number']), 'daw_code': _s(t['daw_code']),
                   'pharmacy_npi': _s(t['service_provider_id']) if (_s(t['service_provider_id_qualifier']) or '01') == '01' else None,
                   'pharmacy_name': None, 'pharmacy_type': npi_type.get(_s(t['service_provider_id']) or ''),
                   'bin': _s(t['bin_number']), 'pcn': _s(t['pcn']), 'group_id': _s(t['group_id']),
                   'prescriber_npi': _s(t['prescriber_id']) if (_s(t['prescriber_id_qualifier']) or '01') == '01' else None,
                   'claim_status': status, 'reject_code': (_s(t['reject_codes']) or '').split(',')[0] or None
                   if status == 'rejected' else None, 'reject_codes': _s(t['reject_codes']),
                   'prescription_number': _s(t['rx_service_ref_number']), 'transaction_code': code,
                   'reversal_reason': None, 'plan_paid': plan_paid, 'patient_pay': patient_pay,
                   'ingredient_cost': _money(t['ingredient_cost_paid']) if _num(t['ingredient_cost_paid']) is not None
                   else _money(t['ingredient_cost_submitted']),
                   'dispensing_fee': _money(t['dispensing_fee_paid']) if _num(t['dispensing_fee_paid']) is not None
                   else _money(t['dispensing_fee_submitted']), 'formulary_tier': None,
                   'original_rx_claim_id': base_m.group('base') if base_m and base_m.group('suffix') else None,
                   'date_prescription_written': _d(t['date_prescription_written']), 'adjudication_source': 'ncpdp',
                   '_fill': dos, '_available': _d(t['file_date']) or _file_date(t['_file']), '_rid': rid, '_sup': sup,
                   '_lin': [_lin('ncpdp_claim', t)] + ([_lin('payers_pharmacy', f)] if f is not None else [])}
            if status == 'paid':
                last_paid = rid
            prev_rid = rid
            pending.append(rec)
        # flat events NCPDP never carried (recoupments, flat-only claims)
        for f in fr:
            if id(f) in used_flat:
                continue
            st = (_s(f['status']) or '').lower()
            if any((_s(x['status']) or '').lower() == st and id(x) in used_flat for x in fr) and st != 'recouped':
                continue
            rec = _flat_rx_record(ctx, cid, f, ndc_name, npi_type, prev_rid if st in ('recouped', 'reversed') else None)
            rep['cross_check'][f'flat_pharmacy_event_not_in_ncpdp:{st}'] += 1
            prev_rid = rec['_rid']
            pending.append(rec)
        emitted_ids.add(cid)
    for cid, fr in flat['by_id'].items():
        if cid in emitted_ids:
            continue
        rep['cross_check']['flat_pharmacy_claim_not_in_ncpdp'] += 1
        prev = None
        for f in sorted(fr, key=lambda x: (_d(x['fill_date']) or '', x['_file'], x['_row'])):
            rec = _flat_rx_record(ctx, cid, f, ndc_name, npi_type, prev)
            prev = rec['_rid']
            pending.append(rec)
    # CCLF7 (Part D PDE) for MSSP: match to MSSP NCPDP on MBI + Rx number + fill number
    # 5 MSSP cardholder ids arrive as the MBI plus one trailing character (12 chars); the key uses the 11-char MBI
    mssp_paid = {}
    for rec in sorted(pending, key=lambda r: 1 if r.get('original_rx_claim_id') else 0):
        if _short(rec['payer_id']) == 'MSSP' and rec['claim_status'] == 'paid':
            mssp_paid.setdefault((_mbi11(rec['member_id']), rec['prescription_number'], rec['fill_number']), rec)
    for key, chain in pde['chains'].items():
        hit = mssp_paid.get(key)
        if hit is not None:
            rep['dropped']['cclf_pde_matched_mssp_ncpdp'] += 1
            orig = chain[0]
            hit['cclf_claim_id'] = _s(orig['cur_clm_uniq_id'])
            hit['_lin'] = hit['_lin'] + [_lin('cclf_partd_claims', x) for x in chain]
            if hit.get('patient_pay') is None:
                hit['patient_pay'] = _money(orig['clm_line_bene_pmt_amt'])
            hit['pharmacy_type'] = hit['pharmacy_type'] or PHARMACY_TYPE.get(_s(orig['clm_phrmcy_srvc_type_cd']) or '')
            continue
        rep['cross_check']['cclf_pde_without_ncpdp'] += 1
        pending += _pde_records(ctx, key, chain, ndc_name)
    # Every event is kept. A reversal or recoupment names the payment it takes back (reverses_record_id) instead of
    # superseding it, and carries that payment's fill date (its own transaction date stays in transaction_date):
    # superseding would make staging delete the paid fill, and the reversal's own later date would replace the fill date.
    by_rid = {r['_rid']: r for r in pending}
    targets = {}
    for rec in pending:  # resolve every take-back to its payment first (chains may run through another take-back)
        if rec['claim_status'] in ('reversed', 'recouped') and rec.get('_sup'):
            tgt, seen = by_rid.get(rec['_sup']), set()
            while tgt is not None and tgt['claim_status'] != 'paid' and tgt.get('_sup') and tgt['_rid'] not in seen:
                seen.add(tgt['_rid'])
                tgt = by_rid.get(tgt['_sup'])
            targets[rec['_rid']] = tgt if tgt is not None and tgt['claim_status'] == 'paid' else None
    fills = {r['_rid']: r['_fill'] for r in pending}
    for rec in pending:
        rec['transaction_date'] = rec['_fill']
        if rec['_rid'] in targets:
            rec['_sup'] = None
            tgt = targets[rec['_rid']]
            if tgt is not None:
                rec['reverses_record_id'] = tgt['_rid']
                rec['_fill'] = fills[tgt['_rid']] or rec['_fill']
                ctx.rep['cross_check'][f'pharmacy_{rec["claim_status"]}_linked_to_paid_fill'] += 1
            else:
                ctx.rep['cross_check'][f'pharmacy_{rec["claim_status"]}_without_paid_fill'] += 1
    for rec in pending:
        yield ctx.emit(_rx_record(ctx, rec))


def _mbi11(v):
    v = _s(v) or ''
    return v[:11] if len(v) == 12 and identity.normalize_mbi(v[:11]) and not identity.normalize_mbi(v) else v


def _rx_record(ctx, rec):
    payer, member = rec['payer_id'], rec['member_id']
    env = _envelope(ctx, 'payer_pharmacy_claims', rec.pop('_rid'), payer, member, rec['_fill'], rec['transaction_date'],
                    rec.pop('_available'), rec.pop('_lin'), rec.pop('_sup'))
    rec['product'] = ctx.product(payer, member, rec['_fill'])
    rec['fill_date'] = rec.pop('_fill')
    env.update(rec)
    return env


def _flat_pharmacy(ctx):
    rows = _rows(ctx.db, 'payers_pharmacy', ['claim_id', 'member_id', 'fill_date', 'ndc', 'drug_name', 'quantity',
                                             'days_supply', 'fill_number', 'pharmacy_npi', 'prescriber_npi', 'rx_number',
                                             'status', 'plan_paid', 'member_paid', 'payer', 'file_variant'])
    rows = _keep_files(ctx.rep, 'payers_pharmacy', rows)
    by_id = defaultdict(list)
    seen = set()
    for r in sorted(rows, key=lambda r: (r['_file'], r['_row'])):
        cid = _s(r['claim_id'])
        st = (_s(r['status']) or '').lower()
        if not cid or st not in FLAT_RX_STATUS:
            ctx.rep['dropped']['malformed_flat_pharmacy_row'] += 1
            continue
        key = (cid, st, _s(r['fill_date']), _s(r['plan_paid']), _s(r['member_paid']))
        if key in seen:
            ctx.rep['dropped']['repeat_flat_pharmacy_row'] += 1
            continue
        seen.add(key)
        by_id[cid].append(r)
    return {'by_id': by_id}


def _flat_rx_record(ctx, cid, f, ndc_name, npi_type, sup):
    st = (_s(f['status']) or '').lower()
    payer = _payer_id(f['payer'] or f['_instance'])
    fill = _d(f['fill_date'])
    base_m = RXC_ID.match(cid)
    plan = _money(f['plan_paid'])
    return {'rx_claim_id': cid, 'member_id': _s(f['member_id']), 'payer_id': payer, 'ndc': _s(f['ndc']), 'rxcui': None,
            'drug_name': _s(f['drug_name']) or ndc_name.get(_s(f['ndc']) or ''), 'days_supply': _int(f['days_supply']),
            'quantity_dispensed': _num(f['quantity']), 'fill_number': _int(f['fill_number']), 'daw_code': None,
            'pharmacy_npi': _s(f['pharmacy_npi']), 'pharmacy_name': None,
            'pharmacy_type': npi_type.get(_s(f['pharmacy_npi']) or ''), 'bin': None, 'pcn': None, 'group_id': None,
            'prescriber_npi': _s(f['prescriber_npi']), 'claim_status': st, 'reject_code': None, 'reject_codes': None,
            'prescription_number': _s(f['rx_number']), 'transaction_code': 'B2' if st == 'reversed' else None,
            'reversal_reason': None, 'plan_paid': plan, 'patient_pay': _money(f['member_paid']), 'ingredient_cost': None,
            'dispensing_fee': None, 'formulary_tier': None,
            'original_rx_claim_id': base_m.group('base') if base_m and base_m.group('suffix') else None,
            'date_prescription_written': None, 'adjudication_source': 'flat_pharmacy', '_fill': fill,
            '_available': _file_date(f['_file']) or fill,
            '_rid': _rid('payer_pharmacy_claims', cid, 'flat', st, fill, plan), '_sup': sup,
            '_lin': [_lin('payers_pharmacy', f)]}


def _cclf_partd(ctx):
    rep = ctx.rep
    rows = _rows(ctx.db, 'cclf_partd_claims', [
        'cur_clm_uniq_id', 'bene_mbi_id', 'clm_line_ndc_cd', 'clm_line_from_dt', 'prvdr_srvc_id_qlfyr_cd',
        'clm_srvc_prvdr_gnrc_id_num', 'clm_line_srvc_unit_qty', 'clm_line_days_suply_qty', 'clm_line_bene_pmt_amt',
        'clm_adjsmt_type_cd', 'clm_efctv_dt', 'clm_idr_ld_dt', 'clm_line_rx_srvc_rfrnc_num', 'clm_line_rx_fill_num',
        'clm_phrmcy_srvc_type_cd', 'clm_prsbng_prvdr_gnrc_id_num', 'clm_daw_prod_slctn_cd', 'file_date'])
    rows = _keep_files(rep, 'cclf_partd_claims', rows)
    chains, seen, npi_type = defaultdict(list), set(), {}
    for r in sorted(rows, key=lambda r: (_d(r['file_date']) or '', r['_file'], r['_row'])):
        adj = _s(r['clm_adjsmt_type_cd'])
        if adj not in ('0', '1', '2') or not _s(r['bene_mbi_id']):
            rep['dropped']['malformed_cclf_row'] += 1
            continue
        k = (_s(r['cur_clm_uniq_id']), adj)
        if k in seen:
            rep['dropped']['repeat_cclf_row'] += 1
            continue
        seen.add(k)
        npi = _s(r['clm_srvc_prvdr_gnrc_id_num'])
        t = PHARMACY_TYPE.get(_s(r['clm_phrmcy_srvc_type_cd']) or '')
        if npi and t:
            npi_type.setdefault(npi, t)
        key = (_s(r['bene_mbi_id']), _s(r['clm_line_rx_srvc_rfrnc_num']), _int(r['clm_line_rx_fill_num']))
        chains[key].append(r)
    for key in chains:
        chains[key].sort(key=lambda r: (_d(r['clm_idr_ld_dt']) or '', r['_file'], r['_row']))
    return {'chains': chains, 'npi_type': npi_type}


def _pde_records(ctx, key, chain, ndc_name):
    mbi, rx, fill = key
    cid = f'CCLF-PDE-{mbi}-{rx}-{fill}'
    out, prev = [], None
    for r in chain:
        adj = _s(r['clm_adjsmt_type_cd'])
        status = 'reversed' if adj == '1' else 'paid'
        ndc = _s(r['clm_line_ndc_cd'])
        rid = _rid('payer_pharmacy_claims', cid, 'cclf', _s(r['cur_clm_uniq_id']), adj)
        out.append({'rx_claim_id': cid, 'member_id': mbi, 'payer_id': 'WH-PAYER-MSSP', 'ndc': ndc, 'rxcui': None,
                    'drug_name': ndc_name.get(ndc or ''), 'days_supply': _int(r['clm_line_days_suply_qty']),
                    'quantity_dispensed': _num(r['clm_line_srvc_unit_qty']), 'fill_number': fill,
                    'daw_code': _s(r['clm_daw_prod_slctn_cd']),
                    'pharmacy_npi': _s(r['clm_srvc_prvdr_gnrc_id_num']) if (_s(r['prvdr_srvc_id_qlfyr_cd']) or '01') == '01' else None,
                    'pharmacy_name': None, 'pharmacy_type': PHARMACY_TYPE.get(_s(r['clm_phrmcy_srvc_type_cd']) or ''),
                    'bin': None, 'pcn': None, 'group_id': None, 'prescriber_npi': _s(r['clm_prsbng_prvdr_gnrc_id_num']),
                    'claim_status': status, 'reject_code': None, 'reject_codes': None, 'prescription_number': rx,
                    'transaction_code': 'B2' if status == 'reversed' else None, 'reversal_reason': None,
                    'plan_paid': None, 'patient_pay': _money(r['clm_line_bene_pmt_amt']), 'ingredient_cost': None,
                    'dispensing_fee': None, 'formulary_tier': None, 'original_rx_claim_id': None,
                    'date_prescription_written': None, 'adjudication_source': 'cclf_partd',
                    'cclf_claim_id': _s(r['cur_clm_uniq_id']), '_fill': _d(r['clm_line_from_dt']),
                    '_available': _d(r['file_date']) or _file_date(r['_file']), '_rid': rid, '_sup': prev,
                    '_lin': [_lin('cclf_partd_claims', r)]})
        prev = rid
    return out


# ================================================================================================================== SDOH
SDOH_FLOAT = ('svi_overall_percentile', 'svi_socioeconomic_percentile', 'svi_household_characteristics_percentile',
              'svi_minority_language_percentile', 'svi_housing_transportation_percentile')
SDOH_INT = ('match_confidence', 'est_household_size', 'length_of_residence_years', 'est_vehicle_count',
            'broadband_access_score', 'financial_stress_score', 'social_isolation_score', 'transportation_risk_score',
            'housing_instability_score', 'adi_natrank', 'adi_staterank')
SDOH_BOOL = ('presence_of_children', 'food_access_low_flag')
SDOH_TEXT = ('vendor_name', 'member_match_status', 'county_fips', 'est_household_income_band', 'homeowner_status',
             'est_education_level', 'health_literacy_estimate', 'language_preference_estimate')


def _bool(v):
    v = (_s(v) or '').lower()
    return True if v in ('true', '1', 'y', 'yes') else False if v in ('false', '0', 'n', 'no') else None


def _sdoh(ctx):
    rep = ctx.rep
    rows = _rows(ctx.db, 'sdoh_vendor', ['member_id', 'delivery_date', 'file_variant'] + list(SDOH_FLOAT + SDOH_INT +
                                                                                            SDOH_BOOL + SDOH_TEXT))
    rows = _keep_files(rep, 'sdoh_vendor', rows)
    seen = set()
    for r in sorted(rows, key=lambda r: (r['_file'], r['_row'])):
        member = _s(r['member_id'])
        delivery = _d(r['delivery_date']) or _file_date(r['_file'])
        if not member:
            rep['dropped']['malformed_sdoh_row'] += 1
            continue
        if (member, delivery) in seen:
            rep['dropped']['repeat_sdoh_row'] += 1
            continue
        seen.add((member, delivery))
        payers = sorted(ctx.member_payers.get(member, ())) or [None]
        key = identity.sdoh_key(member)  # SDOH rows carry no payer: identity links member:sdoh_vendor:<id>
        pid = ctx.resolve(key) if key else None
        rid = _rid('sdoh_vendor_attributes', member, delivery)
        rec = _envelope(ctx, 'sdoh_vendor_attributes', rid, payers[0], member, delivery, delivery,
                        _file_date(r['_file']) or delivery, [_lin('sdoh_vendor', r)], source_system='WHN-SDOH-VENDOR',
                        person=pid)
        rec['delivery_date'] = delivery
        for k in SDOH_FLOAT:
            rec[k] = _num(r[k])
        for k in SDOH_INT:
            rec[k] = _int(r[k])
        for k in SDOH_BOOL:
            rec[k] = _bool(r[k])
        for k in SDOH_TEXT:
            rec[k] = _s(r[k])
        rec['member_id'] = member
        yield ctx.emit(rec)


# ================================================================================ provider revenue cycle (practice X12)
def _cents(v):
    n = _num(v)
    return None if n is None else int(round(n * 100))


def _practice(path):
    parts = (path or '').split('/')
    return parts[2] if len(parts) > 3 and parts[0] == 'x12' else None


def _provider(ctx):
    """The practice's own claim submissions (x12/837p) and remittances (x12/835): medical_claim_submissions,
    medical_claim_lines, medical_claims (one per adjudication) and provider_remittance_advice."""
    rep = ctx.rep
    db = ctx.db
    c837 = _rows(db, 'x12_837_claim', [
        'isa_control_number', 'claim_key', 'transaction_type', 'patient_control_number', 'subscriber_member_id',
        'patient_member_id', 'payer_id', 'payer_name', 'total_charge', 'facility_code', 'place_of_service',
        'frequency_code', 'statement_from', 'statement_to', 'admission_date', 'discharge_date', 'drg',
        'billing_provider_npi', 'rendering_npi', 'attending_npi', 'interchange_date'], "WHERE _feed = 'x12'")
    # _medical already counted the provider files' resend/ISA drops; count them here only in dropped_by_table
    tmp = _new_report()
    c837 = _isa_filter(tmp, 'provider_streams:x12_837_claim', _keep_files(tmp, 'provider_streams:x12_837_claim', c837))
    files = {r['_file'] for r in c837}
    dx, lines = defaultdict(list), defaultdict(list)
    for r in _rows(db, 'x12_837_dx', ['claim_key', 'dx_sequence', 'dx_code'], "WHERE _feed = 'x12'"):
        if r['_file'] in files:
            dx[(r['_file'], r['claim_key'])].append(r)
    for r in _rows(db, 'x12_837_line', ['claim_key', 'line_number', 'line_ordinal', 'procedure_code', 'revenue_code',
                                        'charge', 'units', 'service_date', 'rendering_npi'], "WHERE _feed = 'x12'"):
        if r['_file'] in files:
            lines[(r['_file'], r['claim_key'])].append(r)
    subs = defaultdict(list)
    seen = set()
    for r in sorted(c837, key=lambda r: (_d(r['interchange_date']) or '', r['_file'], r['_row'])):
        cid = _s(r['patient_control_number'])
        if not cid:
            continue
        freq = _s(r['frequency_code']) or '1'
        key = (cid, freq, r['total_charge'], tuple((l['procedure_code'], l['service_date'])
                                                   for l in lines.get((r['_file'], r['claim_key']), [])))
        if key in seen:
            rep['dropped']['repeat_provider_837_loop'] += 1
            continue
        seen.add(key)
        subs[cid].append(r)
    sub_rec = {}   # claim id -> [submission records]
    first_code = {}  # submission record id -> (first line code, system) for the adjudicated claim's service_code
    for cid, loops in subs.items():
        prev_orig = None
        recs = []
        for r in loops:
            freq = _s(r['frequency_code']) or '1'
            kind = {'7': 'corrected', '8': 'void'}.get(freq, 'original')
            practice = _practice(r['_file'])
            payer = _payer_id(r['payer_id'] or r['payer_name'])
            member = _s(r['subscriber_member_id']) or _s(r['patient_member_id'])
            ls = sorted(lines.get((r['_file'], r['claim_key']), []), key=lambda l: l['line_ordinal'] or 0)
            dxs = [d['dx_code'].strip() for d in sorted(dx.get((r['_file'], r['claim_key']), []),
                                                        key=lambda d: d['dx_sequence'] or 0) if _s(d['dx_code'])]
            dates = sorted(_d(l['service_date']) for l in ls if _d(l['service_date']))
            inst = (r['transaction_type'] or '') == '837I'
            sent = _d(r['interchange_date']) or _file_date(r['_file'])
            rid = _rid('medical_claim_submissions', practice, cid, freq, r['isa_control_number'])
            rec = _envelope(ctx, 'medical_claim_submissions', rid, payer, member, sent, sent, sent,
                            [_lin('x12_837_claim', r)] + [_lin('x12_837_dx', d) for d in dx.get((r['_file'], r['claim_key']), [])],
                            source_system=practice)
            rec.update({
                'claim_id': cid, 'service_organization_id': practice,
                'service_date': dates[0] if dates else _d(r['statement_from']),
                'claim_type': 'institutional' if inst else 'professional', 'service_code_system': 'CPT/HCPCS',
                'billed_cents': _cents(r['total_charge']), 'currency': 'USD', 'submission_kind': kind,
                'original_submission_record_id': prev_orig if kind != 'original' else None,
                'member_identifier_submitted': member, 'status': 'submitted', 'diagnosis_codes': dxs,
                'principal_diagnosis': dxs[0] if dxs else None, 'billing_provider_npi': _s(r['billing_provider_npi']),
                'rendering_provider_npi': _s(r['rendering_npi']) or _s(r['attending_npi']),
                'place_of_service': None if inst else (_s(r['place_of_service']) or _s(r['facility_code'])),
                'payer_organization_id': payer, 'claim_frequency_code': freq,
                'payer_claim_id': 'WHN-CLM-' + cid if not cid.startswith('WHN-') else cid})
            if inst:
                rec.update(type_of_bill='0' + (_s(r['facility_code']) or '') + freq, admission_date=_d(r['admission_date']),
                           discharge_date=_d(r['discharge_date']), ms_drg=_s(r['drg']))
            if kind == 'original' and prev_orig is None:
                prev_orig = rid
            rec['_lines'] = ls
            code0 = _s(ls[0]['procedure_code']) if ls else None
            first_code[rid] = (code0, _proc_system(code0))
            recs.append(rec)
        sub_rec[cid] = recs
    for cid, recs in sub_rec.items():
        for rec in recs:
            ls = rec.pop('_lines')
            yield ctx.emit(rec)
        cur = recs[-1]
        for n, l in enumerate(ls, 1):
            code = _s(l['procedure_code'])
            ln = _int(l['line_number']) or l['line_ordinal'] or n
            rid = _rid('medical_claim_lines', cur['service_organization_id'], cid, ln)
            rec = _envelope(ctx, 'medical_claim_lines', rid, cur['payer_organization_id'], cur['member_identifier_submitted'],
                            cur['event_date'], cur['event_date'], cur['available_date'], [_lin('x12_837_line', l)],
                            source_system=cur['service_organization_id'], person=cur['patient_id'])
            rec.update({'claim_id': cid, 'submission_record_id': cur['record_id'], 'line_number': ln,
                        'service_date': _d(l['service_date']) or cur['service_date'],
                        'service_organization_id': cur['service_organization_id'], 'service_code_system': _proc_system(code),
                        'service_code': code, 'units': _int(l['units']) if _num(l['units']) == _int(l['units']) else _num(l['units']),
                        'charge_cents': _cents(l['charge']), 'revenue_code': _s(l['revenue_code']),
                        'rendering_provider_npi': _s(l['rendering_npi'])})
            yield ctx.emit(rec)
        superseded = sum(len(lines.get((r['_file'], r['claim_key']), [])) for r in subs[cid][:-1])
        if superseded:
            rep['dropped']['lines_of_superseded_provider_submission'] += superseded
    # ---- remittances
    c835 = _rows(db, 'x12_835_claim', [
        'isa_control_number', 'st_control_number', 'claim_key', 'patient_control_number', 'claim_status_code',
        'total_charge', 'paid_amount', 'patient_responsibility', 'payer_claim_control_number', 'frequency_code',
        'patient_id', 'patient_id_qualifier', 'insured_id', 'payer_name', 'payee_npi', 'remark_codes'], "WHERE _feed = 'x12'")
    c835 = _isa_filter(tmp, 'provider_streams:x12_835_claim', _keep_files(tmp, 'provider_streams:x12_835_claim', c835))
    rep['dropped_by_table'].update(tmp['dropped_by_table'])
    f835 = {r['_file'] for r in c835}
    adj = defaultdict(list)
    for r in _rows(db, 'x12_835_adjustment', ['claim_key', 'level', 'cas_sequence', 'group_code', 'reason_code', 'amount'],
                   "WHERE _feed = 'x12'"):
        if r['_file'] in f835:
            adj[(r['_file'], r['claim_key'])].append(r)
    pay = {}
    for r in _rows(db, 'x12_835_payment', ['isa_control_number', 'st_control_number', 'effective_date', 'interchange_date',
                                           'interchange_sender_id'], "WHERE _feed = 'x12'"):
        pay[(r['_file'], r['isa_control_number'], r['st_control_number'])] = (_d(r['effective_date']), _d(r['interchange_date']))
    events = defaultdict(list)
    seen = set()
    for r in c835:
        cid = _s(r['patient_control_number'])
        if not cid:
            continue
        eff, idate = pay.get((r['_file'], r['isa_control_number'], r['st_control_number']), (None, None))
        r['_date'] = eff or idate or _file_date(r['_file'])
        r['_available'] = idate or r['_date']
        key = (cid, _s(r['claim_status_code']), r['paid_amount'], r['patient_responsibility'], r['_date'])
        if key in seen:
            rep['dropped']['repeat_provider_835_event'] += 1
            continue
        seen.add(key)
        events[cid].append(r)
    for cid, evs in events.items():
        evs.sort(key=lambda r: (r['_date'] or '', 1 if _s(r['claim_status_code']) == '22' else 0, r['_file'], r['_row']))
        recs = sub_rec.get(cid) or []
        last_paid = None
        for e in evs:
            code = _s(e['claim_status_code'])
            status = 'reversed' if code == '22' else 'denied' if code == '4' else 'paid'
            practice = _practice(e['_file'])
            sub = next((x for x in reversed(recs) if (x['event_date'] or '') <= (e['_date'] or '9999')), recs[-1] if recs else None)
            prefix = re.match(r'^([A-Z0-9]+)_835_', (e['_file'] or '').rsplit('/', 1)[-1])
            payer = _payer_id(e['payer_name']) or (sub or {}).get('payer_organization_id') or \
                (_payer_id(prefix.group(1)) if prefix else None)
            q = _s(e['patient_id_qualifier'])
            member = _s(e['patient_id']) or (q if q and len(q) > 3 else None) or _s(e['insured_id'])
            a = adj.get((e['_file'], e['claim_key']), [])
            adjustments, cat = [], Counter()
            for x in sorted(a, key=lambda x: x['cas_sequence'] or 0):
                g, rc, amt = _s(x['group_code']), _s(x['reason_code']), _cents(x['amount'])
                item = {'group': g, 'reason_code': rc, 'amount_cents': amt}
                if _s(e['remark_codes']) and g == 'CO' and rc not in ROUTINE_CARC:
                    item['remark_codes'] = [c.split(':')[-1] for c in e['remark_codes'].split(',') if c]
                adjustments.append(item)
                cat[(g, rc)] += amt or 0
            charge, paid, resp = _cents(e['total_charge']), _cents(e['paid_amount']) or 0, _cents(e['patient_responsibility']) or 0
            sign = -1 if status == 'reversed' else 1
            allowed = 0 if status == 'denied' else abs(paid) + abs(resp)
            if status == 'denied':
                deltas = dict.fromkeys(('billed', 'allowed', 'payer', 'responsibility', 'contractual', 'deductible', 'copay',
                                        'coinsurance'), 0)
            else:
                deltas = {'billed': sign * abs(charge or 0), 'allowed': sign * allowed, 'payer': sign * abs(paid),
                          'responsibility': sign * abs(resp), 'contractual': sign * abs(cat[('CO', '45')]),
                          'deductible': sign * abs(cat[('PR', '1')]), 'copay': sign * abs(cat[('PR', '3')]),
                          'coinsurance': sign * abs(cat[('PR', '2')])}
            rid = _rid('medical_claims', practice, cid, code, e['paid_amount'], e['_date'])
            lin = [_lin('x12_835_claim', e)] + [_lin('x12_835_adjustment', x) for x in a]
            rec = _envelope(ctx, 'medical_claims', rid, payer, member, e['_date'], e['_date'], e['_available'], lin,
                            source_system=payer)
            rec.update({
                'claim_id': cid, 'submission_record_id': sub['record_id'] if sub else None,
                'service_organization_id': practice,
                'service_date': sub['service_date'] if sub else None,
                'service_code': None, 'service_code_system': None, 'status': status,
                'billed_cents': abs(charge) if charge is not None else None, 'allowed_cents': allowed,
                'payer_paid_cents': abs(paid), 'patient_responsibility_cents': abs(resp),
                'original_adjudication_record_id': last_paid if status == 'reversed' else None,
                'adjustments': adjustments, 'financial_deltas_cents': deltas,
                'carc': _carc(a), 'payer_claim_id': _s(e['payer_claim_control_number']),
                'claim_frequency_code': _s(e['frequency_code']),
                'benefit_year': int(sub['service_date'][:4]) if sub and sub.get('service_date') else None})
            if sub:
                for k in ('diagnosis_codes', 'principal_diagnosis', 'billing_provider_npi', 'rendering_provider_npi',
                          'place_of_service', 'type_of_bill', 'admission_date', 'discharge_date', 'ms_drg'):
                    if sub.get(k) is not None:
                        rec[k] = sub[k]
                rec['service_code'], rec['service_code_system'] = first_code.get(sub['record_id'], (None, None))
            if status == 'paid':
                last_paid = rid
            yield ctx.emit(rec)
            pra = _envelope(ctx, 'provider_remittance_advice', _rid('provider_remittance_advice', practice, cid, code,
                                                                   e['paid_amount'], e['_date']),
                            payer, member, e['_date'], e['_date'], e['_available'], lin, source_system=practice,
                            person=rec['patient_id'])
            carc = _carc(a)
            pra.update({'claim_id': cid, 'original_adjudication_record_id': rid, 'payer_organization_id': payer,
                        'status': ('denied_information_needed' if carc == 'CO-16' else 'denied') if status == 'denied'
                        else 'adjudicated', 'patient_responsibility_cents': sign * abs(resp) if status != 'denied' else 0,
                        'payer_paid_cents': sign * abs(paid), 'carc': carc, 'adjudication_status': status})
            yield ctx.emit(pra)
        if not recs:
            rep['cross_check']['provider_835_without_provider_837'] += 1


# ================================================================================================================ deaths
def _deaths(ctx):
    """death_records from the only delivered death source: CCLF8 bene_death_dt (MSSP beneficiaries) and ALR 1-1
    bene_death_dt when sent. One record per MBI (the person resolves through mbi:<MBI>); event/recorded/available date =
    the first delivery that reported the death. 834 and flat eligibility carry no death date; EHR death columns hold
    column-shifted junk and stay with the clinical adapter."""
    rep = ctx.rep
    found = {}
    for table, cols in (('cclf_beneficiary_demographics', ['bene_mbi_id', 'bene_death_dt', 'file_date']),
                        ('alr_aalr1_assigned_beneficiaries', ['bene_mbi_id', 'bene_death_dt'])):
        rows = _keep_files(rep, table, _rows(ctx.db, table, cols))
        for r in sorted(rows, key=lambda r: (_d(r.get('file_date')) or _file_date(r['_file']) or '', r['_file'], r['_row'])):
            mbi, dod = _s(r['bene_mbi_id']), _d(r['bene_death_dt'])
            if not mbi or not dod:
                continue
            fd = _d(r.get('file_date')) or _file_date(r['_file']) or dod
            d = found.setdefault(mbi, {'dod': dod, 'first': fd, 'lin': [], 'dates': set()})
            d['dod'] = dod  # latest delivered value
            d['dates'].add(dod)
            d['lin'].append(_lin(table, r))
    # an MBI re-issue reports the same death under both MBIs: one record per person (the later-reported MBI wins)
    best = {}
    for mbi, d in found.items():
        key = identity.mbi_key(mbi)
        d['pid'] = ctx.resolve(key) if key else None
        k = d['pid'] or mbi
        if k in best:
            rep['dropped']['death_reported_under_second_mbi'] += 1
            if (d['first'], mbi) < (best[k][1]['first'], best[k][0]):
                best[k][1]['lin'] += d['lin']
                continue
            d['lin'] += best[k][1]['lin']
        best[k] = (mbi, d)
    for mbi, d in sorted(best.values()):
        if len(d['dates']) > 1:
            rep['cross_check']['death_date_changed_between_deliveries'] += 1
        pid = d['pid']
        rec = _envelope(ctx, 'death_records', _rid('death_records', mbi), 'WH-PAYER-MSSP', mbi, d['first'], d['first'],
                        d['first'], d['lin'][:3] + d['lin'][-1:], person=pid)
        rec.update({'date_of_death': d['dod'], 'place_of_death': None, 'underlying_cause_icd10': None,
                    'source': 'CMS CCLF8 beneficiary demographics (bene_death_dt)', 'member_id': mbi})
        yield ctx.emit(rec)
