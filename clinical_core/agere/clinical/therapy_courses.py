"""Medication adherence per drug class and calendar year, with the therapy event that starts each course.

Built on the line-of-therapy engine (line_of_therapy.fill_rows / component_fills / episodes / derive_lines / pdc): fills
are received dispensing events only (pharmacy dispenses, provider pharmacy claims, paid payer pharmacy claims; B2 returns
to stock never count).

  PDC        Pharmacy Quality Alliance proportion of days covered: from the first fill of the class in the year to the
             end of the year (or death / snapshot), supply of overlapping fills shifted forward; fills from the prior year
             still supplying days count. Rows need >= min_fills_in_year fills of the class in the year.
  courses    a course of a class is a run of fills whose supply never lapses for course_gap_days or more. The default is the
             new-user washout (washout_days, 180): the new-user design (Ray, Am J Epidemiol 2003;158:915) calls a patient a
             new user only after a fill-free washout, so a refill inside that window continues the same course (a refill
             gap is non-adherence, which PDC measures) and only a resumption after a full washout is a new course. A course
             also ends at a documented stop that falls in a supply lapse (course_episodes).
  events     derive_lines per therapeutic area (AREAS: each class is assigned to one area so a class has one history),
             reported only for courses that start >= washout_days after the fill feed starts (before that, therapy already
             under way at the data start cannot be ruled out; the same washout as initiation, applied to every event):
               initiation  first course of the area's therapy for a new user
               add_on      a class starts while every current regimen component continues
               switch      a class starts and a regimen component ends within switch_window_days
               restart     a class resumes after a documented stop: a clinician's stop of the class in a note
                           (note_therapy_change action 'stop') or a discontinued medication-list entry, dated between the
                           previous course's first fill and the resumption. A supply lapse alone is non-persistence (PDC),
                           not an intentional stop: pharmacy data cannot tell a stop-and-restart from a refill gap.
             The event is attached to the class-year of the course start; first_fill = that class's first fill of the course
             (for a regimen started with several classes, each class's own first fill).
Drug classes are written with the scorer's canonical names where Agere's component name differs (OUTPUT_CLASS).
"""
from collections import defaultdict
from datetime import date

from . import line_of_therapy as lot

VERSION = 'therapy-courses/2'
AREAS = {
    'diabetes': ['metformin', 'sulfonylurea', 'dpp4_inhibitor', 'sglt2_inhibitor', 'glp1_agonist', 'thiazolidinedione', 'insulin'],
    'hypertension': ['ace_inhibitor', 'arb', 'arni', 'calcium_channel_blocker', 'thiazide_diuretic', 'beta_blocker', 'mra'],
    'lipids': ['statin'],
    'depression': ['ssri', 'snri', 'bupropion', 'mirtazapine', 'tricyclic_antidepressant'],
}
DEFAULTS = {'course_gap_days': None, 'regimen_window_days': 30, 'switch_window_days': 30, 'washout_days': 180, 'add_on_new_line': True,
            'max_lines': 8, 'min_fills_in_year': 1}
EVENT = {'first_line': 'initiation', 'add_on': 'add_on', 'switch': 'switch', 'restart': 'restart'}
OUTPUT_CLASS = {'bupropion': 'NDRI'}  # bupropion is a norepinephrine-dopamine reuptake inhibitor


def _course_start(episodes, day, window):
    """The start of the class's course that begins at the event (within the regimen window of the event day)."""
    starts = [e[0] for e in episodes if day <= e[0] <= day + window]
    return min(starts) if starts else day


def documented_stops(db, classes):
    """{(patient_id, class): sorted ordinal days} of documented stops (note therapy changes, discontinued list entries)."""
    import re
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    rx = {c: re.compile(lot.COMPONENTS[c], re.I) for c in classes if c in lot.COMPONENTS}
    out = defaultdict(set)

    def add(pid, day, cls, name):
        if not day: return
        for c, r in rx.items():
            if cls == c or r.search(name or ''): out[(pid, c)].add(lot._day(day))
    if 'note_therapy_change' in tables:
        for pid, day, cls, target in db.execute("SELECT patient_id, substr(event_date,1,10), drug_class, target FROM note_therapy_change WHERE action = 'stop'"):
            add(pid, day, cls, target)
    if 'medication_list_entry' in tables:
        for pid, day, name in db.execute("SELECT patient_id, substr(last_listed,1,10), name FROM medication_list_entry WHERE list_status = 'discontinued'"):
            add(pid, day, None, name)
    return {k: sorted(v) for k, v in out.items()}


