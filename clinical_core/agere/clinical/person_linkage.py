"""Enterprise person linking: which received charts belong to the same person ('duplicate charts').

Client-visible identifiers only: the chart demographics in the record store (name, birth date, sex, street address,
postal code, phone) and payer member IDs carried on enrollment spans and payer claims. No generator truth, no answer
keys, and the chart's data_segment label is never read.

Method (Fellegi & Sunter, "A Theory for Record Linkage", JASA 1969; frequency-based u-probabilities as in Winkler,
"String Comparator Metrics and Enhanced Decision Rules in the Fellegi-Sunter Model of Record Linkage", 1990):
  1. Blocking: candidate pairs share at least one blocking key (birth date; phone; Soundex(last name) + birth year;
     Soundex(first name) + postal code; normalised address + postal code; a payer member ID; a facility MRN). Blocks larger than
     max_block are skipped (a key that common carries no identity).
  2. Comparison: each field agrees, partially agrees or disagrees. Names use Jaro-Winkler (>= 0.92 partial) and a
     nickname table; birth dates partially agree on a single-component difference or a month/day transposition;
     addresses compare after USPS-style normalisation.
  3. Weights: agreement log2(m/u), disagreement log2((1-m)/(1-u)); partial agreement = half the agreement weight.
     m-probabilities are stated parameters (M below); u-probabilities are estimated from the received charts
     (value-specific for exact name and birth-date agreement: a rare surname is more informative than a common one),
     or stated through u_override when the population is too small to estimate them.
  4. Decision: link when the summed weight >= threshold (default 15 bits), or deterministically when two charts carry the
     same payer member ID, or the same facility medical record number (source system + source patient ID: an MRN is
     unique within its assigning authority, HL7 v2 PID-3 / CX.4), and their birth dates agree at least partially.
     Links are closed transitively (union-find);
     a cluster's enterprise person id is 'AGP-' + its smallest chart id.

Precision/recall: threshold is the lever (higher = fewer, surer links). link_report() returns the pair weights so the
cut can be reviewed.
"""
from collections import Counter, defaultdict
from pathlib import Path
import json, math, re, sqlite3

