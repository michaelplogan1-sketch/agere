"""Workspace history for the simulator, read once from the semantic layer (read-only) and held in memory.

    h = History.load(semantic_path)

Streams (whatever the layer carries; absent tables leave the stream empty):
  patient          birth date, sex
  member_month     enrolled months (YYYY-MM) with line of business and attribution practice (exposure and contract population)
  utilization_event admissions (kind inpatient) and ED visits (kind ed_visit) with dates
  payer_claim      paid amount (counts_in_metrics = 1), service date and principal diagnosis
  condition        chronic condition categories and HF diagnosis dates
  death            death dates

Each admission / ED visit is costed from the paid claims of that event (inpatient: service dates within the stay, then ED:
claims on the visit date not already taken by a stay) and tagged with the principal diagnoses of those claims; every other
paid claim is "other" spending. So an avoided event removes exactly the spending the workspace attached to it.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import date
from pathlib import Path
import sqlite3
import threading

_CACHE, _GUARD = {}, threading.Lock()


def day(s):
    return date.fromisoformat(str(s)[:10]).toordinal()


def month_key(d):
    x = date.fromordinal(int(d))
    return f'{x.year:04d}-{x.month:02d}'


def add_months(d, n):
    """ordinal day d shifted by n calendar months (day clipped to 28 to stay valid)."""
    x = date.fromordinal(int(d))
    m = x.month - 1 + n
    y, m = x.year + m // 12, m % 12 + 1
    return date(y, m, min(x.day, 28)).toordinal()


def months_between(a, b):
    """['YYYY-MM', ...] for months whose first day falls in [a, b) (ordinal days)."""
    out = []
    x = date.fromordinal(int(a))
    cur = date(x.year, x.month, 1)
    if cur.toordinal() < a:
        cur = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
    while cur.toordinal() < b:
        out.append(f'{cur.year:04d}-{cur.month:02d}')
        cur = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
    return out


class History:
    def __init__(self):
        self.birth, self.sex = {}, {}
        self.months = defaultdict(dict)          # pid -> {'YYYY-MM': (lob, practice)}
        self.events = defaultdict(list)          # pid -> [(day, kind, cost, (principal dx, ...))]
        self.other = defaultdict(list)           # pid -> [(day, paid, principal dx)] claims not attached to an event
        self.chronic = defaultdict(list)         # pid -> [(day, category)]
        self.hf_dx = {}                          # pid -> first I50 day
        self.death = {}
        self.data_start = self.data_end = None
        self.tables = set()

    @classmethod
    def load(cls, path):
        path = Path(path)
        st = path.stat()
        key = (str(path.resolve()), st.st_mtime_ns, st.st_size)
        with _GUARD:
            if key in _CACHE:
                return _CACHE[key]
        h = cls()
        db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        try:
            h._read(db)
        finally:
            db.close()
        with _GUARD:
            _CACHE.clear()
            _CACHE[key] = h
        return h

    def _cols(self, db, t):
        return {r[1] for r in db.execute(f'PRAGMA table_info({t})')}

    def _read(self, db):
        self.tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        meta = dict(db.execute('SELECT key, value FROM meta').fetchall()) if 'meta' in self.tables else {}
        self.meta_as_of = meta.get('as_of')
        if 'patient' in self.tables:
            cols = self._cols(db, 'patient')
            sex = 'sex_reported' if 'sex_reported' in cols else ('sex' if 'sex' in cols else None)
            for pid, b, s in db.execute(f"SELECT patient_id, birth_date, {sex or 'NULL'} FROM patient"):
                if b:
                    try:
                        self.birth[pid] = day(b)
                    except ValueError:
                        pass
                self.sex[pid] = (s or '').upper()[:1]
        if 'member_month' in self.tables:
            cols = self._cols(db, 'member_month')
            prac = 'attribution_practice_id' if 'attribution_practice_id' in cols else 'NULL'
            for pid, m, lob, pr in db.execute(f'SELECT patient_id, month, line_of_business, {prac} FROM member_month'):
                if pid and m:
                    self.months[pid][str(m)[:7]] = ((lob or '').lower(), pr)
        if 'death' in self.tables:
            for pid, d in db.execute('SELECT patient_id, death_date FROM death WHERE death_date IS NOT NULL'):
                try:
                    self.death[pid] = day(d)
                except ValueError:
                    pass
        if 'condition' in self.tables:
            cols = self._cols(db, 'condition')
            cat = 'icd10_category' if 'icd10_category' in cols else "substr(REPLACE(icd10,'.',''),1,3)"
            chronic = 'chronic' if 'chronic' in cols else '1'
            for pid, d, c, ch, icd in db.execute(f'SELECT patient_id, event_date, {cat}, {chronic}, icd10 FROM condition'):
                if not pid or not d:
                    continue
                try:
                    dd = day(d)
                except ValueError:
                    continue
                code = str(icd or '').upper().replace('.', '')
                if code.startswith('I50'):
                    self.hf_dx[pid] = min(dd, self.hf_dx.get(pid, dd))
                if ch and c:
                    self.chronic[pid].append((dd, c))
        claims = defaultdict(list)
        if 'payer_claim' in self.tables:
            cols = self._cols(db, 'payer_claim')
            if {'paid_cents', 'service_from'} <= cols:
                live = 'counts_in_metrics = 1' if 'counts_in_metrics' in cols else '1'
                pdx = 'principal_diagnosis' if 'principal_diagnosis' in cols else 'NULL'
                for pid, d, paid, dx in db.execute(f'SELECT patient_id, service_from, paid_cents, {pdx} FROM payer_claim WHERE {live} '
                                                   'AND service_from IS NOT NULL'):
                    try:
                        claims[pid].append((day(d), (paid or 0) / 100.0, str(dx or '').upper().replace('.', '')))
                    except ValueError:
                        continue
        raw_events = defaultdict(list)
        if 'utilization_event' in self.tables:
            for pid, kind, d, e in db.execute("SELECT patient_id, kind, event_date, end_date FROM utilization_event "
                                              "WHERE kind IN ('inpatient', 'ed_visit') AND event_date IS NOT NULL"):
                try:
                    s = day(d)
                    en = day(e) if e else s
                except ValueError:
                    continue
                raw_events[pid].append((s, max(en, s), 'admission' if kind == 'inpatient' else 'ed'))
        days_seen = []
        for pid in set(claims) | set(raw_events):
            cl = sorted(claims.get(pid, []))
            cdays = [c[0] for c in cl]
            taken = set()
            evs = sorted(raw_events.get(pid, []), key=lambda e: (e[2] != 'admission', e[0]))  # stays first
            for s, en, kind in evs:
                lo, hi = bisect_left(cdays, s), bisect_right(cdays, en if kind == 'admission' else s)
                idx = [i for i in range(lo, hi) if i not in taken]
                taken.update(idx)
                self.events[pid].append((s, kind, sum(cl[i][1] for i in idx), tuple(sorted({cl[i][2] for i in idx if cl[i][2]}))))
            self.events[pid].sort()
            self.other[pid] = [cl[i] for i in range(len(cl)) if i not in taken]
            if cdays:
                days_seen += [cdays[0], cdays[-1]]
            if evs:
                days_seen += [min(e[0] for e in evs), max(e[0] for e in evs)]
        per = {}
        for v in self.months.values():
            for m in v:
                per[m] = per.get(m, 0) + 1
        if per:  # first month with at least half the median membership (a partial first delivery month is skipped)
            med = sorted(per.values())[len(per) // 2]
            first = min(m for m, c in per.items() if c >= 0.5 * med)
            self.data_start = day(first + '-01')
        elif days_seen:
            self.data_start = min(days_seen)
        if self.meta_as_of:
            self.data_end = day(self.meta_as_of)
        elif days_seen:
            self.data_end = max(days_seen)

    # --- per-patient queries --------------------------------------------------------------------------------------------

    def enrolled_months(self, pid, a, b):
        mm = self.months.get(pid)
        if not mm:
            return 0
        return sum(1 for m in months_between(a, b) if m in mm)

    def enrolled_at(self, pid, d):
        mm = self.months.get(pid) or {}
        k = month_key(d)
        prev = month_key(add_months(d, -1))
        return k in mm or prev in mm

    def lob_at(self, pid, d):
        mm = self.months.get(pid) or {}
        for k in (month_key(d), month_key(add_months(d, -1))):
            if k in mm:
                return mm[k][0]
        if mm:
            return mm[sorted(mm)[-1]][0]
        return ''

    def practice_at(self, pid, d):
        mm = self.months.get(pid) or {}
        for k in (month_key(d), month_key(add_months(d, -1))):
            if k in mm:
                return mm[k][1]
        return None

    def window(self, pid, a, b):
        """(admissions, ed visits, event cost, other cost) with dates in [a, b)."""
        adm = ed = 0
        ecost = ocost = 0.0
        for d, kind, cost, _ in self.events.get(pid, ()):
            if a <= d < b:
                ecost += cost
                if kind == 'admission':
                    adm += 1
                else:
                    ed += 1
        for d, paid, _ in self.other.get(pid, ()):
            if a <= d < b:
                ocost += paid
        return adm, ed, ecost, ocost

    def related_other(self, pid, a, b, prefixes):
        return sum(p for d, p, dx in self.other.get(pid, ()) if a <= d < b and dx.startswith(tuple(prefixes)))
