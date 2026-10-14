"""Clinical CMS-HCC V28 categories per patient-year with recapture: what the patient has, not only what was billed.

Uses the risk-adjustment engine's V28 crosswalk, age/sex edits and hierarchies (risk_adjustment.load_model / ccs_for /
apply_hierarchy). For each in-scope patient-year:

  captured     ICD-10 codes dated in the year on any received source (paid payer claims, the client's paid claims, EHR
               visit diagnoses, condition records). claims_hccs keeps the paid-claim subset for comparison.
  recaptured   codes for chronic conditions documented in an earlier year (since the feed start) that were not re-coded
               this year. A chronic condition does not resolve because a year passed without a claim; this is the
               recapture worklist of MA/ACO programs (CMS requires annual re-documentation, hence the gap).
               Acute categories are never carried (ACUTE_HCC: sepsis, acute MI, stroke, fractures, acute respiratory
               failure, ...); acute heart failure is carried as chronic heart failure (I50.9) and diabetes with acute
               complications as diabetes (E11.9). carry_years limits how far back a code may come from (None = no limit).
  suspected    suspect conditions (condition_suspects) whose evidence was complete by the end of the year.

The union is mapped once, so the V28 hierarchy resolves overlaps (e.g. carried E11.9 under a current E11.22 -> HCC 37 only).
A patient-year is in scope when the patient is enrolled in any month of the year or has any dated diagnosis that year,
and was alive on January 1. Age for the model edits is age on December 31.
"""
from collections import defaultdict
from datetime import date
from functools import lru_cache

from . import risk_adjustment as ra

VERSION = 'hcc-capture/1'
DEFAULTS = {'carry_forward': True, 'carry_years': None, 'include_suspects': True}
ACUTE_HCC = {2, 6, 36, 78, 92, 195, 202, 212, 213, 224, 225, 228, 229, 248, 249, 267, 282, 283, 385, 397, 398, 399, 401, 402, 405}
CARRY_AS = {224: 'I509', 225: 'I509', 36: 'E119'}


def _age(birth, when):
    if not birth: return None
    b = date.fromisoformat(birth[:10])
    return when.year - b.year - ((when.month, when.day) < (b.month, b.day))


def carry_code(model, code, age, sex):
    """The code carried into a later year, or None for an acute-only code."""
    return _carry(model['name'], code, age, sex)


@lru_cache(maxsize=200000)
def _carry(name, code, age, sex):
    model = ra.load_model(name)
    hccs = set(ra.ccs_for(model, [code], age, sex)) & model['payment']
    if not hccs: return None
    if not hccs & ACUTE_HCC: return code
    for h in sorted(hccs):
        if h in CARRY_AS: return CARRY_AS[h]
    return None


def capture(rows, people, years, feed_start, suspects=None, enrolled_years=None, deaths=None, params=None):
    """rows: iterable of (pid, icd10 key, date or None, kind) from chronic_conditions.diagnosis_rows.
    people: {pid: {'birth_date', 'sex' (1 male, 2 female), 'age'}}. suspects: {pid: [{'code', 'first_evidence'}]}.
    enrolled_years: {pid: set(years)}. deaths: {pid: 'YYYY-MM-DD'}.
    Returns {(pid, year): {'hccs', 'claims_hccs', 'recaptured', 'suspected'}}."""
    p = {**DEFAULTS, **(params or {})}
    model = ra.load_model('V28')
    by = defaultdict(lambda: defaultdict(list))  # pid -> year -> [(code, kind)]
    active_years = defaultdict(set)
    for pid, code, day, kind in rows:
        y = int(day[:4]) if day and day[:4].isdigit() else int(feed_start[:4])
        if day and day < feed_start: y = int(feed_start[:4])
        by[pid][y].append((code, kind))
        if day: active_years[pid].add(int(day[:4]))
    out = {}
    pids = set(by) | set(enrolled_years or {}) | set(suspects or {})
    for pid in pids:
        info = people.get(pid) or {}
        sex = info.get('sex') or 1
        scope = set(active_years.get(pid, ())) | set((enrolled_years or {}).get(pid, ()))
        death = (deaths or {}).get(pid)
        for y in years:
            if y not in scope: continue
            if death and death < f'{y}-01-01': continue
            age = _age(info.get('birth_date'), date(y, 12, 31))
            if age is None: age = info.get('age') or 70
            current = sorted({c for c, _ in by[pid].get(y, [])})
            claims = sorted({c for c, k in by[pid].get(y, []) if k.startswith('claim')})
            carried = set()
            if p['carry_forward']:
                lo = y - p['carry_years'] if p['carry_years'] else -10 ** 6
                for yy, items in by[pid].items():
                    if lo <= yy < y:
                        for c in {c for c, _ in items}:
                            cc = carry_code(model, c, age, sex)
                            if cc: carried.add(cc)
            carried = sorted(carried)
            sus = []
            if p['include_suspects']:
                sus = [s['code'] for s in (suspects or {}).get(pid, []) if s.get('first_evidence') and s['first_evidence'] <= f'{y}-12-31']
            base = set(ra._model_hccs(model, current, age, sex))
            with_carry = set(ra._model_hccs(model, current + carried, age, sex))
            final = set(ra._model_hccs(model, current + carried + sus, age, sex))
            if not final: continue
            out[(pid, y)] = {'hccs': sorted(final), 'claims_hccs': sorted(ra._model_hccs(model, claims, age, sex)),
                             'recaptured': sorted(with_carry - base), 'suspected': sorted(final - with_carry)}
    return out