VERSION = 'person-linkage/2'
DEFAULTS = {'threshold': 15.0, 'max_block': 60, 'partial_jw': 0.92, 'member_id_link': True, 'mrn_link': True, 'value_specific': True, 'u_override': {}}
# m-probabilities: probability a field agrees for a true match (stated assumptions; typical registry values).
M = {'first': 0.92, 'last': 0.93, 'birth_date': 0.98, 'sex': 0.98, 'address': 0.75, 'postal': 0.85, 'phone': 0.75}
NICKNAMES = [('WILLIAM', 'BILL', 'WILL', 'BILLY', 'LIAM'), ('ROBERT', 'BOB', 'ROB', 'BOBBY', 'BOBBIE'), ('RICHARD', 'RICK', 'DICK', 'RICH'),
             ('JAMES', 'JIM', 'JIMMY', 'JAMIE'), ('JOHN', 'JACK', 'JOHNNY'), ('JOSEPH', 'JOE', 'JOEY'), ('THOMAS', 'TOM', 'TOMMY'),
             ('MICHAEL', 'MIKE', 'MICKEY'), ('CHARLES', 'CHARLIE', 'CHUCK'), ('DAVID', 'DAVE'), ('DANIEL', 'DAN', 'DANNY'),
             ('EDWARD', 'ED', 'EDDIE', 'TED'), ('ANTHONY', 'TONY'), ('CHRISTOPHER', 'CHRIS'), ('MATTHEW', 'MATT'), ('NICHOLAS', 'NICK'),
             ('STEVEN', 'STEVE'), ('STEPHEN', 'STEVE'), ('KENNETH', 'KEN', 'KENNY'), ('RONALD', 'RON', 'RONNIE'), ('DONALD', 'DON'),
             ('GERALD', 'JERRY'), ('LAWRENCE', 'LARRY'), ('RAYMOND', 'RAY'), ('SAMUEL', 'SAM'), ('BENJAMIN', 'BEN'), ('ALEXANDER', 'ALEX'),
             ('PATRICK', 'PAT'), ('PATRICIA', 'PAT', 'PATTY', 'TRISH'), ('ELIZABETH', 'LIZ', 'BETH', 'BETTY', 'LIZZIE', 'ELIZA'),
             ('MARGARET', 'MAGGIE', 'PEGGY', 'MEG'), ('KATHERINE', 'KATE', 'KATHY', 'KATIE'), ('CATHERINE', 'CATHY', 'CATE'),
             ('JENNIFER', 'JENNY', 'JEN'), ('SUSAN', 'SUE', 'SUZY'), ('DEBORAH', 'DEBBIE', 'DEB'), ('BARBARA', 'BARB'),
             ('DOROTHY', 'DOT', 'DOTTIE'), ('REBECCA', 'BECKY'), ('VICTORIA', 'VICKY', 'TORI'), ('CHRISTINE', 'CHRIS', 'CHRISTY'),
             ('KIMBERLY', 'KIM'), ('PAMELA', 'PAM'), ('SAMANTHA', 'SAM'), ('ALEXANDRA', 'ALEX'), ('JACQUELINE', 'JACKIE'),
             ('ABIGAIL', 'ABBY'), ('MARIA', 'MARIE'), ('ANTONIO', 'TONY'), ('FRANCISCO', 'FRANK', 'PACO'), ('FRANCIS', 'FRANK'),
             ('JOSE', 'PEPE'), ('ALEJANDRO', 'ALEX'), ('GREGORY', 'GREG'), ('JEFFREY', 'JEFF'), ('TIMOTHY', 'TIM'), ('ANDREW', 'ANDY', 'DREW'),
             ('JONATHAN', 'JON'), ('NATHANIEL', 'NATE', 'NATHAN'), ('ZACHARY', 'ZACH'), ('JOSHUA', 'JOSH'), ('ALBERT', 'AL'),
             ('ARTHUR', 'ART'), ('EUGENE', 'GENE'), ('HAROLD', 'HAL', 'HARRY'), ('HENRY', 'HANK', 'HARRY'), ('WALTER', 'WALT'),
             ('THEODORE', 'TED', 'TEDDY'), ('CYNTHIA', 'CINDY'), ('SANDRA', 'SANDY'), ('TERESA', 'TERRY'), ('THERESA', 'TERRY'),
             ('LINDA', 'LINDY'), ('CAROLYN', 'CAROL'), ('VIRGINIA', 'GINNY'), ('JUDITH', 'JUDY'), ('FREDERICK', 'FRED'), ('LEONARD', 'LEN')]
_NICK = defaultdict(set)
for _group in NICKNAMES:
    for _n in _group: _NICK[_n].update(_group)
ADDRESS_WORDS = {'STREET': 'ST', 'AVENUE': 'AVE', 'AV': 'AVE', 'ROAD': 'RD', 'DRIVE': 'DR', 'LANE': 'LN', 'PLACE': 'PL', 'COURT': 'CT',
                 'BOULEVARD': 'BLVD', 'CIRCLE': 'CIR', 'TERRACE': 'TER', 'PARKWAY': 'PKWY', 'HIGHWAY': 'HWY', 'APARTMENT': 'APT',
                 'UNIT': 'APT', 'SUITE': 'STE', '#': 'APT', 'NORTH': 'N', 'SOUTH': 'S', 'EAST': 'E', 'WEST': 'W'}


# ---- normalisation --------------------------------------------------------------------------------------------------------
def _clean(s):
    return re.sub(r'[^A-Z ]', '', re.sub(r'\.', ' ', str(s or '').upper().replace('-', ''))).split()  # hyphenated surnames stay one token