def course_episodes(days, gap, stop_days=()):
    """Courses of one class: line_of_therapy episodes with gap = course gap, also split where a documented stop falls after
    a fill whose supply ran out before the next fill (the clinician stopped it; the next fill restarts it)."""
    order, groups, cur, cover = sorted(days), [], {}, None
    for d in order:
        if cur and cover is not None and d > cover + 1 and any(max(cur) <= x < d for x in stop_days):
            groups.append(cur); cur, cover = {}, None
        cur[d] = days[d]
        cover = d + days[d] - 1 if cover is None or d > cover else cover + days[d]
    if cur: groups.append(cur)
    return [e for g in groups for e in lot.episodes(g, gap)]


def courses(db, years, as_of, feed_start, deaths=None, params=None, keep=None):
    """[{'patient_id', 'drug_class', 'year', 'pdc', 'fills', 'therapy_event'?, 'first_fill'?}]."""
    p = {**DEFAULTS, **(params or {})}
    gap = p['course_gap_days'] if p['course_gap_days'] is not None else p['washout_days']
    rows, _ = lot.fill_rows(db, keep)
    classes = [c for comps in AREAS.values() for c in comps]
    fills = lot.component_fills(rows, classes)
    feed = lot._day(feed_start)
    as_of_day = lot._day(as_of)
    new_from = feed + p['washout_days']
    stops = documented_stops(db, classes)
    out = {}
    for pid, comp in fills.items():
        death = (deaths or {}).get(pid)
        stop = min(as_of_day, lot._day(death)) if death else as_of_day
        for cls, days in comp.items():
            for y in years:
                lo, hi = date(y, 1, 1).toordinal(), min(date(y, 12, 31).toordinal(), stop)
                inside = sorted(d for d in days if lo <= d <= hi)
                if len(inside) < max(1, p['min_fills_in_year']) or hi < lo: continue
                out[(pid, cls, y)] = {'patient_id': pid, 'drug_class': cls, 'year': y, 'pdc': round(lot.pdc(days, inside[0], hi), 4),
                                      'fills': len(inside)}
        for area, comps in AREAS.items():
            eps = {c: course_episodes(comp[c], gap, stops.get((pid, c), ())) for c in comps if c in comp}
            if not eps: continue
            _, events = lot.derive_lines(eps, {**p, 'gap_days': gap}, as_of_day)
            for ev in events:
                kind = EVENT.get(ev['kind'])
                if not kind: continue
                for c in ev['components']:
                    start = _course_start(eps[c], ev['day'], p['regimen_window_days'])
                    if start < new_from: continue  # course under way at the data start cannot be ruled out
                    if kind == 'restart':
                        prior = [e[0] for e in eps[c] if e[0] < start]
                        if not prior or not any(max(prior) <= d <= start for d in stops.get((pid, c), ())): continue
                    y = date.fromordinal(start).year
                    if y not in years: continue
                    key = (pid, c, y)
                    row = out.get(key)
                    if row is None:
                        hi = min(date(y, 12, 31).toordinal(), stop)
                        row = out[key] = {'patient_id': pid, 'drug_class': c, 'year': y,
                                          'pdc': round(lot.pdc(comp[c], start, hi), 4) if hi >= start else None, 'fills': 0}
                    if 'therapy_event' not in row:  # the first event of the class in the year
                        row['therapy_event'] = kind
                        row['first_fill'] = date.fromordinal(start).isoformat()
    for r in out.values():
        r['drug_class'] = OUTPUT_CLASS.get(r['drug_class'], r['drug_class'])
    return sorted(out.values(), key=lambda r: (r['patient_id'], r['drug_class'], r['year']))
