"""clinical_trial_v1 reference answers (2026-11-17, written before any arm ran). Raw warehouse tables only.

Questions a sepsis readmission committee, a heart-failure program and a value-based care team ask. The hospital's
own certified definitions (proof_v14_customer_definitions.json) govern: their helpers are reused from
proof_v14_references.py (inpatient stays at our four hospitals, merged per encounter; 30-day readmission index; sepsis
by any EHR diagnosis A40/A41/R65.2; heart failure by principal diagnosis I50; counted payer claims; member months).
New here: sepsis and heart-failure readmission views, post-discharge labs and fills, spend concentration, frequent ED use.
Pharmacy fills are the health plan's paid pharmacy claims (members only).
"""
import importlib.util
import statistics
from collections import Counter, defaultdict
from pathlib import Path

_spec = importlib.util.spec_from_file_location('proof_v14_refs', Path(__file__).with_name('proof_v14_references.py'))
_v14 = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_v14)
for _name, _fn in vars(_v14).items():
    if _name.startswith('ref_') or _name.startswith('_') and callable(_fn) and not _name.startswith('__'):
        globals()[_name] = _fn
_d, _in, _rate, _inpatient, _readmit_index, _sepsis, _hf, _dgroup, _rx, _mm, _member_events = (
    _v14._d, _v14._in, _v14._rate, _v14._inpatient, _v14._readmit_index, _v14._sepsis, _v14._hf, _v14._dgroup, _v14._rx, _v14._mm, _v14._member_events)

EB_BETA_BLOCKER = ('carvedilol', 'metoprolol succinate', 'bisoprolol')
SGLT2 = ('dapagliflozin', 'empagliflozin', 'sotagliflozin', 'canagliflozin', 'ertugliflozin')


def _index_for(db, ids):
    return [s for s in _readmit_index(db) if s['id'] in ids]


def _readmissions_after(db, index):
    """index stay id -> the readmission stay (the first stay starting 1-30 days after its discharge)."""
    by_pt = defaultdict(list)
    for s in _inpatient(db): by_pt[s['patient']].append(s)
    out = {}
    for s in index:
        if not s['readmit']: continue
        nxt = [o for o in by_pt[s['patient']] if o['id'] != s['id'] and 1 <= (_d(o['start']) - _d(s['end'])).days <= 30]
        out[s['id']] = min(nxt, key=lambda o: o['start'])
    return out


# ---- readmission committee
def ref_readmit_rate_by_disposition(db, year):
    g = defaultdict(list)
    for s in _readmit_index(db):
        if _in(s['end'], year): g[_dgroup(s['disp'])].append(s['readmit'])
    return [[k, _rate(sum(v), len(v))] for k, v in sorted(g.items())]


# ---- sepsis
def _sepsis_index(db, start, end):
    ids = {s['id'] for s in _sepsis(db)}
    return [s for s in _index_for(db, ids) if _in(s['end'], start, end)]


def ref_sepsis_readmit_rate(db, start, end):
    v = _sepsis_index(db, start, end)
    return [[_rate(sum(s['readmit'] for s in v), len(v))]]


def ref_sepsis_readmit_count(db, start, end):
    return [[sum(s['readmit'] for s in _sepsis_index(db, start, end))]]


def ref_sepsis_readmit_again_sepsis_share(db, start, end):
    ids = {s['id'] for s in _sepsis(db)}
    after = _readmissions_after(db, _sepsis_index(db, start, end))
    return [[_rate(sum(1 for r in after.values() if r['id'] in ids), len(after))]]


def ref_sepsis_median_days_to_readmit(db, start, end):
    v = [s['days_to'] for s in _sepsis_index(db, start, end) if s['readmit']]
    return [[round(float(statistics.median(v)), 1)]]


def ref_sepsis_disposition_mix(db, year):
    c = Counter(_dgroup(s['disp']) for s in _sepsis(db) if _in(s['end'], year))
    return [[k, c[k]] for k in sorted(c)]


def ref_sepsis_by_hospital(db, year):
    c = Counter(s['org'] for s in _sepsis(db) if _in(s['start'], year))
    return [[k, c[k]] for k in sorted(c)]


# ---- heart failure
def _hf_index(db, year):
    ids = {s['id'] for s in _hf(db)}
    return [s for s in _index_for(db, ids) if _in(s['end'], year)]


def ref_hf_repeat_patients(db, year, n=2):
    c = Counter(s['patient'] for s in _hf(db) if _in(s['start'], year))
    return [[sum(1 for v in c.values() if v >= int(n))]]


def ref_hf_readmit_rate_by_hospital(db, year):
    g = defaultdict(list)
    for s in _hf_index(db, year): g[s['org']].append(s['readmit'])
    return [[k, _rate(sum(v), len(v))] for k, v in sorted(g.items())]


def ref_hf_potassium_within(db, year, days=14):
    labs = defaultdict(list)
    for pid, ev in db.execute("SELECT patient_id, event_date FROM measurement WHERE concept = 'potassium' AND source_table = 'labs'"):
        labs[pid].append(ev[:10])
    v = _hf_index(db, year)   # live discharges (the index excludes deaths)
    hit = sum(1 for s in v if any(0 <= (_d(x) - _d(s['end'])).days <= int(days) for x in labs[s['patient']]))
    return [[_rate(hit, len(v))]]


def _fill_within(db, year, names, days=30):
    fills = defaultdict(list)
    for r in _rx(db):
        if r['status'] == 'paid' and any(n in (r['drug'] or '').lower() for n in names): fills[r['pid']].append(r['fill'][:10])
    v = _hf_index(db, year)
    hit = sum(1 for s in v if any(0 <= (_d(x) - _d(s['end'])).days <= int(days) for x in fills[s['patient']]))
    return [[_rate(hit, len(v))]]


def ref_hf_beta_blocker_fill_30d(db, year):
    return _fill_within(db, year, EB_BETA_BLOCKER)


def ref_hf_sglt2_fill_30d(db, year):
    return _fill_within(db, year, SGLT2)


# ---- value-based care
def ref_top5pct_spend_share(db, year):
    members = {p for (p, m) in _mm(db) if m[:4] == str(year)}
    paid = defaultdict(int)
    for pid, sf, c in _v14._counted_claims(db, 'patient_id, service_from, paid_cents'):
        if _in(sf, year) and pid in members: paid[pid] += c or 0
    vals = sorted((paid.get(p, 0) for p in members), reverse=True)
    k = max(1, round(len(vals) * 0.05))
    return [[_rate(sum(vals[:k]), sum(vals))]]


def ref_frequent_ed_members(db, year, n=4):
    _, ed = _member_events(db)
    members = {p for (p, m) in _mm(db) if m[:4] == str(year)}
    c = Counter(p for p, dt in ed if _in(dt, year) and p in members)
    return [[sum(1 for v in c.values() if v >= int(n))]]