def split_name(display_name):
    """'Last, First M' or 'First M Last' -> (first, last), upper case, punctuation removed; suffixes dropped."""
    raw = str(display_name or '')
    if ',' in raw:
        last, _, rest = raw.partition(',')
        first = _clean(rest)
        last = _clean(last)
    else:
        parts = [p for p in _clean(raw) if p not in ('JR', 'SR', 'II', 'III', 'IV', 'MD')]
        first, last = parts[:1], parts[1:][-1:] if len(parts) > 1 else []
    return (first[0] if first else ''), (''.join(last) if last else '')


def norm_phone(s):
    d = re.sub(r'\D', '', str(s or ''))
    return d[-10:] if len(d) >= 10 else ''


def norm_address(s):
    words = re.sub(r'[^A-Z0-9# ]', ' ', str(s or '').upper()).replace('#', ' # ').split()
    return ' '.join(ADDRESS_WORDS.get(w, w) for w in words)


def soundex(name):
    name = re.sub(r'[^A-Z]', '', str(name or '').upper())
    if not name: return ''
    codes = {**dict.fromkeys('BFPV', '1'), **dict.fromkeys('CGJKQSXZ', '2'), **dict.fromkeys('DT', '3'), 'L': '4',
             **dict.fromkeys('MN', '5'), 'R': '6'}
    out, last = name[0], codes.get(name[0], '')
    for ch in name[1:]:
        c = codes.get(ch, '')
        if c and c != last: out += c
        if ch not in 'HW': last = c
    return (out + '000')[:4]


def jaro_winkler(a, b, p=0.1):
    if not a or not b: return 0.0
    if a == b: return 1.0
    la, lb = len(a), len(b)
    window = max(la, lb) // 2 - 1
    ma, mb = [False] * la, [False] * lb
    matches = 0
    for i, ch in enumerate(a):
        for j in range(max(0, i - window), min(lb, i + window + 1)):
            if not mb[j] and b[j] == ch:
                ma[i] = mb[j] = True; matches += 1; break
    if not matches: return 0.0
    t, k = 0, 0
    for i in range(la):
        if ma[i]:
            while not mb[k]: k += 1
            if a[i] != b[k]: t += 1
            k += 1
    jaro = (matches / la + matches / lb + (matches - t / 2) / matches) / 3
    prefix = 0
    for x, y in zip(a[:4], b[:4]):
        if x != y: break
        prefix += 1
    return jaro + prefix * p * (1 - jaro)


# ---- identities ---------------------------------------------------------------------------------------------------------------
def _ro(path):
    try:
        db = sqlite3.connect(f'file:{path}?mode=ro', uri=True); db.execute('SELECT 1 FROM sqlite_master LIMIT 1'); return db
    except sqlite3.Error:
        return sqlite3.connect(f'file:{path}?immutable=1', uri=True)  # read-only directory: a WAL database still opens


def identity(pid, payload):
    first, last = split_name(payload.get('display_name') or ' '.join(x for x in (payload.get('first_name'), payload.get('last_name')) if x))
    return {'patient_id': pid, 'first': first, 'last': last, 'birth_date': str(payload.get('birth_date') or '')[:10],
            'sex': str(payload.get('sex_reported') or payload.get('sex') or '')[:1].upper(), 'address': norm_address(payload.get('address_line')),
            'postal': str(payload.get('postal_code') or payload.get('zip') or '')[:5], 'phone': norm_phone(payload.get('phone')),
            'member_ids': set(), 'mrns': set()}


