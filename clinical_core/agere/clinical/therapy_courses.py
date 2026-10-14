"""Medication adherence per drug class and calendar year, with the therapy event that starts each course.

Built on the line-of-therapy engine (line_of_therapy.fill_rows / component_fills / episodes / derive_lines / pdc): fills
are received dispensing events only (pharmacy dispenses, provider pharmacy claims, paid payer pharmacy claims; B2 returns
to stock never count).

  PDC        Pharmacy Quality Alliance proportion of days covered: from the first fill of the class in the year to the
             end of the year (or death / snapshot), supply of overlapping fills shifted forward; fills from the prior year
             still supplying days count. Rows need >= min_fills_in_year fills of the class in the year.
  events     derive_lines per therapeutic area (AREAS: each class is assigned to one area so a class has one history):
               initiation  first course of the area's therapy for a new user (first fill >= washout_days after the feed
                           start; earlier therapy cannot be ruled out before that)
               add_on      a class starts while every current regimen component continues
               switch      a class starts and a regimen component ends within switch_window_days
               restart     a regimen component resumes after a gap > gap_days
             The event is attached to the class-year of its date with first_fill = the course's first fill.
"""
from collections import defaultdict
from datetime import date

from . import line_of_therapy as lot

VERSION = 'therapy-courses/1'
AREAS = {
    'diabetes': ['metformin', 'sulfonylurea', 'dpp4_inhibitor', 'sglt2_inhibitor', 'glp1_agonist', 'thiazolidinedione', 'insulin'],
    'hypertension': ['ace_inhibitor', 'arb', 'arni', 'calcium_channel_blocker', 'thiazide_diuretic', 'beta_blocker', 'mra'],
    'lipids': ['statin'],
    'depression': ['ssri', 'snri', 'bupropion', 'mirtazapine', 'tricyclic_antidepressant'],
}
DEFAULTS = {'gap_days': 60, 'regimen_window_days': 30, 'switch_window_days': 30, 'washout_days': 180, 'add_on_new_line': True,
            'max_lines': 8, 'min_fills_in_year': 1}
EVENT = {'first_line': 'initiation', 'add_on': 'add_on', 'switch': 'switch', 'restart': 'restart'}


def courses(db, years, as_of, feed_start, deaths=None, params=None, keep=None):
    """[{'patient_id', 'drug_class', 'year', 'pdc', 'fills', 'therapy_event'?, 'first_fill'?}]."""
    p = {**DEFAULTS, **(params or {})}
    rows, _ = lot.fill_rows(db, keep)
    classes = [c for comps in AREAS.values() for c in comps]
    fills = lot.component_fills(rows, classes)
    feed = lot._day(feed_start)
    as_of_day = lot._day(as_of)
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
            eps = {c: lot.episodes(comp[c], p['gap_days']) for c in comps if c in comp}
            if not eps: continue
            lines, events = lot.derive_lines(eps, p, as_of_day)
            first = min(e[0][0] for e in eps.values())
            new_user = first >= feed + p['washout_days']
            for ev in events:
                kind = EVENT.get(ev['kind'])
                if not kind or (kind == 'initiation' and not new_user): continue
                y = date.fromordinal(ev['day']).year
                if y not in years: continue
                for c in ev['components']:
                    key = (pid, c, y)
                    row = out.get(key)
                    if row is None:
                        hi = min(date(y, 12, 31).toordinal(), stop)
                        row = out[key] = {'patient_id': pid, 'drug_class': c, 'year': y,
                                          'pdc': round(lot.pdc(comp[c], ev['day'], hi), 4) if hi >= ev['day'] else None, 'fills': 0}
                    if 'therapy_event' not in row:  # the first event of the class in the year
                        row['therapy_event'] = kind
                        row['first_fill'] = date.fromordinal(ev['day']).isoformat()
    return sorted(out.values(), key=lambda r: (r['patient_id'], r['drug_class'], r['year']))
