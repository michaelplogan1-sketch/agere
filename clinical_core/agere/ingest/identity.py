"""Person identity resolution for native-feed staging: landing tables -> one person per human.

    build(db) -> report dict

Writes (drops and recreates) in the given connection:
    stg_person_map(source_key TEXT PRIMARY KEY, person_id TEXT, tier INTEGER, evidence TEXT)
    stg_person(person_id TEXT PRIMARY KEY, payload TEXT)   -- JSON, canonical patient fields + identity evidence

Source keys (canonical.py conventions; every adapter resolves people through these):
    epic:<instance>:<pat_id>          instance = Epic folder, e.g. EPIC-DEEPCHART
    athena:<contextid>:<patientid>
    ecw:<practice>:<pid>              practice = eCW folder, e.g. WHN-PRACTICE-AK-0
    fhir:<practice folder>:<Patient.id>  folder = last folder name (ehr/fhir/X and fhir_bulk/X both -> X; DEEPCHART)
    mrn:<practice>:<mrn>              practice from the MRN itself (ICE-CA-5-.. -> WHN-PRACTICE-CA-5, CE-CA-11-..,
                                      16-hex -> DEEPCHART) else from the issuing context (Epic identity type name,
                                      FHIR folder, ORU authority PRACCA8 -> WHN-PRACTICE-CA-8); see mrn_key()
    mbi:<MBI>
    member:<payer>:<member_id>        payer = full payer folder id, e.g. WH-PAYER-MCD2 (see payer_id()); a member's
                                      own subscriber id is registered as member:<payer>:<subscriber_id> too.
                                      SDOH vendor ids (no payer in the file) are member:sdoh_vendor:<id>, linked to
                                      the mbi:/member: key carrying the same id value.
    hl7:<assigning authority>:<PID-3.1>

Method (docs/agere/NATIVE_FEED_INVENTORY.md 1.4), union-find over source keys:
    tier 0  same source key (all tables), a member's own subscriber id, SDOH id = payer member id
    tier 1  MRN within a practice: Epic identity_id, athena enterpriseid, eCW hl7Id, FHIR identifier MR, ORU PID-3
            (FHIR Patient.id bridges old/new MRN at EHR migrations; deep-chart pat_ids carry two MRNs)
    tier 2  MBI: CMS feeds, 834 MEDICARE_FFS loops, MSSP X12/NCPDP/care gaps, MBI re-issue (contiguous CCLF8 spans
            + identical demographics, corroborated by CCLF9 when present)
    tier 3  demographic key lower(last) + first token of lower(first) + DOB, merged only when sex or ZIP5 also agrees,
            DOB plausible (1900-01-01 .. as_of), never merging two different keys of the same source namespace whose
            sex conflicts. Sex-agreeing merges are applied before ZIP-only merges.
Each key's tier is the weakest link on its strongest path to the person's anchor key; evidence names that link.
Person id = 'AGP-' + sha256(anchor)[:12], anchor = the cluster's smallest stable key (rank epic, athena, ecw, fhir,
mbi, member, hl7, mrn, sdoh; then lexicographic), so ids are deterministic across runs.
A cluster without any name+DOB observation is unresolved: its keys map to person_id NULL.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import datetime as _dt
import hashlib
import heapq
import json
import re
import sqlite3
import time
import unicodedata

AS_OF = '2030-05-27'
MIN_DOB = '1900-01-01'

KIND_RANK = {'epic': 0, 'athena': 1, 'ecw': 2, 'fhir': 3, 'mbi': 4, 'member': 5, 'hl7': 6, 'mrn': 7}
EHR_KINDS = {'epic', 'athena', 'ecw', 'fhir', 'mrn', 'hl7'}
SDOH_NS = 'member:sdoh_vendor'

# demographic source priority (higher wins when choosing payload values)
PRI = {'epic_deep': 9, 'ehr': 8, 'cclf': 7, 'eligibility': 6, '834': 6, 'alr': 5, 'hl7': 4, 'sdoh': 3,
       'care_gap': 3, '837': 2, 'risk': 1, 'ncpdp': 1}

FIPS_STATE = {
    '01': 'AL', '02': 'AK', '04': 'AZ', '05': 'AR', '06': 'CA', '08': 'CO', '09': 'CT', '10': 'DE', '11': 'DC',
    '12': 'FL', '13': 'GA', '15': 'HI', '16': 'ID', '17': 'IL', '18': 'IN', '19': 'IA', '20': 'KS', '21': 'KY',
    '22': 'LA', '23': 'ME', '24': 'MD', '25': 'MA', '26': 'MI', '27': 'MN', '28': 'MS', '29': 'MO', '30': 'MT',
    '31': 'NE', '32': 'NV', '33': 'NH', '34': 'NJ', '35': 'NM', '36': 'NY', '37': 'NC', '38': 'ND', '39': 'OH',
    '40': 'OK', '41': 'OR', '42': 'PA', '44': 'RI', '45': 'SC', '46': 'SD', '47': 'TN', '48': 'TX', '49': 'UT',
    '50': 'VT', '51': 'VA', '53': 'WA', '54': 'WV', '55': 'WI', '56': 'WY', '72': 'PR'}
STATE_NAMES = {'alabama': 'AL', 'alaska': 'AK', 'arizona': 'AZ', 'arkansas': 'AR', 'california': 'CA',
               'colorado': 'CO', 'connecticut': 'CT', 'delaware': 'DE', 'district of columbia': 'DC',
               'florida': 'FL', 'georgia': 'GA', 'hawaii': 'HI', 'idaho': 'ID', 'illinois': 'IL', 'indiana': 'IN',
               'iowa': 'IA', 'kansas': 'KS', 'kentucky': 'KY', 'louisiana': 'LA', 'maine': 'ME', 'maryland': 'MD',
               'massachusetts': 'MA', 'michigan': 'MI', 'minnesota': 'MN', 'mississippi': 'MS', 'missouri': 'MO',
               'montana': 'MT', 'nebraska': 'NE', 'nevada': 'NV', 'new hampshire': 'NH', 'new jersey': 'NJ',
               'new mexico': 'NM', 'new york': 'NY', 'north carolina': 'NC', 'north dakota': 'ND', 'ohio': 'OH',
               'oklahoma': 'OK', 'oregon': 'OR', 'pennsylvania': 'PA', 'rhode island': 'RI',
               'south carolina': 'SC', 'south dakota': 'SD', 'tennessee': 'TN', 'texas': 'TX', 'utah': 'UT',
               'vermont': 'VT', 'virginia': 'VA', 'washington': 'WA', 'west virginia': 'WV', 'wisconsin': 'WI',
               'wyoming': 'WY', 'puerto rico': 'PR'}
CCLF_RACE = {'0': None, '1': 'White', '2': 'Black or African American', '3': 'Other', '4': 'Asian',
             '5': 'Hispanic', '6': 'American Indian or Alaska Native'}
EPIC_RACE_DEFAULT = {'1': 'White', '2': 'Black or African American', '3': 'American Indian or Alaska Native',
                     '4': 'Asian', '5': 'Native Hawaiian or Other Pacific Islander', '6': 'Other',
                     '7': 'Patient Declined', '8': 'Unknown', '9': 'Two or More Races'}

MBI_RE = re.compile(r'^[1-9][A-Z0-9]{10}$')
MRN_ICE = re.compile(r'^I?CE-([A-Z]{2})-(\d+)-[0-9A-F]+$')
MRN_HEX16 = re.compile(r'^[0-9a-f]{16}$')
TYPE_PRACTICE = re.compile(r'WHN ([A-Z]{2}) PRIMARY CARE (\d+) MRN')
AUTH_PRACTICE = re.compile(r'^PRAC([A-Z]{2})(\d+)$')
MEMBER_RE = re.compile(r'^[A-Z0-9-]{6,24}$')
SAFE_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,40}$')
PLACEHOLDER_NAMES = {'patient', 'unknown', 'test'}


# ---------------------------------------------------------------------------------------------------------------------
# normalizers and key builders (resolve-friendly: adapters build the same keys with these)
# ---------------------------------------------------------------------------------------------------------------------

def _clean(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def normalize_mbi(v):
    """11-char Medicare Beneficiary Identifier, upper case, dashes/spaces removed; None when not MBI-shaped."""
    s = _clean(v)
    if not s:
        return None
    s = re.sub(r'[\s-]', '', s).upper()
    return s if MBI_RE.match(s) else None


def normalize_member_id(v):
    """Payer member/subscriber id as sent, trimmed and upper-cased; None when empty or not id-shaped."""
    s = _clean(v)
    if not s:
        return None
    s = re.sub(r'\s+', '', s).upper()
    # column-shifted rows put product names, plan ids, codes or fragments in the id column: refuse those
    if not MEMBER_RE.match(s) or not re.search(r'\d', s) or s.startswith('WH-'):
        return None
    return s


def payer_id(v):
    """Payer namespace used in member keys: the full payer folder id (MCD2 -> WH-PAYER-MCD2)."""
    s = _clean(v)
    if not s:
        return None
    s = s.upper()
    return s if s.startswith('WH-PAYER-') else 'WH-PAYER-' + s


def _fix_text(s):
    if s and ('Ã' in s or 'Â' in s):
        try:
            s = s.encode('latin-1').decode('utf-8')
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return s


def _ascii_lower(s):
    s = unicodedata.normalize('NFKD', _fix_text(s))
    return ''.join(c for c in s if not unicodedata.combining(c)).lower()


def norm_last(v):
    s = _clean(v)
    if not s:
        return None
    s = re.sub(r'[^a-z]', '', _ascii_lower(s))
    return s or None


def norm_first(v):
    s = _clean(v)
    if not s:
        return None
    toks = re.sub(r'[^a-z ]', ' ', _ascii_lower(s).replace("'", '')).split()
    return toks[0] if toks else None


def parse_date(v):
    """ISO date from the delivered formats (ISO, YYYYMMDD, MMDDYYYY, MM/DD/YYYY, ISO datetimes); None if invalid."""
    s = _clean(v)
    if not s:
        return None
    s = s.split('T')[0].split(' ')[0]
    y = m = d = None
    if re.match(r'^\d{4}-\d{1,2}-\d{1,2}$', s) or re.match(r'^\d{4}/\d{1,2}/\d{1,2}$', s):
        y, m, d = re.split(r'[-/]', s)
    elif re.match(r'^\d{1,2}/\d{1,2}/\d{4}$', s) or re.match(r'^\d{1,2}-\d{1,2}-\d{4}$', s):
        m, d, y = re.split(r'[-/]', s)
    elif re.match(r'^\d{8}$', s):
        if 1800 <= int(s[:4]) <= 2200 and 1 <= int(s[4:6]) <= 12:
            y, m, d = s[:4], s[4:6], s[6:]
        else:
            m, d, y = s[:2], s[2:4], s[4:]
    else:
        return None
    try:
        return _dt.date(int(y), int(m), int(d)).isoformat()
    except ValueError:
        return None


def plausible_dob(iso, as_of=AS_OF):
    return bool(iso) and MIN_DOB <= iso <= as_of


def demographic_key(last, first, dob, as_of=AS_OF):
    """'last|first-token|YYYY-MM-DD' (normalized), or None when a part is missing or the DOB is implausible."""
    ln, fn, d = norm_last(last), norm_first(first), parse_date(dob)
    if not ln or not fn or not plausible_dob(d, as_of) or ln in PLACEHOLDER_NAMES:
        return None
    return f'{ln}|{fn}|{d}'


def normalize_sex(v, scheme=None):
    """'M'/'F'/None. scheme: 'epic' (sex_c 1=F 2=M), 'cms' (CCLF/ALR 1=M 2=F), 'ncpdp' (1=M 2=F); letters and
    words (M/F/male/female) are understood under any scheme."""
    s = _clean(v)
    if not s:
        return None
    s = s.lower()
    if s in ('m', 'male', 'man'):
        return 'M'
    if s in ('f', 'female', 'woman'):
        return 'F'
    if s in ('1', '2'):
        if scheme == 'epic':
            return 'F' if s == '1' else 'M'
        if scheme in ('cms', 'ncpdp'):
            return 'M' if s == '1' else 'F'
    return None


def zip5(v):
    s = _clean(v)
    if not s:
        return None
    digits = re.sub(r'\D', '', s.split('-')[0])
    if len(digits) == 4:
        digits = '0' + digits
    return digits[:5] if len(digits) >= 5 else None


def state_code(v):
    s = _clean(v)
    if not s:
        return None
    if re.match(r'^[A-Za-z]{2}$', s):
        return s.upper()
    if s.isdigit():
        return FIPS_STATE.get(s.zfill(2))
    return STATE_NAMES.get(s.lower())


def mrn_practice(mrn, hint=None):
    m = MRN_ICE.match(mrn or '')
    if m:
        return f'WHN-PRACTICE-{m.group(1)}-{int(m.group(2))}'
    if MRN_HEX16.match(mrn or ''):
        return 'DEEPCHART'
    return practice_from_hint(hint)


def practice_from_hint(hint):
    h = _clean(hint)
    if not h:
        return None
    m = TYPE_PRACTICE.search(h.upper())
    if m:
        return f'WHN-PRACTICE-{m.group(1)}-{int(m.group(2))}'
    m = AUTH_PRACTICE.match(h.upper())
    if m:
        return f'WHN-PRACTICE-{m.group(1)}-{int(m.group(2))}'
    h = h.split('/')[-1]
    return h.upper() if h else None


def mrn_key(mrn, hint=None):
    """mrn:<practice>:<mrn>. The practice comes from the MRN when it encodes one, else from hint (Epic identity type
    name 'WHN CA PRIMARY CARE 8 MRN', ORU authority 'PRACCA8', FHIR folder, or the instance)."""
    v = _clean(mrn)
    if not v or not SAFE_ID.match(v):
        return None
    p = mrn_practice(v, hint)
    return f'mrn:{p}:{v}' if p else None


def _tail(instance):
    s = _clean(instance)
    return s.split('/')[-1] if s else None


def epic_key(instance, pat_id):
    i, p = _tail(instance), _clean(pat_id)
    return f'epic:{i}:{p}' if i and p and re.match(r'^[A-Z]\d{4,10}$', p) else None


def athena_key(contextid, patientid):
    c, p = _clean(contextid), _clean(patientid)
    return f'athena:{c}:{p}' if c and p and c.isdigit() and p.isdigit() else None


def ecw_key(practice, pid):
    i, p = _tail(practice), _clean(pid)
    return f'ecw:{i}:{p}' if i and p and p.isdigit() else None


def fhir_key(folder, patient_id):
    i, p = _tail(folder), _clean(patient_id)
    if p and p.startswith('Patient/'):
        p = p[len('Patient/'):]
    return f'fhir:{i}:{p}' if i and p and SAFE_ID.match(p) else None


def mbi_key(v):
    m = normalize_mbi(v)
    return f'mbi:{m}' if m else None


def member_key(payer, member_id):
    p, m = payer_id(payer), normalize_member_id(member_id)
    return f'member:{p}:{m}' if p and m else None


def sdoh_key(member_id):
    m = normalize_member_id(member_id)
    return f'{SDOH_NS}:{m}' if m else None


def hl7_key(authority, pid3):
    a, p = _clean(authority), _clean(pid3)
    return f'hl7:{a}:{p}' if a and p and SAFE_ID.match(p) else None


def kind_of(key):
    return key.split(':', 1)[0]


def namespace_of(key):
    return key.rsplit(':', 1)[0]


# ---------------------------------------------------------------------------------------------------------------------
# landing access
# ---------------------------------------------------------------------------------------------------------------------

def _tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(db, table):
    return [r[1] for r in db.execute(f'PRAGMA table_info("{table}")')]


def _distinct(db, table, spec, cols_cache):
    """SELECT DISTINCT over the named columns (spec: out name -> column name or tuple of fallbacks), with the row
    count and latest drop per combination. Missing columns read as NULL; a missing table yields nothing."""
    cols = cols_cache.get(table)
    if cols is None:
        return []
    exprs = []
    for out, src in spec.items():
        cands = (src,) if isinstance(src, str) else tuple(src or ())
        col = next((c for c in cands if c in cols), None)
        exprs.append((f'"{col}"' if col else 'NULL') + f' AS "{out}"')
    if not any(e.startswith('"') for e in exprs):
        return []
    drop = 'max("_drop")' if '_drop' in cols else 'NULL'
    sql = (f'SELECT {", ".join(exprs)}, count(*) AS _n, {drop} AS _d FROM "{table}" '
           f'GROUP BY {", ".join(str(i + 1) for i in range(len(exprs)))}')
    cur = db.execute(sql)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur]


# ---------------------------------------------------------------------------------------------------------------------
# collection
# ---------------------------------------------------------------------------------------------------------------------

class _Graph:
    def __init__(self, as_of):
        self.as_of = as_of
        self.keys = {}                # key -> list of observation dicts
        self.links = []               # (a, b, tier, evidence)
        self.sources = Counter()      # table -> distinct rows read
        self.mbi_spans = defaultdict(lambda: {'bgn': set(), 'end': set(), 'demo': Counter()})
        self.xref = {}                # current MBI -> (previous MBI or None, obsolete date)
        self.cms_mbis = set()
        self.subscribers = defaultdict(set)   # (payer, subscriber) -> member ids
        self.sub_own = set()                  # (payer, subscriber, member) asserted own by 834 INS/relationship

    def key(self, k, obs=None):
        if not k:
            return None
        lst = self.keys.setdefault(k, [])
        if obs:
            lst.append(obs)
        return k

    def link(self, a, b, tier, evidence):
        if a and b and a != b:
            self.key(a)
            self.key(b)
            self.links.append((a, b, tier, evidence))


def _obs(pri, row_n, drop, **f):
    o = {k: v for k, v in f.items() if v not in (None, '')}
    o['_pri'], o['_n'], o['_d'] = pri, row_n or 1, drop or ''
    return o


def _person_obs(g, pri, r, *, first, last, dob, sex, scheme=None, addr=None, city=None, state=None, zipc=None,
                county=None, race=None, eth=None, lang=None, phone=None, middle=None):
    f, l = _clean(first), _clean(last)
    if l and l.lower() in PLACEHOLDER_NAMES and not f:
        l = None
    return _obs(pri, r.get('_n'), r.get('_d'), first=f, last=l, middle=_clean(middle), dob=parse_date(dob),
                sex=normalize_sex(sex, scheme), addr=_clean(addr), city=_clean(city), state=state_code(state),
                zip=zip5(zipc), county=_clean(county), race=_clean(race), eth=_clean(eth), lang=_clean(lang),
                phone=_clean(phone))


def _collect_epic(db, g, cc):
    race_names = dict(EPIC_RACE_DEFAULT)
    for r in _distinct(db, 'ehr_epic_zc_patient_race', {'c': 'patient_race_c', 'name': 'name'}, cc):
        if r['c'] and str(r['c']).isdigit() and _clean(r['name']):
            race_names[str(r['c'])] = r['name'].strip()
    races = defaultdict(Counter)
    for r in _distinct(db, 'ehr_epic_patient_race', {'i': '_instance', 'p': 'pat_id', 'c': 'patient_race_c'}, cc):
        k = epic_key(r['i'], r['p'])
        name = race_names.get(str(r['c'] or '').strip())
        if k and name:
            races[k][name] += r['_n']
    rows = _distinct(db, 'ehr_epic_patient', {
        'i': '_instance', 'p': 'pat_id', 'first': 'first_name', 'last': 'last_name', 'dob': 'birth_date',
        'sex': 'sex_c', 'a1': 'add_line_1', 'a2': 'add_line_2', 'city': 'city', 'state': 'state_c', 'zip': 'zip',
        'county': 'county_c', 'phone': 'home_phone'}, cc)
    g.sources['ehr_epic_patient'] += len(rows)
    for r in rows:
        k = epic_key(r['i'], r['p'])
        if not k:
            continue
        deep = 'DEEPCHART' in (r['i'] or '').upper()
        addr = ' '.join(x.strip() for x in (r['a1'], r['a2']) if _clean(x)) or None
        race = races[k].most_common(1)[0][0] if races.get(k) else None
        county = _clean(r['county'])
        g.key(k, _person_obs(g, PRI['epic_deep' if deep else 'ehr'], r, first=r['first'], last=r['last'],
                             dob=r['dob'], sex=r['sex'], scheme='epic', addr=addr, city=r['city'], state=r['state'],
                             zipc=r['zip'], county=county if county and county.isdigit() and len(county) == 5 else None,
                             race=race, phone=r['phone']))
    type_names = defaultdict(Counter)
    for r in _distinct(db, 'ehr_epic_identity_id_type', {'i': '_instance', 't': 'id_type', 'n': 'id_type_name'}, cc):
        if _clean(r['n']) and str(r['t'] or '').strip().isdigit():
            type_names[(_tail(r['i']), r['t'].strip())][r['n'].strip()] += r['_n']
    rows = _distinct(db, 'ehr_epic_identity_id', {'i': '_instance', 'p': 'pat_id', 'mrn': 'identity_id',
                                                  't': 'identity_type_id', 'line': 'line'}, cc)
    g.sources['ehr_epic_identity_id'] += len(rows)
    for r in rows:
        k = epic_key(r['i'], r['p'])
        t = re.match(r'^\d+', str(r['t'] or '').strip())
        t = t.group(0) if t else None
        names = type_names.get((_tail(r['i']), t))
        name = max(names, key=lambda n: (len(n), names[n])) if names else None
        mk = mrn_key(r['mrn'], name or _tail(r['i']))
        if k and mk:
            g.link(k, mk, 1, f'epic identity_id {_tail(r["i"])} type {t} line {_clean(r["line"])}'
                             + (f' ({name})' if name else ''))
    for t in sorted(cc):
        if t.startswith('ehr_epic_') and t not in ('ehr_epic_patient', 'ehr_epic_identity_id') and 'pat_id' in cc[t]:
            for r in _distinct(db, t, {'i': '_instance', 'p': 'pat_id'}, cc):
                g.key(epic_key(r['i'], r['p']))


def _collect_athena(db, g, cc):
    rows = _distinct(db, 'ehr_athena_patient', {
        'c': 'contextid', 'p': 'patientid', 'mrn': 'enterpriseid', 'first': 'firstname', 'last': 'lastname',
        'sex': 'sex', 'dob': 'dob', 'a1': 'address', 'a2': 'address2', 'city': 'city', 'state': 'state', 'zip': 'zip',
        'race': 'race', 'eth': 'ethnicity', 'lang': 'language', 'phone': 'patienthomephone'}, cc)
    g.sources['ehr_athena_patient'] += len(rows)
    for r in rows:
        k = athena_key(r['c'], r['p'])
        if not k:
            continue
        addr = ' '.join(x.strip() for x in (r['a1'], r['a2']) if _clean(x)) or None
        g.key(k, _person_obs(g, PRI['ehr'], r, first=r['first'], last=r['last'], dob=r['dob'], sex=r['sex'],
                             addr=addr, city=r['city'], state=r['state'], zipc=r['zip'], race=r['race'],
                             eth=r['eth'], lang=r['lang'], phone=r['phone']))
        mk = mrn_key(r['mrn'], f'athena/{r["c"]}')
        if mk:
            g.link(k, mk, 1, f'athena PATIENT.enterpriseid context {r["c"]}')
    for r in _distinct(db, 'ehr_athena_chart', {'c': 'contextid', 'mrn': 'enterpriseid'}, cc):
        g.key(mrn_key(r['mrn'], f'athena/{r["c"]}'))
    for t in sorted(cc):
        if t.startswith('ehr_athena_') and t != 'ehr_athena_patient' and 'patientid' in cc[t] and 'contextid' in cc[t]:
            for r in _distinct(db, t, {'c': 'contextid', 'p': 'patientid'}, cc):
                g.key(athena_key(r['c'], r['p']))


def _collect_ecw(db, g, cc):
    eth_names = {}
    for r in _distinct(db, 'ehr_ecw_ethnicity', {'id': 'ethid', 'name': 'name'}, cc):
        if _clean(r['id']) and _clean(r['name']) and r['id'].strip().isdigit():
            eth_names[r['id'].strip()] = r['name'].strip()
    pats = _distinct(db, 'ehr_ecw_patients', {'i': '_instance', 'p': 'pid', 'mrn': 'hl7id', 'lang': 'language',
                                              'race': 'race', 'eth': 'ethnicity'}, cc)
    g.sources['ehr_ecw_patients'] += len(pats)
    extra = defaultdict(Counter)
    for r in pats:
        k = ecw_key(r['i'], r['p'])
        if not k:
            continue
        g.key(k)
        eth = _clean(r['eth'])
        extra[k][(_clean(r['lang']), _clean(r['race']), eth_names.get(eth, eth if eth and not eth.isdigit() else None))] += r['_n']
        mk = mrn_key(r['mrn'], _tail(r['i']))
        if mk:
            g.link(k, mk, 1, f'ecw patients.hl7Id {_tail(r["i"])}')
    users = _distinct(db, 'ehr_ecw_users', {
        'i': '_instance', 'p': 'uid', 'first': 'ufname', 'last': 'ulname', 'dob': 'dob', 'sex': 'sex',
        'a1': 'upaddress', 'a2': 'upaddress2', 'city': 'upcity', 'state': 'upstate', 'zip': 'zipcode',
        'phone': 'upphone', 'ut': 'usertype'}, cc)
    g.sources['ehr_ecw_users'] += len(users)
    for r in users:
        k = ecw_key(r['i'], r['p'])
        if not k or (k not in extra and str(r['ut'] or '').strip() != '3'):
            continue  # providers and staff live in users too
        lang, race, eth = extra[k].most_common(1)[0][0] if extra.get(k) else (None, None, None)
        addr = ' '.join(x.strip() for x in (r['a1'], r['a2']) if _clean(x)) or None
        g.key(k, _person_obs(g, PRI['ehr'], r, first=r['first'], last=r['last'], dob=r['dob'], sex=r['sex'],
                             addr=addr, city=r['city'], state=r['state'], zipc=r['zip'], race=race, eth=eth,
                             lang=lang, phone=r['phone']))
    for t in sorted(cc):
        if t.startswith('ehr_ecw_') and t != 'ehr_ecw_patients' and 'patientid' in cc[t]:
            for r in _distinct(db, t, {'i': '_instance', 'p': 'patientid'}, cc):
                g.key(ecw_key(r['i'], r['p']))


def _collect_fhir(db, g, cc):
    rows = _distinct(db, 'fhir_patient', {
        'i': '_instance', 'p': ('id', 'patient_id'), 'mrn': 'mrn', 'mbi': 'mbi', 'first': 'name_given',
        'last': 'name_family', 'dob': 'birth_date', 'sex': 'gender', 'addr': 'address_line', 'city': 'address_city',
        'state': 'address_state', 'zip': 'address_zip', 'race': 'race', 'eth': 'ethnicity', 'lang': 'language'}, cc)
    g.sources['fhir_patient'] += len(rows)
    for r in rows:
        k = fhir_key(r['i'], r['p'])
        if not k:
            continue
        deep = 'DEEPCHART' in (r['i'] or '').upper()
        g.key(k, _person_obs(g, PRI['epic_deep' if deep else 'ehr'], r, first=r['first'], last=r['last'],
                             dob=r['dob'], sex=r['sex'], addr=r['addr'], city=r['city'], state=r['state'],
                             zipc=r['zip'], race=r['race'], eth=r['eth'], lang=r['lang']))
        mk = mrn_key(r['mrn'], _tail(r['i']))
        if mk:
            g.link(k, mk, 1, f'fhir Patient.identifier MR {_tail(r["i"])}')
        bk = mbi_key(r['mbi'])
        if bk:
            g.link(k, bk, 2, 'fhir Patient.identifier MBI')
    for t in sorted(cc):
        if t.startswith('fhir_') and t != 'fhir_patient' and 'patient_id' in cc[t]:
            for r in _distinct(db, t, {'i': '_instance', 'p': 'patient_id'}, cc):
                g.key(fhir_key(r['i'], r['p']))


def _collect_hl7(db, g, cc):
    rows = _distinct(db, 'hl7_message', {
        'id': ('patient_id', 'patient_mrn'), 'auth': ('patient_id_assigning_authority', 'patient_mrn_assigning_authority'),
        'code': 'message_code', 'first': 'patient_name_given', 'last': 'patient_name_family', 'dob': 'patient_dob',
        'dobraw': 'patient_dob_raw', 'sex': 'patient_sex', 'addr': 'patient_address_street',
        'city': 'patient_address_city', 'state': 'patient_address_state', 'zip': 'patient_address_zip'}, cc)
    g.sources['hl7_message'] += len(rows)
    for r in rows:
        k = hl7_key(r['auth'], r['id'])
        if not k:
            continue
        g.key(k, _person_obs(g, PRI['hl7'], r, first=r['first'], last=r['last'], dob=r['dob'] or r['dobraw'],
                             sex=r['sex'], addr=r['addr'], city=r['city'], state=r['state'], zipc=r['zip']))
        auth = (r['auth'] or '').strip().upper()
        if (r['code'] or '').upper() == 'ORU' or AUTH_PRACTICE.match(auth):
            mk = mrn_key(r['id'], auth)
            if mk:
                g.link(k, mk, 1, f'ORU PID-3 authority {auth}')


def _collect_cms(db, g, cc):
    rows = _distinct(db, 'cclf_beneficiary_demographics', {
        'mbi': 'bene_mbi_id', 'first': 'bene_1st_name', 'middle': 'bene_midl_name', 'last': 'bene_last_name',
        'dob': 'bene_dob', 'sex': 'bene_sex_cd', 'race': 'bene_race_cd', 'addr': 'bene_line_1_adr',
        'city': 'geo_zip_plc_name', 'state': 'geo_usps_state_cd', 'zip5': 'geo_zip5_cd', 'zip': 'bene_zip_cd',
        'fst': 'bene_fips_state_cd', 'fct': 'bene_fips_cnty_cd', 'bgn': 'bene_rng_bgn_dt', 'end': 'bene_rng_end_dt'}, cc)
    g.sources['cclf_beneficiary_demographics'] += len(rows)
    for r in rows:
        k = mbi_key(r['mbi'])
        if not k:
            continue
        g.cms_mbis.add(k)
        county = None
        if _clean(r['fst']) and _clean(r['fct']) and r['fst'].strip().isdigit() and r['fct'].strip().isdigit():
            county = r['fst'].strip().zfill(2) + r['fct'].strip().zfill(3)
        o = _person_obs(g, PRI['cclf'], r, first=r['first'], middle=r['middle'], last=r['last'], dob=r['dob'],
                        sex=r['sex'], scheme='cms', addr=r['addr'], city=r['city'], state=r['state'],
                        zipc=r['zip5'] or r['zip'], county=county, race=CCLF_RACE.get(str(r['race'] or '').strip()))
        g.key(k, o)
        sp = g.mbi_spans[k]
        if parse_date(r['bgn']):
            sp['bgn'].add(parse_date(r['bgn']))
        if parse_date(r['end']):
            sp['end'].add(parse_date(r['end']))
        sp['demo'][(norm_last(r['last']), norm_first(r['first']), parse_date(r['dob']), o.get('sex'))] += r['_n']
    for r in _distinct(db, 'cclf_enrollment', {'mbi': 'current_bene_mbi_id'}, cc):
        k = mbi_key(r['mbi'])
        if k:
            g.cms_mbis.add(g.key(k))
    for r in _distinct(db, 'cclf_beneficiary_xref', {'cur': 'crnt_num', 'prev': 'prvs_num', 'obs': 'prvs_id_obslt_dt'}, cc):
        cur, prev = mbi_key(r['cur']), mbi_key(r['prev'])
        if cur:
            g.cms_mbis.add(g.key(cur))
            g.xref[cur] = (prev, parse_date(r['obs']))
            if prev:
                g.cms_mbis.add(g.key(prev))
                g.link(cur, prev, 2, 'CCLF9 crnt_num/prvs_num')
    for t in sorted(cc):
        if t.startswith('cclf_') and t not in ('cclf_beneficiary_demographics',) and 'bene_mbi_id' in cc[t]:
            for r in _distinct(db, t, {'mbi': 'bene_mbi_id'}, cc):
                k = mbi_key(r['mbi'])
                if k:
                    g.cms_mbis.add(g.key(k))
    for t in sorted(cc):
        if t.startswith('alr_') and 'bene_mbi_id' in cc[t]:
            rows = _distinct(db, t, {'mbi': 'bene_mbi_id', 'first': 'bene_1st_name', 'last': 'bene_last_name',
                                     'sex': 'bene_sex_cd', 'dob': 'bene_brth_dt', 'state': 'geo_ssa_state_name',
                                     'county': 'state_county_cd'}, cc)
            g.sources[t] += len(rows)
            for r in rows:
                k = mbi_key(r['mbi'])
                if not k:
                    continue
                g.cms_mbis.add(k)
                county = _clean(r['county'])
                g.key(k, _person_obs(g, PRI['alr'], r, first=r['first'], last=r['last'], dob=r['dob'], sex=r['sex'],
                                     scheme='cms', state=r['state'],
                                     county=county if county and county.isdigit() and len(county) == 5 else None))
    rows = _distinct(db, 'programs_mssp_qalr', {'mbi': 'mbi', 'name': 'bene_name', 'dob': 'bene_dob', 'sex': 'bene_sex'}, cc)
    g.sources['programs_mssp_qalr'] += len(rows)
    for r in rows:
        k = mbi_key(r['mbi'])
        if not k:
            continue
        g.cms_mbis.add(k)
        first, last = _split_name(r['name'])
        g.key(k, _person_obs(g, PRI['alr'], r, first=first, last=last, dob=r['dob'], sex=r['sex'], scheme='cms'))


def _split_name(v):
    s = _clean(v)
    if not s:
        return None, None
    if ',' in s:
        last, first = s.split(',', 1)
        return first.strip() or None, last.strip() or None
    toks = s.split()
    if len(toks) == 1:
        return None, toks[0]
    return toks[0], toks[-1]


def _payer_of(r):
    inst = _clean(r.get('i'))
    if inst and inst.upper().startswith('WH-PAYER-'):
        return inst.upper()
    for f in ('payer', 'payer_alt'):
        v = _clean(r.get(f))
        if v:
            return payer_id(v)
    return None


def _member(g, payer, mid, obs=None):
    k = member_key(payer, mid)
    if not k:
        return None
    g.key(k, obs)
    if payer_id(payer) == 'WH-PAYER-MSSP':  # MSSP member id is the MBI everywhere it is sent
        bk = mbi_key(mid)
        if bk:
            g.link(k, bk, 2, 'WH-PAYER-MSSP member id = MBI')
    return k


def _collect_payers(db, g, cc):
    rows = _distinct(db, 'x12_834_member', {
        'i': '_instance', 'payer': 'sponsor_name', 'mid': 'member_id', 'sub': 'subscriber_id',
        'ind': 'subscriber_indicator', 'rel': 'relationship_code', 'first': 'first_name', 'last': 'last_name',
        'middle': 'middle_name', 'dob': 'dob', 'sex': 'gender', 'addr': 'address_1', 'city': 'city', 'state': 'state',
        'zip': 'postal_code', 'product': 'plan_coverage_description'}, cc)
    g.sources['x12_834_member'] += len(rows)
    for r in rows:
        payer = _payer_of(r)
        o = _person_obs(g, PRI['834'], r, first=r['first'], last=r['last'], middle=r['middle'], dob=r['dob'],
                        sex=r['sex'], addr=r['addr'], city=r['city'], state=r['state'], zipc=r['zip'])
        k = _member(g, payer, r['mid'], o)
        if not k:
            continue
        sub = normalize_member_id(r['sub'])
        mid = normalize_member_id(r['mid'])
        if sub and sub != mid:
            g.subscribers[(payer, sub)].add(mid)
            if (r['ind'] or '').strip().upper() == 'Y' or (r['rel'] or '').strip() == '18':
                g.sub_own.add((payer, sub, mid))
        if (r['product'] or '').strip().upper() == 'MEDICARE_FFS':
            bk = mbi_key(r['mid'])
            if bk:
                g.link(k, bk, 2, f'834 MEDICARE_FFS member id = MBI ({payer})')
    rows = _distinct(db, 'payers_eligibility', {
        'i': '_instance', 'payer': ('payer', 'flat_payer'), 'mid': 'member_id', 'sub': 'subscriber_id',
        'first': 'first_name', 'last': 'last_name', 'dob': 'dob', 'sex': 'gender', 'addr': 'address', 'city': 'city',
        'state': 'state', 'zip': 'zip'}, cc)
    g.sources['payers_eligibility'] += len(rows)
    for r in rows:
        payer = _payer_of(r)
        o = _person_obs(g, PRI['eligibility'], r, first=r['first'], last=r['last'], dob=r['dob'], sex=r['sex'],
                        addr=r['addr'], city=r['city'], state=r['state'], zipc=r['zip'])
        k = _member(g, payer, r['mid'], o)
        sub, mid = normalize_member_id(r['sub']), normalize_member_id(r['mid'])
        if k and sub and sub != mid:
            g.subscribers[(payer, sub)].add(mid)
    for t in ('payers_medical', 'payers_pharmacy'):
        rows = _distinct(db, t, {'i': '_instance', 'payer': ('payer', 'flat_payer'), 'mid': 'member_id'}, cc)
        g.sources[t] += len(rows)
        for r in rows:
            _member(g, _payer_of(r), r['mid'])
    rows = _distinct(db, 'x12_837_claim', {
        'i': '_instance', 'payer': 'payer_id', 'payer_alt': 'payer_name', 'mid': 'subscriber_member_id',
        'first': 'subscriber_first_name', 'last': 'subscriber_last_name', 'dob': 'subscriber_dob',
        'sex': 'subscriber_gender', 'pmid': 'patient_member_id', 'pfirst': 'patient_first_name',
        'plast': 'patient_last_name', 'pdob': 'patient_dob', 'psex': 'patient_gender'}, cc)
    g.sources['x12_837_claim'] += len(rows)
    for r in rows:
        payer = _payer_of(r)
        _member(g, payer, r['mid'], _person_obs(g, PRI['837'], r, first=r['first'], last=r['last'], dob=r['dob'],
                                                sex=r['sex']))
        if _clean(r['pmid']):
            _member(g, payer, r['pmid'], _person_obs(g, PRI['837'], r, first=r['pfirst'], last=r['plast'],
                                                     dob=r['pdob'], sex=r['psex']))
    rows = _distinct(db, 'x12_835_claim', {
        'i': '_instance', 'payer': 'payer_name', 'iid': 'insured_id', 'iq': 'insured_id_qualifier',
        'pid': 'patient_id', 'pq': 'patient_id_qualifier'}, cc)
    g.sources['x12_835_claim'] += len(rows)
    for r in rows:
        payer = _payer_of(r)
        for idv, q in ((r['iid'], r['iq']), (r['pid'], r['pq'])):
            # some senders drop one NM1 element, which shifts the id into the qualifier position (NM1*QC*1*PATIENT****MI*id)
            v = _clean(idv) or (_clean(q) if _clean(q) and len(_clean(q)) > 3 else None)
            if v:
                _member(g, payer, v)
    rows = _distinct(db, 'ncpdp_claim', {
        'i': '_instance', 'payer': 'file_sender', 'mid': 'cardholder_id', 'first': 'patient_first_name',
        'last': 'patient_last_name', 'dob': 'patient_dob', 'dobraw': 'patient_dob_raw', 'sex': 'patient_gender',
        'sexc': 'patient_gender_code'}, cc)
    g.sources['ncpdp_claim'] += len(rows)
    for r in rows:
        sex = r['sex'] or normalize_sex(r['sexc'], 'ncpdp')
        _member(g, _payer_of(r), r['mid'], _person_obs(g, PRI['ncpdp'], r, first=r['first'], last=r['last'],
                                                       dob=r['dob'] or r['dobraw'], sex=sex))
    rows = _distinct(db, 'programs_care_gaps', {'i': '_instance', 'payer': ('payer', 'flat_payer'),
                                                'mid': 'member_id', 'name': 'member_name', 'dob': 'dob'}, cc)
    g.sources['programs_care_gaps'] += len(rows)
    for r in rows:
        first, last = _split_name(r['name'])
        _member(g, _payer_of(r), r['mid'], _person_obs(g, PRI['care_gap'], r, first=first, last=last, dob=r['dob'],
                                                       sex=None))
    rows = _distinct(db, 'programs_risk_scores', {'i': '_instance', 'payer': ('payer', 'flat_payer'),
                                                  'mid': 'member_id', 'dob': 'dob', 'sex': 'sex'}, cc)
    g.sources['programs_risk_scores'] += len(rows)
    for r in rows:
        _member(g, _payer_of(r), r['mid'], _person_obs(g, PRI['risk'], r, first=None, last=None, dob=r['dob'],
                                                       sex=r['sex']))


def _collect_sdoh(db, g, cc):
    rows = _distinct(db, 'sdoh_vendor', {'mid': 'member_id', 'first': 'first_name', 'last': 'last_name', 'dob': 'dob',
                                         'zip': 'zip', 'county': 'county_fips',
                                         'lang': ('language_preference_estimate', 'preferred_language')}, cc)
    g.sources['sdoh_vendor'] += len(rows)
    by_id = defaultdict(set)
    for k in g.keys:
        if k.startswith('member:') and not k.startswith(SDOH_NS + ':'):
            by_id[k.rsplit(':', 1)[1]].add(k)
    for r in rows:
        k = sdoh_key(r['mid'])
        if not k:
            continue
        county = _clean(r['county'])
        g.key(k, _person_obs(g, PRI['sdoh'], r, first=r['first'], last=r['last'], dob=r['dob'], sex=None,
                             zipc=r['zip'], county=county if county and county.isdigit() and len(county) == 5 else None,
                             lang=r['lang']))
        mid = normalize_member_id(r['mid'])
        bk = mbi_key(mid)
        if bk and (bk in g.cms_mbis or bk in g.keys):
            g.link(k, bk, 2, 'SDOH member_id = MBI')
        targets = by_id.get(mid, set())
        if len(targets) == 1:
            g.link(k, next(iter(targets)), 0, 'SDOH member_id = payer member id')


def _post_links(g):
    # a member's own subscriber id (subscriber ids are never shared across members of one payer)
    for (payer, sub), mids in sorted(g.subscribers.items()):
        if len(mids) == 1:
            mid = next(iter(mids))
            own = (payer, sub, mid) in g.sub_own
            g.link(member_key(payer, mid), member_key(payer, sub), 0,
                   'own subscriber id' + (' (834 subscriber loop)' if own else ' (unique to member)'))
    # member ids that are MBIs known to CMS feeds
    for k in list(g.keys):
        if k.startswith('member:') and not k.startswith(SDOH_NS + ':'):
            bk = mbi_key(k.rsplit(':', 1)[1])
            if bk and bk in g.cms_mbis:
                g.link(k, bk, 2, 'member id = MBI present in CCLF/ALR')
    # MBI re-issue: contiguous CCLF8 spans with identical demographics
    groups = defaultdict(list)
    for k, sp in g.mbi_spans.items():
        if not sp['demo'] or not sp['bgn']:
            continue
        demo = sp['demo'].most_common(1)[0][0]
        if all(demo[:3]):
            groups[demo].append((min(sp['bgn']), max(sp['end']) if sp['end'] else None, k))
    for demo, items in groups.items():
        if len(items) < 2:
            continue
        items.sort()
        for (b1, e1, k1), (b2, e2, k2) in zip(items, items[1:]):
            if not e1:
                continue
            gap = (_dt.date.fromisoformat(b2) - _dt.date.fromisoformat(e1)).days
            if 0 <= gap <= 1:
                xr = g.xref.get(k2)
                corro = f'; CCLF9 lists {k2[4:]} obsolete-date {xr[1]}' if xr and xr[1] == b2 else ''
                g.link(k1, k2, 2, f'MBI re-issue: CCLF8 span {k1[4:]} ends {e1}, {k2[4:]} begins {b2}, same '
                                  f'name/DOB/sex{corro}')


# ---------------------------------------------------------------------------------------------------------------------
# union-find and tier 3
# ---------------------------------------------------------------------------------------------------------------------

class _UF:
    def __init__(self, items):
        self.p = {k: k for k in items}

    def find(self, x):
        p = self.p
        root = x
        while p[root] != root:
            root = p[root]
        while p[x] != root:
            p[x], x = root, p[x]
        return root

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return None
        if rb < ra:
            ra, rb = rb, ra
        self.p[rb] = ra
        return ra


def _key_profile(obs, as_of):
    sexes, zips, demos = Counter(), set(), set()
    has_person = False
    for o in obs:
        if o.get('sex'):
            sexes[o['sex']] += o['_n']
        if o.get('zip'):
            zips.add(o['zip'])
        dk = demographic_key(o.get('last'), o.get('first'), o.get('dob'), as_of)
        if dk:
            demos.add(dk)
        if (o.get('last') or o.get('first')) and o.get('dob'):
            has_person = True
    sex = None
    if sexes:
        top = sexes.most_common(2)
        sex = top[0][0] if len(top) == 1 or top[0][1] > top[1][1] else None
    return sex, zips, demos, has_person


def _resolve(g):
    uf = _UF(g.keys)
    unions = Counter()
    for a, b, tier, ev in sorted(g.links, key=lambda x: x[2]):
        if uf.union(a, b):
            unions[tier] += 1
    prof = {k: _key_profile(obs, g.as_of) for k, obs in g.keys.items()}
    agg = {}
    for k, (sex, zips, demos, _) in prof.items():
        r = uf.find(k)
        a = agg.setdefault(r, {'sex': set(), 'zip': set(), 'ns': defaultdict(set)})
        if sex:
            a['sex'].add(sex)
            a['ns'][namespace_of(k)].add(sex)
        a['zip'] |= zips
    by_demo = defaultdict(set)
    for k, (_, _, demos, _) in prof.items():
        for d in demos:
            by_demo[d].add(k)
    tier3, blocked = [], Counter()
    agreement = Counter()

    def conflict(a, b):
        for ns, sa in a['ns'].items():
            sb = b['ns'].get(ns)
            if sb and len(sa | sb) > 1:
                return ns
        return None

    for phase in ('sex', 'zip'):
        for d in sorted(by_demo):
            ks = sorted(by_demo[d], key=_anchor_rank)
            roots = []
            for k in ks:
                r = uf.find(k)
                if r not in [x[0] for x in roots]:
                    roots.append((r, k))
            if len(roots) < 2:
                continue
            for i in range(len(roots)):
                for j in range(i + 1, len(roots)):
                    ra, rb = uf.find(roots[i][0]), uf.find(roots[j][0])
                    if ra == rb:
                        continue
                    A, B = agg[ra], agg[rb]
                    sex_ok = bool(A['sex'] & B['sex'])
                    zip_ok = bool(A['zip'] & B['zip'])
                    if not (sex_ok if phase == 'sex' else zip_ok):
                        if phase == 'zip' and not sex_ok and not zip_ok:
                            blocked['no_sex_or_zip_agreement'] += 1
                        continue
                    ns = conflict(A, B)
                    if ns:
                        blocked['same_source_sex_conflict'] += 1
                        continue
                    new = uf.union(ra, rb)
                    old = rb if new == ra else ra
                    M, O = agg[new], agg.pop(old)
                    M['sex'] |= O['sex']
                    M['zip'] |= O['zip']
                    for n, s in O['ns'].items():
                        M['ns'][n] |= s
                    why = 'sex' if sex_ok else 'ZIP5'
                    agreement[why + (' + ZIP5' if sex_ok and zip_ok else '')] += 1
                    ev = f'demographic key {d} + {why} agreement'
                    tier3.append((roots[i][1], roots[j][1], 3, ev))
                    unions[3] += 1
    # blocked pairs are counted per attempt in each phase; report distinct attempts loosely
    blocked.update({'agreement:' + k: v for k, v in agreement.items()})
    return uf, prof, unions, tier3, blocked


def _anchor_rank(k):
    kind = kind_of(k)
    rank = 8 if k.startswith(SDOH_NS + ':') else KIND_RANK.get(kind, 9)
    return (rank, k)


def _tiers(members, edges, anchor):
    """Bottleneck (minimax) tier from the anchor to each key, with the evidence of the edge that set it."""
    adj = defaultdict(list)
    for a, b, t, ev in edges:
        adj[a].append((b, t, ev))
        adj[b].append((a, t, ev))
    best = {anchor: (0, 'anchor key')}
    heap = [(0, anchor)]
    done = set()
    while heap:
        t, k = heapq.heappop(heap)
        if k in done:
            continue
        done.add(k)
        for n, et, ev in adj.get(k, ()):
            nt = max(t, et)
            if n not in best or nt < best[n][0]:
                best[n] = (nt, f'tier {et}: {ev} (via {k})')
                heapq.heappush(heap, (nt, n))
    for k in members:
        best.setdefault(k, (None, 'unlinked'))
    return best


# ---------------------------------------------------------------------------------------------------------------------
# payload
# ---------------------------------------------------------------------------------------------------------------------

def _pick(obs, field, filt=None):
    score = {}
    for o in obs:
        v = o.get(field)
        if v is None or (filt and not filt(v)):
            continue
        s = score.setdefault(v, [0, 0, ''])
        s[0] = max(s[0], o['_pri'])
        s[1] += o['_n']
        s[2] = max(s[2], o['_d'] or '')
    if not score:
        return None
    return max(score, key=lambda v: (score[v][0], score[v][1], score[v][2], str(v)))


def _display(v):
    if not v:
        return None
    return v.title() if v.isupper() or v.islower() else v


def _age(dob, as_of):
    if not dob:
        return None
    b, a = _dt.date.fromisoformat(dob), _dt.date.fromisoformat(as_of)
    return a.year - b.year - ((a.month, a.day) < (b.month, b.day))


def _segment(keys):
    deep = any(k.startswith(('epic:EPIC-DEEPCHART:', 'fhir:DEEPCHART:', 'mrn:DEEPCHART:')) for k in keys)
    if deep:
        return 'deep_chart'
    claims = any(kind_of(k) in ('mbi', 'member') and not k.startswith(SDOH_NS + ':') for k in keys)
    # practice EHR only: hospital ADT (hl7:HOSP..) does not make a member 'claims_plus_ehr'
    ehr = any(kind_of(k) in EHR_KINDS and not (kind_of(k) == 'hl7' and not k.startswith('hl7:PRAC')) for k in keys)
    if claims and ehr:
        return 'claims_plus_ehr'
    if claims:
        return 'claims_only'
    return 'ehr_only'


def _payload(pid, keys, obs, as_of, max_tier):
    pick_name_obs = [o for o in obs if o.get('first') or o.get('last')]
    # names: take first+last from the same best observation family so they stay consistent
    first = _pick(pick_name_obs, 'first')
    last = _pick(pick_name_obs, 'last')
    dob = _pick(obs, 'dob', lambda v: plausible_dob(v, as_of)) or _pick(obs, 'dob')
    member_ids = sorted(k[len('member:'):] for k in keys if k.startswith('member:') and not k.startswith(SDOH_NS + ':'))
    return {
        'patient_id': pid,
        'display_name': ' '.join(x for x in (_display(first), _display(last)) if x) or None,
        'age_reported': _age(dob, as_of) if dob and plausible_dob(dob, as_of) else None,
        'sex_reported': _pick(obs, 'sex'),
        'birth_date': dob,
        'race': _pick(obs, 'race'),
        'ethnicity': _pick(obs, 'eth'),
        'preferred_language': _pick(obs, 'lang'),
        'address_line': _display(_pick(obs, 'addr')),
        'city': _display(_pick(obs, 'city')),
        'state': _pick(obs, 'state'),
        'postal_code': _pick(obs, 'zip'),
        'county_fips': _pick(obs, 'county'),
        'phone': _pick(obs, 'phone'),
        'data_segment': _segment(keys),
        'synthetic': True,
        'identity_status': 'matched',
        'identity_max_tier': max_tier,
        'source_keys': len(keys),
        'mbi': sorted(k[4:] for k in keys if k.startswith('mbi:')),
        'member_ids': member_ids,
        'mrns': sorted(k[4:] for k in keys if k.startswith('mrn:')),
        'source_systems': sorted({(k.split(':')[0] + ':' + k.split(':')[1]) for k in keys}),
    }


# ---------------------------------------------------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------------------------------------------------

def build(db, *, as_of=AS_OF, top=10):
    started = time.time()
    cc = {t: _columns(db, t) for t in _tables(db) if not t.startswith(('stg_', 'sqlite_'))}
    g = _Graph(as_of)
    for fn in (_collect_epic, _collect_athena, _collect_ecw, _collect_fhir, _collect_hl7, _collect_cms,
               _collect_payers, _collect_sdoh):
        fn(db, g, cc)
    _post_links(g)
    uf, prof, unions, tier3, blocked = _resolve(g)
    clusters = defaultdict(list)
    for k in g.keys:
        clusters[uf.find(k)].append(k)
    edges_by_root = defaultdict(list)
    for e in g.links + tier3:
        edges_by_root[uf.find(e[0])].append(e)

    db.execute('DROP TABLE IF EXISTS stg_person_map')
    db.execute('DROP TABLE IF EXISTS stg_person')
    db.execute('CREATE TABLE stg_person_map(source_key TEXT PRIMARY KEY, person_id TEXT, tier INTEGER, evidence TEXT)')
    db.execute('CREATE TABLE stg_person(person_id TEXT PRIMARY KEY, payload TEXT)')
    map_rows, person_rows = [], []
    unresolved = Counter()
    segments = Counter()
    multi_dob, sizes = [], []
    key_tiers = Counter()
    for root, members in clusters.items():
        members.sort(key=_anchor_rank)
        resolved = any(prof[k][3] for k in members)
        if not resolved:
            for k in members:
                unresolved[kind_of(k) if not k.startswith(SDOH_NS + ':') else 'sdoh']+= 1
                map_rows.append((k, None, None, 'unresolved: no name+DOB in any linked source'))
            continue
        anchor = members[0]
        pid = 'AGP-' + hashlib.sha256(anchor.encode()).hexdigest()[:12]
        tiers = _tiers(members, edges_by_root[root], anchor)
        obs = [o for k in members for o in g.keys[k]]
        max_tier = max((t for t, _ in tiers.values() if t is not None), default=0)
        payload = _payload(pid, members, obs, as_of, max_tier)
        person_rows.append((pid, json.dumps(payload, sort_keys=True)))
        segments[payload['data_segment']] += 1
        for k in members:
            t, ev = tiers[k]
            key_tiers[t] += 1
            map_rows.append((k, pid, t, ev))
        dobs = sorted({o['dob'] for o in obs if o.get('dob') and plausible_dob(o['dob'], as_of)})
        demos = {d for k in members for d in prof[k][2]}
        info = {'person_id': pid, 'display_name': payload['display_name'], 'keys': len(members),
                'kinds': dict(Counter(kind_of(k) for k in members)), 'distinct_dobs': dobs,
                'demographic_keys': len(demos), 'segment': payload['data_segment']}
        sizes.append(info)
        if len(dobs) > 1:
            multi_dob.append(info)
    # near misses: distinct persons sharing DOB + ZIP5 + sex and one exact name part (typos, nicknames, name changes);
    # reported for review, never merged (tier 3 requires the exact demographic key)
    near, seen = defaultdict(list), []
    for pid, js in person_rows:
        p = json.loads(js)
        parts = (p['display_name'] or '').split()
        if not parts or not p['birth_date'] or not p['postal_code']:
            continue
        base = (p['birth_date'], p['postal_code'], p['sex_reported'])
        near[base + ('first', norm_first(parts[0]))].append((pid, p['display_name']))
        near[base + ('last', norm_last(parts[-1]))].append((pid, p['display_name']))
    for k, v in sorted(near.items(), key=lambda x: (str(x[0]))):
        if len(v) > 1:
            seen.append({'dob': k[0], 'zip': k[1], 'sex': k[2], 'shared': k[3], 'persons': v})
    db.executemany('INSERT INTO stg_person_map VALUES (?,?,?,?)', map_rows)
    db.executemany('INSERT INTO stg_person VALUES (?,?)', person_rows)
    sizes.sort(key=lambda x: (-x['keys'], x['person_id']))
    multi_dob.sort(key=lambda x: (-len(x['distinct_dobs']), -x['keys'], x['person_id']))
    link_counts = Counter(t for _, _, t, _ in g.links)
    link_counts[3] += len(tier3)
    return {
        'persons': len(person_rows),
        'source_keys': len(g.keys),
        'keys_by_kind': dict(sorted(Counter(kind_of(k) if not k.startswith(SDOH_NS + ':') else 'sdoh'
                                             for k in g.keys).items())),
        'links_by_tier': {str(t): link_counts[t] for t in sorted(link_counts)},
        'unions_by_tier': {str(t): unions[t] for t in sorted(unions)},
        'keys_by_tier': {str(t): key_tiers[t] for t in sorted(key_tiers, key=lambda x: (x is None, x))},
        'clusters_merged_by_demographics': unions[3],
        'tier3_blocked': {k: v for k, v in blocked.items() if not k.startswith('agreement:')},
        'tier3_merges_by_agreement': {k[10:]: v for k, v in blocked.items() if k.startswith('agreement:')},
        'unresolved_keys': dict(sorted(unresolved.items())),
        'unresolved_total': sum(unresolved.values()),
        'segments': dict(sorted(segments.items())),
        'largest_clusters': sizes[:top],
        'near_miss_pairs': len(seen),
        'near_miss_examples': seen[:top],
        'clusters_with_multiple_dobs': len(multi_dob),
        'multiple_dob_examples': multi_dob[:top],
        'sources': dict(sorted(g.sources.items())),
        'as_of': as_of,
        'seconds': round(time.time() - started, 1),
    }


def resolve(db, source_key):
    row = db.execute('SELECT person_id FROM stg_person_map WHERE source_key = ?', (source_key,)).fetchone()
    return row[0] if row else None


if __name__ == '__main__':  # python -m clinical_core.agere.ingest.identity <landing.sqlite>
    import sys
    con = sqlite3.connect(sys.argv[1])
    rep = build(con)
    con.commit()
    print(json.dumps(rep, indent=1, default=str))