def load_identities(stack_dir, semantic_db=None):
    """[identity] from the record store's patient rows (stack.sqlite3 patients) plus payer member IDs from the semantic layer."""
    stack_dir = Path(stack_dir)
    db = _ro(stack_dir / 'stack.sqlite3')
    try:
        rows = db.execute('SELECT id, payload FROM patients').fetchall()
    finally:
        db.close()
    ids = {pid: identity(pid, json.loads(p or '{}')) for pid, p in rows}
    if semantic_db is not None:
        tables = {r[0] for r in semantic_db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        sources = [('enrollment_span', 'payer_id')] + ([('payer_claim', 'payer_id')] if 'payer_claim' in tables else [])
        for table, payer_col in sources:
            if table not in tables: continue
            for pid, payer, mid in semantic_db.execute(f'SELECT DISTINCT patient_id, {payer_col}, member_id FROM {table} WHERE member_id IS NOT NULL'):
                if pid in ids and mid: ids[pid]['member_ids'].add(f'{payer}|{mid}')
        if 'record' in tables:  # facility MRNs bound to each chart by the received source records
            for pid, system, mrn in semantic_db.execute("SELECT DISTINCT patient_id, json_extract(payload,'$.source_system'), "
                                                        "json_extract(payload,'$.source_patient_id') FROM record "
                                                        "WHERE json_extract(payload,'$.source_patient_id') IS NOT NULL"):
                if pid in ids and mrn: ids[pid]['mrns'].add(f'{system or ""}|{mrn}')
    return list(ids.values())


# ---- comparison ---------------------------------------------------------------------------------------------------------------
def _name_level(a, b, jw):
    if not a or not b: return None
    if a == b: return 'agree'
    if b in _NICK.get(a, ()) or (len(a) == 1 or len(b) == 1) and a[0] == b[0]: return 'partial'
    return 'partial' if jaro_winkler(a, b) >= jw else 'disagree'


def _dob_level(a, b):
    if len(a) != 10 or len(b) != 10: return None
    if a == b: return 'agree'
    ya, ma, da = a[:4], a[5:7], a[8:10]
    yb, mb, db = b[:4], b[5:7], b[8:10]
    if ya == yb and ma == db and da == mb: return 'partial'  # month/day transposition
    same = (ya == yb) + (ma == mb) + (da == db)
    if same == 2:
        diff = [(x, y) for x, y in ((ya, yb), (ma, mb), (da, db)) if x != y][0]
        if sum(c1 != c2 for c1, c2 in zip(*diff)) == 1: return 'partial'  # single-digit keying error
    return 'disagree'


def _eq_level(a, b):
    if not a or not b: return None
    return 'agree' if a == b else 'disagree'


def _addr_level(a, b):
    if not a or not b: return None
    if a == b: return 'agree'
    na, nb = a.split(' ', 1)[0], b.split(' ', 1)[0]
    if na.isdigit() and nb.isdigit() and na != nb: return 'disagree'  # different house numbers: a neighbour, not a typo
    return 'partial' if jaro_winkler(a, b) >= 0.95 else 'disagree'


class Model:
    """u-probabilities estimated from the received charts."""
    def __init__(self, people, params=None):
        self.p = {**DEFAULTS, **(params or {})}
        n = max(len(people), 2)
        self.freq = {f: Counter(x[f] for x in people if x[f]) for f in ('first', 'last', 'birth_date', 'postal', 'address', 'phone', 'sex')}
        self.u = {}
        for f, c in self.freq.items():
            tot = sum(c.values()) or 1
            self.u[f] = min(max(sum((v / tot) ** 2 for v in c.values()), 1.0 / n), 0.5)
        self.u.update(self.p.get('u_override') or {})  # stated u-probabilities (small populations cannot estimate them)

    def _u_value(self, f, v):
        c = self.freq[f]
        tot = sum(c.values()) or 1
        return min(max(c.get(v, 1) / tot, 1e-6), 0.5)

    def weight(self, f, level, value=None):
        if level is None: return 0.0
        m = M[f]
        specific = self.p['value_specific'] and level == 'agree' and f in ('first', 'last', 'birth_date') and value and f not in (self.p.get('u_override') or {})
        u = self._u_value(f, value) if specific else self.u[f]
        agree = math.log2(m / u)
        if level == 'agree': return agree
        if level == 'partial': return agree / 2
        return math.log2((1 - m) / (1 - u))

    def compare(self, a, b):
        jw = self.p['partial_jw']
        levels = {'first': _name_level(a['first'], b['first'], jw), 'last': _name_level(a['last'], b['last'], jw),
                  'birth_date': _dob_level(a['birth_date'], b['birth_date']), 'sex': _eq_level(a['sex'], b['sex']),
                  'address': _addr_level(a['address'], b['address']), 'postal': _eq_level(a['postal'], b['postal']),
                  'phone': _eq_level(a['phone'], b['phone'])}
        # first/last swapped between charts: score the swapped orientation when it agrees better
        if levels['first'] == levels['last'] == 'disagree' and a['first'] == b['last'] and a['last'] == b['first']:
            levels['first'] = levels['last'] = 'partial'
        w = {f: round(self.weight(f, lv, a[f] if lv == 'agree' else None), 3) for f, lv in levels.items()}
        shared_member = bool(a['member_ids'] & b['member_ids'])
        shared_mrn = bool(a.get('mrns', set()) & b.get('mrns', set()))
        return {'levels': levels, 'weights': w, 'total': round(sum(w.values()), 3), 'shared_member_id': shared_member, 'shared_mrn': shared_mrn}


# ---- blocking and decision ----------------------------------------------------------------------------------------------------
def _blocks(x):
    keys = []
    if x['birth_date']: keys.append(('dob', x['birth_date']))
    if x['phone']: keys.append(('phone', x['phone']))
    if x['last'] and x['birth_date']: keys.append(('last_year', soundex(x['last']), x['birth_date'][:4]))
    if x['first'] and x['postal']: keys.append(('first_zip', soundex(x['first']), x['postal']))
    if x['address'] and x['postal']: keys.append(('addr', x['address'], x['postal']))
    if x['first'] and x['last']: keys.append(('names', soundex(x['first']), soundex(x['last'])))
    keys += [('member', m) for m in x['member_ids']]
    keys += [('mrn', m) for m in x.get('mrns', ())]
    return keys


def candidate_pairs(people, max_block):
    blocks = defaultdict(list)
    for i, x in enumerate(people):
        for k in _blocks(x): blocks[k].append(i)
    pairs, skipped = set(), 0
    for k, members in blocks.items():
        if len(members) < 2: continue
        if len(members) > max_block: skipped += 1; continue
        for a in range(len(members)):
            for b in range(a + 1, len(members)): pairs.add((members[a], members[b]))
    return pairs, skipped


def link(people, params=None):
    """Returns (assignments {patient_id: person_id} for charts in linked clusters, report)."""
    model = Model(people, params)
    p = model.p
    pairs, skipped = candidate_pairs(people, p['max_block'])
    parent = list(range(len(people)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    links, scored = [], 0
    for i, j in sorted(pairs):
        c = model.compare(people[i], people[j])
        scored += 1
        dob_ok = c['levels']['birth_date'] in ('agree', 'partial')
        by_member = p['member_id_link'] and c['shared_member_id'] and dob_ok
        by_mrn = p['mrn_link'] and c['shared_mrn'] and dob_ok
        deterministic = by_member or by_mrn
        if c['total'] >= p['threshold'] or deterministic:
            rule = 'fellegi_sunter' if c['total'] >= p['threshold'] else 'member_id' if by_member else 'mrn'
            links.append({'a': people[i]['patient_id'], 'b': people[j]['patient_id'], 'weight': c['total'], 'rule': rule})
            parent[find(i)] = find(j)
    clusters = defaultdict(list)
    for i in range(len(people)): clusters[find(i)].append(people[i]['patient_id'])
    out = {}
    for members in clusters.values():
        if len(members) < 2: continue
        person = 'AGP-' + min(members)
        for m in members: out[m] = person
    report = {'version': VERSION, 'parameters': p, 'charts': len(people), 'candidate_pairs': scored, 'oversized_blocks_skipped': skipped,
              'links': len(links), 'linked_charts': len(out), 'persons': len(set(out.values())),
              'u_probabilities': {k: round(v, 6) for k, v in model.u.items()}, 'm_probabilities': M}
    return out, report, links
