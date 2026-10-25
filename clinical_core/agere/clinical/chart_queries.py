"""Deterministic readers of one patient's structured chart (the executors behind chart_reader).

Every function takes a Chart (a read-only connection to semantic.sqlite3, one patient, the as-of date) and an Intent
(chart_intents.parse) and returns a Core dict:

  answer_type  value | count | date | yes_no | list | label | table | summary | none
  value, unit  the number (or bool, or text) the answer commits to, with its unit
  count        the integer for a count answer
  headline     the one-line answer with dates and units
  table        rows (list of dicts) behind the answer
  sources      [{'table', 'record_id', 'date', ...}] the warehouse rows used
  facts        small dict of the computed numbers (for the summary and tests)
  notes        plain statements of the definition applied (window, latest-vs-any, which source)

Definitions come from decision_context (living filter does NOT apply to a named patient), concepts (condition evidence) and the
dev-set definitions Mike decided on 2026-10-23: latest lab = measurement_latest; any result = measurement with current usable
values; ED / inpatient / observation = utilization_event (all sources); spend = payer_claim with counts_in_metrics = 1 by service_from;
fill = the merged fill table; a patient is "on" a drug when it is on the active medication list or was filled in the last 12 months.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date

from . import chart_intents as CI
from . import decision_context as DC
from .age import age_at

VERSION = 'chart-queries/1'
MAX_SOURCES = 40
UTIL_KIND = {'ed_visit': 'ed_visit', 'inpatient': 'inpatient', 'observation': 'observation'}
UTIL_LABEL = {'ed_visit': 'ED visit', 'inpatient': 'hospital admission', 'observation': 'observation stay'}
ANY_LAB_WHERE = ("value_canonical IS NOT NULL AND result_current = 1 AND excluded_reason IS NULL "
                 "AND source_table NOT IN ('claim_line', 'payer_claim_line')")
OUTPATIENT_TYPES = ('primary_care_visit', 'specialist_visit', 'telehealth_contact', 'urgent_care_visit')
HIGH_SVI = 75.0
STEMS = {'statin': r'statin\b', 'ace_inhibitor': r'pril\b', 'arb': r'sartan\b', 'beta_blocker': r'olol\b|carvedilol', 'sglt2_inhibitor': r'gliflozin',
         'glp1_agonist': r'glutide|exenatide|tirzepatide', 'calcium_channel_blocker': r'dipine\b|diltiazem|verapamil', 'loop_diuretic': r'furosemide|torsemide|bumetanide',
         'anticoagulant': r'warfarin|apixaban|rivaroxaban|dabigatran|edoxaban', 'mra': r'spironolactone|eplerenone', 'arni': r'sacubitril',
         'metformin': r'metformin', 'insulin': r'insulin', 'opioid': r'oxycodone|morphine|hydrocodone|fentanyl|tramadol|codeine|hydromorphone',
         'proton_pump_inhibitor': r'prazole\b', 'thiazide_diuretic': r'thiazide|chlorthalidone'}


class Chart:
    """One patient's read-only view of the semantic layer."""

    def __init__(self, db, patient_id, as_of, lexicon=None):
        self.db, self.pid, self.as_of = db, patient_id, str(as_of)[:10]
        self.lexicon = lexicon or {}
        self.db.row_factory = sqlite3.Row
        self._cache = {}

    def rows(self, sql, *params):
        return [dict(r) for r in self.db.execute(sql, params)]

    def one(self, sql, *params):
        r = self.db.execute(sql, params).fetchone()
        return dict(r) if r is not None else None

    def columns(self, table):
        if table not in self._cache:
            self._cache[table] = [r[1] for r in self.db.execute(f'PRAGMA table_info({table})')]
        return self._cache[table]

    @property
    def patient(self):
        if 'patient' not in self._cache:
            self._cache['patient'] = self.one('SELECT * FROM patient WHERE patient_id = ?', self.pid)
        return self._cache['patient']

    @property
    def death(self):
        if 'death' not in self._cache:
            self._cache['death'] = self.one('SELECT patient_id, death_date, death_source, sources, sources_disagree FROM death WHERE patient_id = ?', self.pid)
        return self._cache['death']

    @property
    def window_year(self):
        """(start exclusive, end inclusive) of the default rolling 12 months."""
        return DC.add_months(self.as_of, -12).isoformat(), self.as_of


# -- small helpers ------------------------------------------------------------------------------------------------------------
def num(x, places=2):
    if x is None:
        return None
    f = float(x)
    return int(f) if f == int(f) and abs(f) < 1e12 else round(f, places)


def fmt(x, places=2):
    v = num(x, places)
    return '' if v is None else (f'{v:,}' if isinstance(v, int) and abs(v) >= 10000 else str(v))


def usd(cents):
    return round((cents or 0) / 100.0, 2)


def src(table, record_id, day=None, **extra):
    return {'table': table, 'record_id': record_id, 'date': day, **{k: v for k, v in extra.items() if v is not None}}


def core(answer_type, headline, **kw):
    out = {'answer_type': answer_type, 'headline': headline, 'value': None, 'unit': None, 'count': None, 'table': [], 'sources': [],
           'facts': {}, 'notes': []}
    out.update(kw)
    out['sources'] = out['sources'][:MAX_SOURCES]
    return out


def no_data(headline, **kw):
    return core('none', headline, **kw)


def win_note(window):
    return f'window: {CI.describe_window(window)}'


def condition_measures():
    return CI.condition_measures()


def label_of(concept):
    return CI.LAB_LABELS.get(concept, concept.replace('_', ' '))


def with_unit(value, unit):
    return f'{fmt(value)} {unit}'.strip() if unit else fmt(value)


# -- labs ---------------------------------------------------------------------------------------------------------------------
def lab_latest(ch, concept):
    return ch.one('SELECT record_id, event_date, value_canonical AS value, unit_canonical AS unit, value_censor, same_day_values '
                  'FROM measurement_latest WHERE patient_id = ? AND concept = ?', ch.pid, concept)


def lab_history(ch, concept, window=None):
    frag, params = CI.window_sql('event_date', window)
    return ch.rows(f'SELECT record_id, event_date, value_canonical AS value, unit_canonical AS unit, value_censor, source_table '
                   f'FROM measurement WHERE concept = ? AND patient_id = ? AND {ANY_LAB_WHERE}{frag} '
                   f'ORDER BY event_date DESC, record_id DESC', concept, ch.pid, *params)


def _censor(row):
    return row.get('value_censor') or ''


def lab(ch, it):
    c = it.get('concept') or {}
    pid, intent = ch.pid, it['intent']
    if c.get('kind') == 'unmeasured':
        return no_data(f"{c['value']} is not a measurement concept in this warehouse (no results to read for {pid}).")
    if c.get('kind') == 'general':
        return lab_panel(ch, it)
    concepts = c.get('concepts') or [c.get('value')]
    if not concepts or concepts == [None]:
        return None
    window = it.get('window')
    if intent == 'compare_values':
        return measurement_comparison(ch, it)
    if len(concepts) == 2 and set(concepts) == {'systolic_bp', 'diastolic_bp'} and intent in ('latest_value', 'timeline', 'status', 'count_in_window'):
        return blood_pressure(ch, it)
    if c.get('condition') and len(concepts) > 1 and intent in ('trend', 'latest_value'):
        return condition_measures_answer(ch, it, c, concepts)
    concept = concepts[0]
    label = label_of(concept)
    if intent == 'trend':
        return lab_trend(ch, it, concept, label)
    if intent == 'timeline' or (intent == 'list'):
        rows = lab_history(ch, concept, window)
        table = [{'date': r['event_date'], 'value': num(r['value']), 'unit': r['unit'], 'record_id': r['record_id']} for r in rows]
        head = (f'{len(rows)} {label} result{"" if len(rows) == 1 else "s"} for {pid} ({CI.describe_window(window)})' if rows else f'No {label} result for {pid} ({CI.describe_window(window)})')
        if rows:
            head += f": newest {with_unit(rows[0]['value'], rows[0]['unit'])} on {rows[0]['event_date']}, oldest {with_unit(rows[-1]['value'], rows[-1]['unit'])} on {rows[-1]['event_date']}"
        return core('table', head, count=len(rows), table=table, sources=[src('measurement', r['record_id'], r['event_date']) for r in rows],
                    notes=['every current usable result (measurement), newest first', win_note(window)])
    if intent == 'count_in_window':
        rows = lab_history(ch, concept, window)
        return core('count', f'{len(rows)} {label} result(s) for {pid} ({CI.describe_window(window)})', value=len(rows), count=len(rows),
                    sources=[src('measurement', r['record_id'], r['event_date']) for r in rows],
                    notes=['every current usable result (measurement)', win_note(window)])
    if intent == 'status':
        rows = lab_history(ch, concept, window)
        yes = bool(rows)
        head = (f"Yes: {len(rows)} {label} result(s) for {pid} in {CI.describe_window(window)}; most recent {with_unit(rows[0]['value'], rows[0]['unit'])} on {rows[0]['event_date']}"
                if yes else f'No: no {label} result for {pid} in {CI.describe_window(window)}')
        return core('yes_no', head, value=yes, sources=[src('measurement', r['record_id'], r['event_date']) for r in rows[:5]],
                    facts={'n': len(rows)}, notes=['every current usable result (measurement)', win_note(window)])
    # latest_value
    ordinal, mode = it.get('ordinal'), it.get('mode')
    extreme = _extreme(it.get('question', ''))
    if ordinal or mode == 'any' or extreme:
        rows = lab_history(ch, concept, window)
        if not rows:
            return no_data(f'No {label} result for {pid} ({CI.describe_window(window)})')
        if extreme:
            row = (max if extreme == 'max' else min)(rows, key=lambda r: (r['value'], r['event_date'] or ''))
            what = 'Highest' if extreme == 'max' else 'Lowest'
        elif ordinal == 2:
            if len(rows) < 2:
                return no_data(f'Only {len(rows)} {label} result on record for {pid}; there is no earlier one', sources=[src('measurement', r['record_id'], r['event_date']) for r in rows])
            row, what = rows[1], 'Previous (second most recent)'
        elif ordinal == 1:
            row, what = rows[-1], 'First recorded'
        else:
            row, what = rows[0], 'Most recent (any result)'
        return core('value', f"{what} {label} for {pid}: {_censor(row)}{with_unit(row['value'], row['unit'])} on {row['event_date']}",
                    value=num(row['value']), unit=row['unit'], facts={'date': row['event_date'], 'n_results': len(rows)},
                    sources=[src('measurement', row['record_id'], row['event_date'])],
                    notes=['every current usable result (measurement), not only the latest', win_note(window)])
    row = lab_latest(ch, concept)
    if row is None:
        return no_data(f'No {label} result on record for {pid}')
    notes = ['latest result per patient and concept (measurement_latest)']
    if (row.get('same_day_values') or 0) > 1:
        notes.append(f"{row['same_day_values']} results share this date; the chart's latest-result rule picked this one")
    return core('value', f"Latest {label} for {pid}: {_censor(row)}{with_unit(row['value'], row['unit'])} on {row['event_date']}",
                value=num(row['value']), unit=row['unit'], facts={'date': row['event_date']},
                sources=[src('measurement_latest', row['record_id'], row['event_date'])], notes=notes)


def _extreme(question):
    q = (question or '').lower()
    if re.search(r'\b(?:highest|peak|maximum|max|worst high)\b', q):
        return 'max'
    if re.search(r'\b(?:lowest|minimum|min|nadir)\b', q):
        return 'min'
    return None


def measurement_comparison(ch, it):
    """Evaluate explicit bounds against sourced latest results, preserving unknowns."""
    import operator
    compare = {'<': operator.lt, '<=': operator.le, '>': operator.gt, '>=': operator.ge}
    readings, sources = [], []
    for predicate in it['comparisons']:
        concept = predicate['concept']
        if it.get('window'):
            history = lab_history(ch, concept, it['window'])
            reading = history[0] if history else None
        else:
            reading = lab_latest(ch, concept)
        if not reading or reading['value'] is None:
            return no_data(f'Cannot compare: no usable {label_of(concept)} result for {ch.pid}', sources=sources)
        sources.append(src('measurement_latest' if not it.get('window') else 'measurement', reading['record_id'], reading['event_date']))
        if reading.get('value_censor') or reading.get('same_day_values', 1) > 1:
            return no_data(f'Cannot compare an ambiguous or censored {label_of(concept)} result for {ch.pid}', sources=sources)
        if predicate.get('unit'):
            normalize = lambda unit: str(unit or '').lower().replace(' ', '').replace('percent', '%')
            if normalize(predicate['unit']) != normalize(reading.get('unit')):
                return no_data(f"Cannot compare {label_of(concept)} in {reading.get('unit')} with the requested {predicate['unit']} without a validated conversion", sources=sources)
        readings.append({**predicate, 'actual': reading['value'], 'date': reading['event_date'],
                         'passes': compare[predicate['operator']](reading['value'], predicate['value'])})
    if len({reading['date'] for reading in readings}) > 1:
        return no_data(f'Cannot treat measurements from different dates as one reading for {ch.pid}', table=readings, sources=sources)
    yes = all(reading['passes'] for reading in readings)
    detail = '; '.join(f"{label_of(r['concept'])} {fmt(r['actual'])} {r['operator']} {fmt(r['value'])}" for r in readings)
    return core('yes_no', f"{'Yes' if yes else 'No'}: {detail} ({readings[0]['date']})", value=yes,
                table=readings, sources=sources, facts={'all_explicit_bounds_met': yes},
                notes=['User-supplied bounds; all must hold. This is a numeric comparison, not clinical suitability.'])


def blood_pressure(ch, it):
    s, d = lab_latest(ch, 'systolic_bp'), lab_latest(ch, 'diastolic_bp')
    window = it.get('window')
    if it['intent'] in ('timeline', 'status', 'count_in_window'):
        rows = ch.rows(f"SELECT event_date, concept, value_canonical AS value, record_id FROM measurement WHERE patient_id = ? AND concept IN ('systolic_bp','diastolic_bp') "
                       f"AND {ANY_LAB_WHERE}{CI.window_sql('event_date', window)[0]} ORDER BY event_date DESC", ch.pid, *CI.window_sql('event_date', window)[1])
        by = {}
        for r in rows:
            by.setdefault(r['event_date'], {})[r['concept']] = r
        table = [{'date': k, 'systolic': num(v.get('systolic_bp', {}).get('value')), 'diastolic': num(v.get('diastolic_bp', {}).get('value'))} for k, v in by.items()]
        sources = [src('measurement', r['record_id'], r['event_date']) for r in rows]
        if it['intent'] == 'status':
            return core('yes_no', f"{'Yes' if table else 'No'}: {len(table)} blood pressure reading(s) for {ch.pid} in {CI.describe_window(window)}", value=bool(table), sources=sources[:5])
        if it['intent'] == 'count_in_window':
            return core('count', f'{len(table)} blood pressure reading(s) for {ch.pid} ({CI.describe_window(window)})', value=len(table), count=len(table), sources=sources)
        return core('table', f'{len(table)} blood pressure readings for {ch.pid} ({CI.describe_window(window)})', count=len(table), table=table, sources=sources)
    if not s and not d:
        return no_data(f'No blood pressure on record for {ch.pid}')
    parts = [f"{fmt(s['value']) if s else '?'}/{fmt(d['value']) if d else '?'} mmHg"]
    same = s and d and s['event_date'] == d['event_date']
    head = f"Latest blood pressure for {ch.pid}: {parts[0]} on {s['event_date'] if same else (s or d)['event_date']}"
    if s and d and not same:
        head = f"Latest systolic {fmt(s['value'])} mmHg on {s['event_date']}; latest diastolic {fmt(d['value'])} mmHg on {d['event_date']} (not the same day)"
    return core('value', head, value=num(s['value']) if s else None, unit='mmHg',
                facts={'systolic': num(s['value']) if s else None, 'diastolic': num(d['value']) if d else None, 'date': (s or d)['event_date']},
                table=[{'measure': 'systolic', 'value': num(s['value']) if s else None, 'unit': 'mmHg', 'date': s['event_date'] if s else None},
                       {'measure': 'diastolic', 'value': num(d['value']) if d else None, 'unit': 'mmHg', 'date': d['event_date'] if d else None}],
                sources=[src('measurement_latest', r['record_id'], r['event_date']) for r in (s, d) if r],
                notes=['latest result per patient and concept (measurement_latest)'])


def condition_measures_answer(ch, it, c, concepts):
    """Control of a condition that several measures govern (CKD: eGFR and UACR; hypertension: systolic and diastolic): one reading per measure,
    each stated with its own direction, in one answer that names the condition and the measures."""
    cond = (c.get('condition') or '').replace('_', ' ')
    labels = ' and '.join(label_of(m) for m in concepts)
    parts, table, sources, words = [], [], [], []
    for m in concepts:
        if it['intent'] == 'trend':
            r = lab_trend(ch, it, m, label_of(m))
            words.append((r.get('facts') or {}).get('direction'))
        else:
            row = lab_latest(ch, m)
            r = (core('value', f"Latest {label_of(m)}: {_censor(row)}{with_unit(row['value'], row['unit'])} on {row['event_date']}", value=num(row['value']), unit=row['unit'],
                      table=[{'date': row['event_date'], 'value': num(row['value']), 'unit': row['unit']}], sources=[src('measurement_latest', row['record_id'], row['event_date'])])
                 if row else no_data(f'No {label_of(m)} result on record'))
        parts.append(r['headline'])
        table += [{'measure': label_of(m), **t} for t in r.get('table') or []]
        sources += r.get('sources') or []
    directions = {w for w in words if w}
    overall = (directions.pop() if len(directions) == 1 else 'mixed') if directions else None
    kind = 'trend' if it['intent'] == 'trend' else 'latest'
    head = f"{cond.capitalize()} control for {ch.pid} ({labels}, {kind}): " + ' ; '.join(parts)
    spec = condition_measures().get(c.get('condition')) or {}
    notes = [f"{cond} control is read from {labels}" + (f" ({spec['source']})" if spec.get('source') else ''), win_note(it.get('window'))]
    if overall:
        return core('label', head, value=overall, facts={'direction': overall, 'measures': list(concepts)}, table=table, sources=sources, notes=notes)
    return core('table', head, count=len(table), table=table, sources=sources, notes=notes)


def lab_trend(ch, it, concept, label):
    window = it.get('window')
    rows = lab_history(ch, concept, window)
    q = it.get('question', '')
    if len(rows) < 2:
        return core('label', f'Fewer than two {label} results for {ch.pid} ({CI.describe_window(window)}); no direction can be read.', value='insufficient',
                    count=len(rows), sources=[src('measurement', r['record_id'], r['event_date']) for r in rows])
    last_two = bool(re.search(r'\b(?:last|latest|most recent|two) (?:two|2)\b|\btwo most recent\b|\bcompare the last\b|\blast two\b', q, re.I)) or not window
    if 'delta' in it.get('flags', []) or (window and not last_two):
        new, old = rows[0], rows[-1]
        basis = f'first and last readings in {CI.describe_window(window)}'
    else:
        new, old = rows[0], rows[1]
        basis = 'the two most recent results'
    delta = float(new['value']) - float(old['value'])
    polarity = CI.POLARITY.get(concept, 0)
    if abs(delta) < 1e-9:
        word = 'unchanged'
    elif polarity:
        word = 'improving' if delta * polarity > 0 else 'worsening'
    else:
        word = 'rising' if delta > 0 else 'falling'
    unit = new['unit']
    sign = '+' if delta > 0 else ''
    if 'delta' in it.get('flags', []):
        head = (f"{label} change for {ch.pid}: {sign}{fmt(delta)} {unit or ''} ({fmt(old['value'])} on {old['event_date']} to {fmt(new['value'])} on {new['event_date']}; "
                f"{len(rows)} readings in {CI.describe_window(window)})").replace('  ', ' ')
        return core('value', head, value=num(delta), unit=unit, facts={'first': num(old['value']), 'last': num(new['value']), 'direction': word, 'n_results': len(rows)},
                    table=[{'date': r['event_date'], 'value': num(r['value'])} for r in (old, new)],
                    sources=[src('measurement', r['record_id'], r['event_date']) for r in (old, new)],
                    notes=[f'{basis}; positive = increased', win_note(window)])
    head = (f"{word.capitalize()}: {label} {with_unit(new['value'], unit)} on {new['event_date']} versus {with_unit(old['value'], unit)} on {old['event_date']} "
            f"(change {sign}{fmt(delta)} {unit or ''})").replace('  ', ' ')
    return core('label', head, value=word, unit=unit, facts={'direction': word, 'delta': num(delta), 'newer': num(new['value']), 'older': num(old['value']), 'n_results': len(rows)},
                table=[{'date': r['event_date'], 'value': num(r['value']), 'unit': r['unit']} for r in (new, old)],
                sources=[src('measurement', r['record_id'], r['event_date']) for r in (new, old)],
                notes=[f'compared {basis}', 'the direction word is read against the favourable side of each measure'] if polarity else [f'compared {basis}'])


def lab_panel(ch, it):
    rows = ch.rows('SELECT concept, event_date, value_canonical AS value, unit_canonical AS unit, record_id FROM measurement_latest WHERE patient_id = ? ORDER BY concept', ch.pid)
    table = [{'measure': label_of(r['concept']), 'value': num(r['value']), 'unit': r['unit'], 'date': r['event_date']} for r in rows]
    head = (f'Latest results for {ch.pid}: ' + '; '.join(f"{t['measure']} {fmt(t['value'])} {t['unit'] or ''} ({t['date']})".replace('  ', ' ') for t in table[:12])) if table else f'No lab or vital results for {ch.pid}'
    return core('table', head, count=len(table), table=table, sources=[src('measurement_latest', r['record_id'], r['event_date']) for r in rows],
                notes=['latest result per concept (measurement_latest)'])


# -- demographics, coverage, practice ----------------------------------------------------------------------------------------
def demographics(ch, it):
    p = ch.patient
    if p is None:
        return None
    a = it.get('attribute')
    age = age_at(p.get('birth_date'), ch.as_of)  # completed years from the birth date at the as-of date, never the feed's age_latest
    fields = {'age': ('age', age, 'years', f"age {age} at {ch.as_of}"), 'birth_date': ('date of birth', p.get('birth_date'), None, None),
              'sex': ('sex', p.get('sex_reported'), None, None), 'race': ('race', p.get('race'), None, None), 'ethnicity': ('ethnicity', p.get('ethnicity'), None, None),
              'language': ('preferred language', p.get('preferred_language'), None, None), 'interpreter': ('interpreter needed', bool(p.get('interpreter_needed')), None, None),
              'zip': ('ZIP', p.get('zip'), None, None), 'county': ('county', p.get('county'), None, None), 'state': ('state', p.get('state'), None, None),
              'marital_status': ('marital status', p.get('marital_status'), None, None)}
    if a in fields:
        name, value, unit, _ = fields[a]
        t = 'value' if isinstance(value, (int, float)) and not isinstance(value, bool) else 'yes_no' if isinstance(value, bool) else 'label'
        head = f'{name.capitalize()} of {ch.pid}: {value if value is not None else "not recorded"}{" " + unit if unit and value is not None else ""}'
        return core(t, head, value=value, unit=unit, sources=[src('patient', ch.pid)])
    table = [{'field': k, 'value': v[1]} for k, v in fields.items()]
    return core('table', f"{ch.pid}: age {age}, {p.get('sex_reported')}, {p.get('race')}, {p.get('ethnicity')}, language {p.get('preferred_language')}",
                table=table, sources=[src('patient', ch.pid)])


def practice(ch, it):
    p = ch.patient
    if p is None:
        return None
    pid_practice = p.get('pcp_practice_id')
    basis = 'patient.pcp_practice_id'
    if not pid_practice:
        row = ch.one('SELECT attribution_practice_id, attribution_npi, record_id FROM enrollment_span WHERE patient_id = ? AND attribution_practice_id IS NOT NULL '
                     'ORDER BY coverage_start DESC LIMIT 1', ch.pid)
        pid_practice, basis = (row or {}).get('attribution_practice_id'), 'latest enrollment span attribution'
    org = ch.one('SELECT display_name FROM organization WHERE organization_id = ?', pid_practice) if pid_practice else None
    clin = p.get('pcp_clinician_id')
    if it.get('attribute') == 'pcp':
        head = f'Primary care clinician of {ch.pid}: {clin or "not recorded"}' + (f' ({pid_practice})' if pid_practice else '')
        return core('label', head, value=clin, sources=[src('patient', ch.pid)], facts={'practice': pid_practice})
    if not pid_practice:
        return no_data(f'No attributed practice is recorded for {ch.pid}')
    name = (org or {}).get('display_name')
    return core('label', f"{ch.pid} is attributed to {pid_practice}" + (f' ({name})' if name else ''), value=pid_practice,
                facts={'practice': pid_practice, 'display_name': name, 'pcp': clin}, sources=[src('patient', ch.pid)], notes=[f'practice from {basis}'])


def coverage(ch, it):
    p = ch.patient or {}
    spans = ch.rows('SELECT record_id, member_id, payer_id, product, line_of_business, coverage_start, coverage_end, enrollment_reason, termination_reason, '
                    'attribution_practice_id FROM enrollment_span WHERE patient_id = ? ORDER BY coverage_start', ch.pid)
    table = [{k: v for k, v in s.items() if k != 'record_id'} for s in spans]
    current = None
    for s in spans:
        if s['coverage_end'] is None or s['coverage_end'] >= ch.as_of:
            current = s
    sources = [src('enrollment_span', s['record_id'], s['coverage_start']) for s in spans]
    a = it.get('attribute')
    if it['intent'] == 'list' or a is None and False:
        return core('table', f'{len(spans)} coverage span(s) for {ch.pid}', count=len(spans), table=table, sources=sources)
    if a == 'member_id':
        ids = [s['member_id'] for s in spans]
        return core('label', f"Member id of {ch.pid}: {', '.join(dict.fromkeys(ids)) or 'none recorded'}", value=ids[-1] if ids else None, sources=sources, table=table)
    last = current or (spans[-1] if spans else None)
    status = p.get('coverage_status')
    if last is None:
        return core('label', f"{ch.pid}: coverage {p.get('coverage_type') or 'not recorded'} ({status or 'status unknown'}); no enrollment span on file",
                    value=p.get('coverage_type'), sources=[src('patient', ch.pid)])
    ended = f", ended {last['coverage_end']}" if last['coverage_end'] else ' (open)'
    head = (f"{ch.pid}: {last['product']} ({last['line_of_business']}) via {last['payer_id']}, since {last['coverage_start']}{ended}; patient coverage status {status}")
    return core('label', head, value=last['product'], facts={'product': last['product'], 'line_of_business': last['line_of_business'], 'payer': last['payer_id'],
                'coverage_start': last['coverage_start'], 'coverage_end': last['coverage_end'], 'member_id': last['member_id']}, table=table, sources=sources)


def death(ch, it):
    d = ch.death
    if it.get('attribute') == 'death_date':
        if not d or not d.get('death_date'):
            return no_data(f'No death date is recorded for {ch.pid}', sources=[src('death', ch.pid)])
        if d.get('sources_disagree'):
            return no_data(f'Death-date sources disagree for {ch.pid}; no single confirmed date is reported', sources=[src('death', ch.pid)])
        return core('date', f"Recorded death date for {ch.pid}: {d['death_date']} (source: {d['death_source']})", value=d['death_date'],
                    facts={'death_date': d['death_date']}, sources=[src('death', ch.pid, d['death_date'])])
    if d:
        return core('yes_no', f"No: {ch.pid} is deceased (death date {d['death_date']}, source {d['death_source']})", value=False,
                    facts={'deceased': True, 'death_date': d['death_date']}, sources=[src('death', ch.pid, d['death_date'])])
    return core('yes_no', f'Yes: {ch.pid} has no death record (living as of {ch.as_of})', value=True, facts={'deceased': False}, sources=[src('death', ch.pid)])


# -- utilization ---------------------------------------------------------------------------------------------------------------
def utilization(ch, it):
    kind = UTIL_KIND[it['domain']]
    label = UTIL_LABEL[kind]
    window, intent = it.get('window'), it['intent']
    frag, params = CI.window_sql('event_date', window)
    discharge_order = 'order_by_discharge' in (it.get('flags') or [])
    order = 'end_date DESC, event_date DESC, record_id DESC' if discharge_order else 'event_date DESC, record_id DESC'
    if discharge_order:
        frag += ' AND end_date IS NOT NULL'
    rows = ch.rows(f'SELECT kind, event_date, end_date, discharge_known, sources, in_ehr, record_id, organization_id, readmitted_30d, days_to_readmission '
                   f'FROM utilization_event WHERE patient_id = ? AND kind = ?{frag} ORDER BY {order}', ch.pid, kind, *params)
    table = [{'date': r['event_date'], 'discharge_date': r['end_date'], 'sources': r['sources'], 'readmitted_30d': r['readmitted_30d']} for r in rows]
    sources = [src('utilization_event', r['record_id'], r['event_date'], kind=kind) for r in rows]
    notes = [f'{label}s are all-source utilization_event rows (EHR, ADT and claims), not the EHR-only visit table', win_note(window)]
    plural = label + 's'
    if intent == 'count_in_window':
        return core('count', f'{len(rows)} {label}{"" if len(rows) == 1 else "s"} for {ch.pid} ({CI.describe_window(window)})', value=len(rows), count=len(rows),
                    table=table, sources=sources, notes=notes)
    if intent == 'status':
        yes = bool(rows)
        return core('yes_no', (f"Yes: {len(rows)} {plural} for {ch.pid} ({CI.describe_window(window)}); most recent {rows[0]['event_date']}" if yes else
                               f'No: no {label} for {ch.pid} ({CI.describe_window(window)})'), value=yes, count=len(rows), table=table[:10], sources=sources[:5], notes=notes)
    if intent == 'last_event':
        if not rows:
            return no_data(f'No {label} for {ch.pid} ({CI.describe_window(window)})', notes=notes)
        r = rows[0]
        attr = it.get('attribute')
        if attr == 'discharge_date':
            if r['end_date']:
                headline = (f"Latest recorded hospital discharge for {ch.pid}: {r['end_date']} (admitted {r['event_date']})" if discharge_order else
                            f"The most recent {label} for {ch.pid} (admitted {r['event_date']}) was discharged {r['end_date']}")
                return core('date', headline, value=r['end_date'],
                            facts={'admit_date': r['event_date'], 'discharge_date': r['end_date']}, table=table[:5], sources=sources[:1], notes=notes)
            return core('none', f"The most recent {label} for {ch.pid} (admitted {r['event_date']}) has no recorded discharge date", facts={'admit_date': r['event_date']},
                        table=table[:5], sources=sources[:1], notes=notes)
        extra = f", discharged {r['end_date']}" if r['end_date'] else ''
        return core('date', f"Most recent {label} for {ch.pid}: {r['event_date']}{extra}", value=r['event_date'], facts={'date': r['event_date'], 'discharge_date': r['end_date']},
                    table=table[:5], sources=sources[:1], notes=notes)
    head = f'{len(rows)} {label}{"" if len(rows) == 1 else "s"} for {ch.pid} ({CI.describe_window(window)})' + (
        f": {', '.join(r['event_date'] for r in rows[:8])}" if rows else '')
    return core('table', head, count=len(rows), table=table, sources=sources, notes=notes)


def readmission(ch, it):
    rows = ch.rows('SELECT admit_date, discharge_date, type, readmitted_30d, days_to_readmission, organization_id FROM stay WHERE patient_id = ? ORDER BY admit_date DESC', ch.pid)
    window = it.get('window')
    if window:
        rows = [r for r in rows if window['start'] <= (r['admit_date'] or '') <= window['end']]
    re_ = [r for r in rows if r['readmitted_30d']]
    notes = ['stay.readmitted_30d: another admission within 30 days of a discharge', win_note(window)]
    table = [{'admit_date': r['admit_date'], 'discharge_date': r['discharge_date'], 'type': r['type'], 'days_to_readmission': r['days_to_readmission']} for r in re_]
    sources = [src('stay', ch.pid, r['admit_date']) for r in re_] or [src('stay', ch.pid)]
    if it['intent'] == 'count_in_window':
        return core('count', f'{len(re_)} stay(s) of {ch.pid} were followed by a readmission within 30 days ({CI.describe_window(window)})', value=len(re_), count=len(re_),
                    table=table, sources=sources, notes=notes)
    if it['intent'] == 'list':
        return core('table', f'{len(re_)} readmission-followed stay(s) for {ch.pid}', count=len(re_), table=table, sources=sources, notes=notes)
    if not rows:
        return core('yes_no', f'No: {ch.pid} has no recorded hospital or observation stay ({CI.describe_window(window)})', value=False, sources=sources, notes=notes)
    if re_:
        r = re_[0]
        return core('yes_no', f"Yes: {ch.pid} was readmitted within 30 days of a discharge ({len(re_)} time(s); most recent admission {r['admit_date']}, "
                              f"{r['days_to_readmission']} days to readmission)", value=True, count=len(re_), table=table, sources=sources, notes=notes)
    return core('yes_no', f'No: none of the {len(rows)} stay(s) of {ch.pid} was followed by a readmission within 30 days', value=False, sources=sources, notes=notes)


def visits(ch, it):
    window = it.get('window')
    frag, params = CI.window_sql('event_date', window)
    marks = ','.join('?' * len(OUTPATIENT_TYPES))
    rows = ch.rows(f"SELECT encounter_id, event_date, type, type_concept, status, organization_id FROM visit WHERE patient_id = ? AND type_concept IN ({marks}) "
                   f"AND coalesce(status, 'completed') = 'completed'{frag} ORDER BY event_date DESC", ch.pid, *OUTPATIENT_TYPES, *params)
    table = [{'date': r['event_date'], 'type': r['type_concept']} for r in rows]
    sources = [src('visit', r['encounter_id'], r['event_date']) for r in rows]
    notes = ['completed office, primary care, specialist, telehealth and urgent-care visits (visit table); ED, inpatient and observation are separate all-source events', win_note(window)]
    if it['intent'] == 'count_in_window':
        return core('count', f'{len(rows)} completed visit(s) for {ch.pid} ({CI.describe_window(window)})', value=len(rows), count=len(rows), table=table, sources=sources, notes=notes)
    if it['intent'] == 'status':
        return core('yes_no', f"{'Yes' if rows else 'No'}: {len(rows)} completed visit(s) for {ch.pid} ({CI.describe_window(window)})", value=bool(rows), count=len(rows), sources=sources[:5], notes=notes)
    if it['intent'] == 'last_event':
        if not rows:
            return no_data(f'No completed visit for {ch.pid} ({CI.describe_window(window)})', notes=notes)
        return core('date', f"Most recent completed visit for {ch.pid}: {rows[0]['event_date']} ({rows[0]['type_concept']})", value=rows[0]['event_date'], table=table[:5], sources=sources[:1], notes=notes)
    return core('table', f'{len(rows)} completed visit(s) for {ch.pid} ({CI.describe_window(window)})', count=len(rows), table=table, sources=sources, notes=notes)


def appointments(ch, it):
    window, q = it.get('window'), it.get('question', '').lower()
    frag, params = CI.window_sql('scheduled_date', window)
    rows = ch.rows(f'SELECT appointment_id, scheduled_date, final_status, outcome, purpose, organization_id FROM appointment WHERE patient_id = ?{frag} ORDER BY scheduled_date DESC', ch.pid, *params)
    if re.search(r'no[- ]?shows?|missed', q):
        rows, what = [r for r in rows if r['final_status'] == 'no_show'], 'no-show appointment'
    elif re.search(r'cancell?ed', q):
        rows, what = [r for r in rows if r['final_status'] == 'cancelled'], 'cancelled appointment'
    elif re.search(r'upcoming|next|scheduled|future', q):
        rows, what = sorted([r for r in rows if r['scheduled_date'] > ch.as_of and r['final_status'] == 'booked'], key=lambda r: r['scheduled_date']), 'upcoming appointment'
    else:
        what = 'appointment'
    table = [{'date': r['scheduled_date'], 'status': r['final_status'], 'purpose': r['purpose']} for r in rows]
    sources = [src('appointment', r['appointment_id'], r['scheduled_date']) for r in rows]
    notes = ['appointment.final_status after all rescheduling and cancellation versions', win_note(window)]
    if it['intent'] == 'count_in_window':
        return core('count', f'{len(rows)} {what}(s) for {ch.pid} ({CI.describe_window(window)})', value=len(rows), count=len(rows), table=table, sources=sources, notes=notes)
    if it['intent'] == 'status':
        return core('yes_no', f"{'Yes' if rows else 'No'}: {len(rows)} {what}(s) for {ch.pid} ({CI.describe_window(window)})", value=bool(rows), count=len(rows), sources=sources[:5], notes=notes)
    if it['intent'] == 'last_event':
        if not rows:
            return no_data(f'No {what} for {ch.pid} ({CI.describe_window(window)})', notes=notes)
        return core('date', f"{'Next' if what.startswith('upcoming') else 'Most recent'} {what.replace('upcoming ', '')} for {ch.pid}: {rows[0]['scheduled_date']} ({rows[0]['final_status']})",
                    value=rows[0]['scheduled_date'], table=table[:5], sources=sources[:1], notes=notes)
    return core('table', f'{len(rows)} {what}(s) for {ch.pid} ({CI.describe_window(window)})', count=len(rows), table=table, sources=sources, notes=notes)


# -- medications, fills, prescriptions ---------------------------------------------------------------------------------------
def class_names(ch, cls):
    names = (ch.lexicon or {}).get('names') or {}
    return {n for n, classes in names.items() if cls in classes}


def entry_matches(ch, entry, concept):
    key = (entry.get('drug_key') or '').lower()
    name = (entry.get('name') or '').lower()
    if concept['kind'] == 'drug':
        return concept['value'] in (key, name) or concept['value'] in name
    cls = concept['value']
    if key in class_names(ch, cls):
        return True
    stem = STEMS.get(cls)
    return bool(stem and re.search(stem, f'{key} {name}'))


def concept_label(concept):
    if not concept:
        return 'medication'
    if concept['kind'] == 'class':
        return concept['value'].replace('_', ' ')
    return concept.get('term') or concept['value'].replace('_', ' ')


def fill_filter(concept):
    """-> (sql fragment over the fill table, params)."""
    if concept['kind'] == 'class':
        return ' AND drug_class = ?', [concept['value']]
    return ' AND lower(drug_name) LIKE ?', [f"%{concept['value']}%"]


def active_list(ch):
    return ch.rows("SELECT name, drug_key, rxcui, list_status, list_source, last_fill_date, last_order_date, discrepancy, stale, first_listed, last_listed, record_id "
                   "FROM medication_list_entry WHERE patient_id = ? AND list_status = 'active' ORDER BY drug_key, name", ch.pid)


def medication(ch, it):
    concept, intent = it.get('concept'), it['intent']
    entries = active_list(ch)
    if intent == 'status' and concept:
        label = concept_label(concept)
        hits = [e for e in entries if entry_matches(ch, e, concept)]
        frag, params = fill_filter(concept)
        start, end = ch.window_year
        fills = ch.rows(f'SELECT fill_id, fill_date, drug_name, drug_class, days_supply, record_ids FROM fill WHERE patient_id = ?{frag} AND fill_date > ? AND fill_date <= ? '
                        f'ORDER BY fill_date DESC', ch.pid, *params, start, end)
        last_any = ch.one(f'SELECT max(fill_date) AS d FROM fill WHERE patient_id = ?{frag}', ch.pid, *params)
        asks_list = bool(re.search(r'\b(?:med(?:ication)?s? list|listed|on the list|prescribed|ordered|on file)\b', it.get('question') or '', re.I))
        on = bool(fills) or (asks_list and bool(hits))  # "on" = a picked-up fill in the 12 months ending as-of; list/order status answers only a list question
        bits = []
        if fills:
            bits.append(f"{len(fills)} fill(s) in the 12 months ending {end}, latest {fills[0]['fill_date']}")
        if hits and (asks_list or not fills):
            bits.append("active medication-list entry: " + '; '.join(h['name'] for h in hits[:4]))
        if fills:
            head = f"Yes: {ch.pid} is on {label} ({'; '.join(bits)})"
        elif hits and asks_list:
            head = f"Yes: {label} is listed as prescribed for {ch.pid} ({'; '.join(bits)}); no {label} fill in the 12 months ending {end}"
        elif hits:
            head = f"No: {ch.pid} has no {label} fill in the 12 months ending {end}, so is not counted as on it; {label} is listed or prescribed only ({'; '.join(bits)})"
        else:
            tail = f"; last {label} fill {last_any['d']}" if last_any and last_any['d'] else f'; no {label} fill on record'
            head = f"No: {ch.pid} has no {label} fill in the 12 months ending {end} and no active {label} on the medication list{tail}"
        sources = [src('medication_list_entry', h['record_id'], h['last_listed']) for h in hits] + [src('fill', f['record_ids'].split(',')[0] if f['record_ids'] else f['fill_id'], f['fill_date']) for f in fills[:5]]
        return core('yes_no', head, value=on, count=len(hits) + len(fills),
                    table=[{'basis': 'active list', 'name': h['name'], 'last_fill_date': h['last_fill_date'], 'discrepancy': h['discrepancy']} for h in hits] +
                          [{'basis': 'fill', 'name': f['drug_name'], 'date': f['fill_date'], 'days_supply': f['days_supply']} for f in fills[:5]],
                    sources=sources, facts={'on_list': bool(hits), 'fills_12m': len(fills), 'last_fill': last_any['d'] if last_any else None, 'on': bool(fills)},
                    notes=['"on" = a picked-up fill in the 12 months ending the as-of date (the decision context); a list-only or ordered drug is reported as listed/prescribed',
                           f'window: the 12 months ending {end}'])
    if concept and intent == 'list':
        entries = [e for e in entries if entry_matches(ch, e, concept)]
    table = [{'drug': e['drug_key'], 'name': e['name'], 'last_fill_date': e['last_fill_date'], 'last_order_date': e['last_order_date'], 'note': e['discrepancy']} for e in entries]
    if intent in ('last_event', 'timeline'):
        return fills(ch, {**it, 'domain': 'fill', 'intent': 'last_event' if intent == 'last_event' else 'list'})
    head = (f"{len(entries)} active medication(s) for {ch.pid}: " + ', '.join(e['drug_key'] for e in entries)) if entries else f'No active medication-list entry for {ch.pid}'
    return core('list', head, count=len(entries), table=table, sources=[src('medication_list_entry', e['record_id'], e['last_listed']) for e in entries],
                facts={'drugs': [e['drug_key'] for e in entries], 'names': [e['name'] for e in entries]},
                notes=["active = a medication-list entry whose list status is 'active' (the clinician-facing current medications); "
                       "entries with no fill in 180 days stay listed, with the discrepancy shown"])


def fills(ch, it):
    concept, intent, window = it.get('concept'), it['intent'], it.get('window')
    if it.get('attribute') == 'pdc':
        return pdc(ch, it)
    frag, params = fill_filter(concept) if concept else ('', [])
    wfrag, wparams = CI.window_sql('fill_date', window)
    rows = ch.rows(f'SELECT fill_id, fill_date, drug_name, drug_class, days_supply, supply_end, sources, record_ids FROM fill WHERE patient_id = ?{frag}{wfrag} '
                   f'ORDER BY fill_date DESC, fill_id DESC', ch.pid, *params, *wparams)
    label = concept_label(concept) if concept else 'medication'
    table = [{'date': r['fill_date'], 'drug': r['drug_name'], 'class': r['drug_class'], 'days_supply': r['days_supply']} for r in rows]
    sources = [src('fill', (r['record_ids'] or '').split(',')[0] or r['fill_id'], r['fill_date']) for r in rows]
    notes = ['fill = dispense and counted pharmacy-claim fills merged', win_note(window)]
    if intent == 'count_in_window':
        if it.get('attribute') == 'drug_class':
            from collections import Counter
            classes = Counter(r['drug_class'] for r in rows if r['drug_class'])
            unknown = sum(not r['drug_class'] for r in rows)
            table = [{'drug_class': name, 'fills': count} for name, count in sorted(classes.items())]
            facts = {'distinct_recorded_drug_classes': len(classes), 'unclassified_fills': unknown, 'fills': len(rows)}
            if unknown:
                return no_data(f'At least {len(classes)} recorded drug classes for {ch.pid}; {unknown} fills lack a class, so the exact class count is unknown',
                               table=table, sources=sources, facts=facts, notes=notes)
            return core('count', f'{len(classes)} distinct recorded drug classes for {ch.pid} ({CI.describe_window(window)}; {len(rows)} fills)',
                        value=len(classes), count=len(classes), table=table, sources=sources, facts=facts, notes=notes)
        return core('count', f'{len(rows)} {label} fill(s) for {ch.pid} ({CI.describe_window(window)})', value=len(rows), count=len(rows), table=table, sources=sources, notes=notes)
    if intent == 'status':
        return core('yes_no', f"{'Yes' if rows else 'No'}: {len(rows)} {label} fill(s) for {ch.pid} ({CI.describe_window(window)})", value=bool(rows), count=len(rows), table=table[:5], sources=sources[:5], notes=notes)
    if intent == 'last_event':
        if not rows:
            return no_data(f'No {label} fill on record for {ch.pid} ({CI.describe_window(window)})', notes=notes)
        r = rows[0]
        return core('date', f"Last {label} fill for {ch.pid}: {r['fill_date']} ({r['drug_name']}, {r['days_supply']} days' supply)", value=r['fill_date'],
                    facts={'date': r['fill_date'], 'drug': r['drug_name']}, table=table[:5], sources=sources[:1], notes=notes)
    return core('table', f'{len(rows)} {label} fill(s) for {ch.pid} ({CI.describe_window(window)})', count=len(rows), table=table, sources=sources, notes=notes)


def pdc(ch, it):
    """Proportion of days covered for one drug class or drug: fills in the window; overlapping supply carried forward; the period runs from
    the first fill in the window to the window end; at least two fills (PQA)."""
    concept, window = it.get('concept'), it.get('window')
    if not concept:
        return None
    if not window:
        start, end = ch.window_year
        window = {'kind': 'rolling_12_months', 'start': start, 'end': end, 'start_exclusive': True}
    frag, params = fill_filter(concept)
    wfrag, wparams = CI.window_sql('fill_date', window)
    rows = ch.rows(f'SELECT fill_id, fill_date, days_supply, record_ids FROM fill WHERE patient_id = ?{frag}{wfrag} ORDER BY fill_date, fill_id', ch.pid, *params, *wparams)
    label = concept_label(concept)
    notes = ['PDC = covered days / days from the first fill in the window to the window end; overlapping supply is carried forward; at least 2 fills', win_note(window)]
    if len(rows) < 2:
        return core('none', f'{len(rows)} {label} fill(s) in {CI.describe_window(window)} for {ch.pid}: at least 2 are needed for a proportion of days covered',
                    count=len(rows), sources=[src('fill', (r['record_ids'] or '').split(',')[0], r['fill_date']) for r in rows], notes=notes)
    import datetime as dt
    first = dt.date.fromisoformat(rows[0]['fill_date'])
    last_day = dt.date.fromisoformat(window['end'])
    covered, cursor = set(), first
    for r in rows:
        d = max(dt.date.fromisoformat(r['fill_date']), cursor)
        for i in range(int(r['days_supply'] or 0)):
            day = d + dt.timedelta(days=i)
            if day <= last_day:
                covered.add(day)
        cursor = d + dt.timedelta(days=int(r['days_supply'] or 0))
    period = (last_day - first).days + 1
    value = len(covered) / period
    return core('value', f"Proportion of days covered by {label} for {ch.pid}: {value:.0%} ({len(covered)} of {period} days, {len(rows)} fills, {CI.describe_window(window)}); "
                         f"{'meets' if value >= 0.8 else 'does not meet'} the 0.8 adherence threshold",
                value=round(value, 4), unit='proportion', facts={'pdc': round(value, 4), 'fills': len(rows), 'period_days': period, 'covered_days': len(covered), 'adherent': value >= 0.8,
                                                                  'treatment_days_91': period >= 91},
                sources=[src('fill', (r['record_ids'] or '').split(',')[0] or r['fill_id'], r['fill_date']) for r in rows], notes=notes)


def prescriptions(ch, it):
    concept, intent, window = it.get('concept'), it['intent'], it.get('window')
    wfrag, wparams = CI.window_sql('ordered_date', window)
    rows = ch.rows(f'SELECT record_id, drug_name, ordered_date, fill_status, first_fill_date, last_fill_date, quantity, days_supply, indication FROM prescription '
                   f'WHERE patient_id = ?{wfrag} ORDER BY ordered_date DESC', ch.pid, *wparams)
    if concept:
        rows = [r for r in rows if _rx_matches(ch, r, concept)]
    if 'not_filled' in it.get('flags', []):
        rows = [r for r in rows if r['fill_status'] == 'not_filled']
        what = 'never-filled prescription'
    else:
        what = 'prescription'
    table = [{'drug': r['drug_name'], 'ordered': r['ordered_date'], 'fill_status': r['fill_status'], 'first_fill': r['first_fill_date']} for r in rows]
    sources = [src('prescription', r['record_id'], r['ordered_date']) for r in rows]
    notes = ['prescription.fill_status: filled when a fill is linked to the order, not_filled otherwise', win_note(window)]
    if intent == 'count_in_window':
        return core('count', f'{len(rows)} {what}(s) for {ch.pid}', value=len(rows), count=len(rows), table=table, sources=sources, notes=notes)
    if intent == 'status':
        return core('yes_no', f"{'Yes' if rows else 'No'}: {len(rows)} {what}(s) for {ch.pid}", value=bool(rows), count=len(rows), table=table[:5], sources=sources[:5], notes=notes)
    if intent == 'last_event' and rows:
        return core('date', f"Most recent {what} for {ch.pid}: {rows[0]['drug_name']} on {rows[0]['ordered_date']} ({rows[0]['fill_status']})", value=rows[0]['ordered_date'], table=table[:5], sources=sources[:1], notes=notes)
    head = (f"{len(rows)} {what}(s) for {ch.pid}: " + '; '.join(f"{r['drug_name']} ({r['ordered_date']})" for r in rows[:10])) if rows else f'No {what} for {ch.pid}'
    return core('list', head, count=len(rows), table=table, sources=sources, facts={'drugs': [r['drug_name'] for r in rows]}, notes=notes)


def _rx_matches(ch, row, concept):
    name = (row.get('drug_name') or '').lower()
    if concept['kind'] == 'drug':
        return concept['value'] in name
    return entry_matches(ch, {'drug_key': name.split(' ')[0], 'name': name}, concept)


def returned_fills(ch, it):
    window = it.get('window')
    wfrag, wparams = CI.window_sql('event_date', window)
    rows = ch.rows(f'SELECT record_id, event_date, drug_name, reason, source FROM returned_fill WHERE patient_id = ?{wfrag} ORDER BY event_date DESC', ch.pid, *wparams)
    if it.get('concept'):
        rows = [r for r in rows if _rx_matches(ch, r, it['concept'])]
    table = [{'date': r['event_date'], 'drug': r['drug_name'], 'reason': r['reason']} for r in rows]
    sources = [src('returned_fill', r['record_id'], r['event_date']) for r in rows]
    if it['intent'] == 'count_in_window':
        return core('count', f'{len(rows)} returned fill(s) for {ch.pid}', value=len(rows), count=len(rows), table=table, sources=sources)
    head = (f"{len(rows)} returned fill(s) for {ch.pid}: " + '; '.join(f"{r['drug_name']} ({r['event_date']}, {r['reason']})" for r in rows[:8])) if rows else f'No returned fill for {ch.pid}'
    return core('list', head, count=len(rows), table=table, sources=sources, notes=['dispenses returned to stock or not collected (returned_fill)', win_note(window)])


# -- immunizations, screening, wellness ------------------------------------------------------------------------------------------
def immunizations(ch, it):
    concept, intent, window = it.get('concept'), it['intent'], it.get('window')
    wfrag, wparams = CI.window_sql('event_date', window)
    cfrag, cparams = (' AND vaccine LIKE ?', [concept['value']]) if concept else ('', [])
    rows = ch.rows(f"SELECT record_id, vaccine, product, event_date, season, source FROM immunization WHERE patient_id = ? AND status = 'completed'{cfrag}{wfrag} "
                   f'ORDER BY event_date DESC', ch.pid, *cparams, *wparams)
    label = (concept['value'].rstrip('%') + ' vaccine') if concept else 'vaccine'
    table = [{'date': r['event_date'], 'vaccine': r['vaccine'], 'product': r['product'], 'season': r['season']} for r in rows]
    sources = [src('immunization', r['record_id'], r['event_date']) for r in rows]
    notes = ['completed immunization records', win_note(window)]
    if intent == 'count_in_window':
        return core('count', f'{len(rows)} {label} dose(s) for {ch.pid} ({CI.describe_window(window)})', value=len(rows), count=len(rows), table=table, sources=sources, notes=notes)
    if intent == 'status' or re.search(r'if so|when', it.get('question', ''), re.I) and intent != 'last_event' and rows:
        yes = bool(rows)
        head = (f"Yes: {ch.pid} received {label} on {rows[0]['event_date']} ({rows[0]['vaccine']})" if yes else f'No: no completed {label} for {ch.pid} ({CI.describe_window(window)})')
        return core('yes_no', head, value=yes, count=len(rows), table=table[:5], sources=sources[:3], facts={'date': rows[0]['event_date'] if rows else None}, notes=notes)
    if intent == 'last_event':
        if not rows:
            return no_data(f'No completed {label} for {ch.pid} ({CI.describe_window(window)})', notes=notes)
        return core('date', f"Most recent {label} for {ch.pid}: {rows[0]['event_date']} ({rows[0]['vaccine']})", value=rows[0]['event_date'], table=table[:5], sources=sources[:1], notes=notes)
    return core('table', f'{len(rows)} immunization(s) for {ch.pid}' + (f': ' + ', '.join(f"{r['vaccine']} {r['event_date']}" for r in rows[:8]) if rows else ''), count=len(rows), table=table, sources=sources, notes=notes)


def screenings(ch, it):
    concept, intent, window = it.get('concept'), it['intent'], it.get('window')
    wfrag, wparams = CI.window_sql('event_date', window)
    cfrag, cparams = (' AND test = ?', [concept['value']]) if concept else ('', [])
    rows = ch.rows(f'SELECT record_id, test, result_status, event_date, result_date, birads_category, source FROM cancer_screening WHERE patient_id = ?{cfrag}{wfrag} '
                   f'ORDER BY event_date DESC', ch.pid, *cparams, *wparams)
    final = [r for r in rows if r['result_status'] == 'final']
    billed = [r for r in rows if r['result_status'] != 'final']
    label = concept.get('label', 'screening') if concept else 'cancer screening'
    table = [{'date': r['event_date'], 'test': r['test'], 'result_status': r['result_status']} for r in rows]
    sources = [src('cancer_screening', r['record_id'], r['event_date']) for r in rows]
    notes = ['final results in cancer_screening; claim-billed rows (no result) are listed separately and are not counted as results', win_note(window)]
    if concept and concept.get('colonoscopy') or (concept and concept['value'] == 'FIT'):
        frag2, p2 = CI.window_sql('service_date', window)
        cpts = ('45378', '45380', '45385') if concept.get('colonoscopy') else ('82274',)
        marks = ','.join('?' * len(cpts))
        procs = ch.rows(f'SELECT record_id, service_date, cpt FROM procedure_line WHERE patient_id = ? AND cpt IN ({marks}) AND counts_in_metrics = 1{frag2} ORDER BY service_date DESC', ch.pid, *cpts, *p2)
        if concept.get('colonoscopy'):
            final, table, sources = [], [], []
        for r in procs:
            table.append({'date': r['service_date'], 'test': f"CPT {r['cpt']} (billed procedure)", 'result_status': 'claim_billed'})
            sources.append(src('procedure_line', r['record_id'], r['service_date']))
            billed.append({'event_date': r['service_date'], 'result_status': 'claim_billed'})
        if concept.get('colonoscopy'):
            notes.append('colonoscopy appears in this warehouse only as billed procedure lines (CPT 45378 / 45380 / 45385), never as a result')
    if intent == 'count_in_window':
        return core('count', f'{len(final)} final {label} result(s) for {ch.pid} ({CI.describe_window(window)})', value=len(final), count=len(final), table=table, sources=sources, notes=notes)
    if intent == 'status':
        yes = bool(final)
        head = (f"Yes: {ch.pid} has a final {label} result on {final[0]['event_date']}" if yes else
                f"No: no final {label} result for {ch.pid} ({CI.describe_window(window)})" + (f"; {len(billed)} billed claim row(s) without a result" if billed else ''))
        return core('yes_no', head, value=yes, count=len(final), table=table[:6], sources=sources[:4], notes=notes)
    if intent == 'last_event':
        if final:
            return core('date', f"Last {label} for {ch.pid}: {final[0]['event_date']} (final result)", value=final[0]['event_date'], table=table[:5], sources=sources[:2], notes=notes,
                        facts={'date': final[0]['event_date'], 'later_billed': [b['event_date'] for b in billed if b['event_date'] > final[0]['event_date']][:3]})
        if billed:
            return core('none', f"No final {label} result for {ch.pid}; the latest billed claim row is {max(b['event_date'] for b in billed)} (billing only, no result)", table=table[:5], sources=sources[:2], notes=notes)
        return no_data(f'No {label} on record for {ch.pid} ({CI.describe_window(window)})', notes=notes)
    return core('table', f'{len(rows)} {label} row(s) for {ch.pid}', count=len(rows), table=table, sources=sources, notes=notes)


def wellness(ch, it):
    window, intent = it.get('window'), it['intent']
    wfrag, wparams = CI.window_sql('event_date', window)
    rows = ch.rows(f'SELECT record_id, visit_type, awv_kind, status, event_date, source FROM wellness_visit WHERE patient_id = ?{wfrag} ORDER BY event_date DESC', ch.pid, *wparams)
    table = [{'date': r['event_date'], 'type': r['visit_type'], 'kind': r['awv_kind'], 'status': r['status']} for r in rows]
    sources = [src('wellness_visit', r['record_id'], r['event_date']) for r in rows]
    notes = ['annual wellness visits (wellness_visit)', win_note(window)]
    if intent == 'status':
        return core('yes_no', f"{'Yes' if rows else 'No'}: {len(rows)} annual wellness visit(s) for {ch.pid} ({CI.describe_window(window)})", value=bool(rows), count=len(rows), table=table[:5], sources=sources[:3], notes=notes)
    if intent == 'count_in_window':
        return core('count', f'{len(rows)} annual wellness visit(s) for {ch.pid} ({CI.describe_window(window)})', value=len(rows), count=len(rows), table=table, sources=sources, notes=notes)
    if intent == 'last_event':
        if not rows:
            return no_data(f'No annual wellness visit for {ch.pid} ({CI.describe_window(window)})', notes=notes)
        return core('date', f"Last annual wellness visit for {ch.pid}: {rows[0]['event_date']}", value=rows[0]['event_date'], table=table[:5], sources=sources[:1], notes=notes)
    return core('table', f'{len(rows)} annual wellness visit(s) for {ch.pid}', count=len(rows), table=table, sources=sources, notes=notes)


# -- allergies, conditions, problems, smoking ------------------------------------------------------------------------------------
def allergies(ch, it):
    rows = ch.rows('SELECT record_id, substance, substance_received, reaction, status, no_known_allergies, first_recorded, last_confirmed FROM allergy_entry WHERE patient_id = ?', ch.pid)
    real = [r for r in rows if not r['no_known_allergies']]
    nkda = [r for r in rows if r['no_known_allergies']]
    table = [{'substance': r['substance'], 'reaction': r['reaction'], 'recorded': r['substance_received'], 'last_confirmed': r['last_confirmed']} for r in real]
    sources = [src('allergy_entry', r['record_id'], r['last_confirmed']) for r in rows]
    if not rows:
        return no_data(f'No allergy entry is recorded for {ch.pid} (not the same as no known allergies)')
    concept = it.get('concept')
    if it['intent'] == 'status' and concept and concept.get('kind') == 'substance':
        term = concept['value']
        family = ALLERGY_FAMILIES.get(term, ())
        hits = [r for r in real if term in (r['substance'] or '').lower() or term in (r['substance_received'] or '').lower()
                or any(f in (r['substance'] or '').lower() for f in family)]
        listed = ', '.join(r['substance'] for r in real) or 'no known allergies'
        if hits:
            h = hits[0]
            return core('yes_no', f"Yes: {ch.pid} has a recorded {term} allergy entry ({h['substance_received'] or h['substance']})", value=True, count=len(hits),
                        table=[{'substance': r['substance'], 'reaction': r['reaction']} for r in hits], sources=[src('allergy_entry', r['record_id'], r['last_confirmed']) for r in hits],
                        notes=['allergy list entries (allergy_entry)' + ('; family match: ' + ', '.join(family) if family else '')])
        return core('yes_no', f"No: no {term} allergy is recorded for {ch.pid} (the allergy list has: {listed})", value=False, count=0, sources=sources,
                    notes=['allergy list entries (allergy_entry)'])
    if not real:
        return core('list', f'No known allergies recorded for {ch.pid}', count=0, table=[], sources=sources, facts={'substances': []}, notes=['allergy list entry "no known allergies"'])
    head = f"{len(real)} recorded allerg{'y' if len(real) == 1 else 'ies'} for {ch.pid}: " + '; '.join(
        f"{r['substance']}" + (f" ({r['reaction']})" if r['reaction'] else '') for r in real)
    return core('list', head, count=len(real), table=table, sources=sources, facts={'substances': [r['substance'] for r in real]}, notes=['allergy list entries (allergy_entry)'])


ALLERGY_FAMILIES = {'penicillin': ('penicillin', 'amoxicillin', 'ampicillin', 'augmentin', 'dicloxacillin'), 'sulfa': ('sulfa', 'sulfamethoxazole', 'bactrim', 'trimethoprim'),
                    'nsaid': ('ibuprofen', 'naproxen', 'aspirin', 'nsaid'), 'cephalosporin': ('cephalexin', 'cefazolin', 'ceftriaxone', 'cefuroxime', 'cephalosporin')}


def tables_of(ch, names):
    return {n: ch.columns(n) for n in names}


def condition(ch, it):
    from . import concepts
    concept, intent = it.get('concept'), it['intent']
    if concept and concept.get('value'):
        name = concept['value']
        label = concepts.CONDITIONS[name]['label'].split(' (')[0]
        sql = DC.condition_evidence_sql([name], tables_of(ch, ['condition', 'payer_claim_diagnosis']))
        ev = ch.rows(f'SELECT source, count(*) AS n, min(event_date) AS first_date, max(event_date) AS last_date FROM {sql} WHERE patient_id = ? GROUP BY source', ch.pid) if sql else []
        pl = ch.rows("SELECT label, icd10, clinical_status, first_recorded, last_confirmed, record_id FROM problem_list_entry WHERE patient_id = ? AND ("
                     + ' OR '.join(["icd10 LIKE ?"] * len(concepts.CONDITIONS[name]['icd10'])) + ')', ch.pid, *[p + '%' for p in concepts.CONDITIONS[name]['icd10']])
        has = bool(ev)
        notes = ['condition evidence = EHR diagnosis (incl. problem-list rows with an unknown flag) or a counted payer-claim diagnosis; diabetes and CKD also count laboratory evidence; rule-outs never count']
        table = [{'source': e['source'], 'records': e['n'], 'first': e['first_date'], 'last': e['last_date']} for e in ev]
        sources = [src('condition', None, e['last_date'], evidence=e['source']) for e in ev] + [src('problem_list_entry', p['record_id'], p['last_confirmed']) for p in pl]
        if True:
            if has:
                first = min(e['first_date'] for e in ev if e['first_date']) if any(e['first_date'] for e in ev) else None
                head = f"Yes: {ch.pid} has {label} on record (" + '; '.join(f"{e['source']}: {e['n']} record(s), latest {e['last_date']}" for e in ev) + ')'
            else:
                head = f'No: no {label} evidence on record for {ch.pid}' + (f" (problem list shows {pl[0]['label']}, status {pl[0]['clinical_status']})" if pl else '')
            return core('yes_no', head, value=has, count=sum(e['n'] for e in ev), table=table, sources=sources, notes=notes,
                        facts={'evidence_sources': [e['source'] for e in ev], 'problem_list': [p['label'] for p in pl]})
    # all conditions: library concepts with evidence plus the problem list
    cp = ch.rows("SELECT concept, records, first_date, last_date FROM concept_patient WHERE patient_id = ? AND kind = 'condition' ORDER BY last_date DESC", ch.pid)
    pl = problem_rows(ch)
    table = [{'condition': c['concept'].replace('_', ' '), 'records': c['records'], 'first': c['first_date'], 'last': c['last_date']} for c in cp]
    names = [c['concept'].replace('_', ' ') for c in cp]
    head = (f"{len(cp)} library condition(s) with evidence for {ch.pid}: " + ', '.join(names)) if cp else f'No library condition has evidence for {ch.pid}'
    if pl:
        head += f"; problem list: {', '.join(p['label'] for p in pl[:10])}"
    return core('list', head, count=len(cp), table=table + [{'condition': p['label'], 'source': 'problem list', 'icd10': p['icd10'], 'status': p['clinical_status']} for p in pl],
                sources=[src('concept_patient', ch.pid, c['last_date'], concept=c['concept']) for c in cp] + [src('problem_list_entry', p['record_id'], p['last_confirmed']) for p in pl],
                facts={'conditions': [c['concept'] for c in cp], 'problems': [p['label'] for p in pl]},
                notes=['condition evidence = EHR diagnosis or counted payer-claim diagnosis (plus laboratory evidence for diabetes and CKD)'])


def problem_rows(ch):
    return ch.rows("SELECT label, icd10, clinical_status, first_recorded, last_confirmed, resolved_date, record_id FROM problem_list_entry WHERE patient_id = ? "
                   "AND clinical_status = 'active' ORDER BY last_confirmed DESC", ch.pid)


def problem_list(ch, it):
    rows = problem_rows(ch)
    table = [{'problem': r['label'], 'icd10': r['icd10'], 'status': r['clinical_status'], 'first_recorded': r['first_recorded'], 'last_confirmed': r['last_confirmed']} for r in rows]
    head = (f"{len(rows)} active problem(s) for {ch.pid}: " + '; '.join(r['label'] for r in rows)) if rows else f'No active problem-list entry for {ch.pid}'
    return core('list', head, count=len(rows), table=table, sources=[src('problem_list_entry', r['record_id'], r['last_confirmed']) for r in rows],
                facts={'problems': [r['label'] for r in rows]}, notes=['active problem-list entries (problem_list_entry)'])


def smoking(ch, it):
    r = ch.one('SELECT status, status_received, status_date, record_id FROM smoking_status WHERE patient_id = ?', ch.pid)
    if not r:
        return no_data(f'No structured tobacco status for {ch.pid}')
    detail = f"{r['status']} (recorded {r['status_date']}; as written: {r['status_received']})"
    if it.get('intent') == 'status':
        current = str(r['status']).lower() in ('current', 'current_every_day', 'current_some_day', 'smoker')
        return core('yes_no', f"{'Yes' if current else 'No'}: tobacco status of {ch.pid} is {detail}", value=current, sources=[src('smoking_status', r['record_id'], r['status_date'])])
    return core('label', f"Tobacco status of {ch.pid}: {detail}", value=r['status'], sources=[src('smoking_status', r['record_id'], r['status_date'])])


# -- social --------------------------------------------------------------------------------------------------------------------
def social_needs(ch, it):
    concept, intent, window = it.get('concept'), it['intent'], it.get('window')
    wfrag, wparams = CI.window_sql('event_date', window)
    cfrag, cparams = (' AND need = ?', [concept['value']]) if concept else ('', [])
    rows = ch.rows(f'SELECT record_id, need, positive, instrument, event_date FROM social_need WHERE patient_id = ?{cfrag}{wfrag} ORDER BY event_date DESC', ch.pid, *cparams, *wparams)
    pos = [r for r in rows if r['positive']]
    by = {}
    for r in pos:
        by.setdefault(r['need'], []).append(r)
    table = [{'need': k, 'positive_screens': len(v), 'latest_positive': v[0]['event_date'], 'instrument': v[0]['instrument']} for k, v in by.items()]
    sources = [src('social_need', v[0]['record_id'], v[0]['event_date']) for v in by.values()]
    notes = ['screened-positive need = a social_need row with positive = 1 (any positive screen in the window)', win_note(window)]
    if intent == 'status' and concept:
        screened = [r for r in rows]
        yes = bool(pos)
        head = (f"Yes: {ch.pid} screened positive for {concept['value'].replace('_', ' ')} ({len(pos)} positive screen(s), latest {pos[0]['event_date']})" if yes else
                f"No: no positive {concept['value'].replace('_', ' ')} screen for {ch.pid} ({CI.describe_window(window)}; {len(screened)} screen(s) on record)")
        return core('yes_no', head, value=yes, count=len(pos), table=table, sources=sources or [src('social_need', r['record_id'], r['event_date']) for r in screened[:3]], notes=notes)
    head = (f"{len(by)} positive social need(s) for {ch.pid}: " + ', '.join(f"{k.replace('_', ' ')} (latest {v[0]['event_date']})" for k, v in by.items())) if by else f'No positive social-need screen for {ch.pid} ({CI.describe_window(window)})'
    return core('list', head, count=len(by), table=table, sources=sources, facts={'needs': list(by)}, notes=notes)


def sdoh_vendor(ch, it):
    r = ch.one("SELECT record_id, vendor_name, delivery_date, match_status, match_confidence, svi_overall, est_household_income_band, homeowner_status, food_access_low, "
               "financial_stress_score, social_isolation_score, transportation_risk_score, housing_instability_score, broadband_access_score, health_literacy_estimate "
               "FROM sdoh_vendor WHERE patient_id = ? AND match_status IN ('exact', 'partial_address') ORDER BY delivery_date DESC, record_id DESC LIMIT 1", ch.pid)
    if not r:
        return no_data(f'No matched purchased-SDOH row for {ch.pid}')
    scores = {k: r[k] for k in ('financial_stress_score', 'social_isolation_score', 'transportation_risk_score', 'housing_instability_score', 'broadband_access_score')}
    head = (f"Purchased SDOH for {ch.pid} ({r['delivery_date']}, {r['match_status']} match): " + ', '.join(f"{k.replace('_score', '').replace('_', ' ')} {fmt(v)}" for k, v in scores.items())
            + f"; food access low {bool(r['food_access_low'])}; income band {r['est_household_income_band']}")
    return core('table', head, table=[{'measure': k, 'value': v} for k, v in scores.items()], sources=[src('sdoh_vendor', r['record_id'], r['delivery_date'])],
                facts={**scores, 'food_access_low': bool(r['food_access_low'])}, notes=['latest matched vendor delivery; scores 1-10 (7 or higher is high risk)'])


def svi(ch, it):
    r = ch.one('SELECT county_fips, svi_overall, svi_socioeconomic, svi_household, svi_minority_language, svi_housing_transportation FROM patient_svi WHERE patient_id = ?', ch.pid)
    if not r or r['svi_overall'] is None:
        return no_data(f'No social vulnerability index for {ch.pid}')
    high = r['svi_overall'] >= HIGH_SVI
    return core('value', f"Social vulnerability index (overall) of {ch.pid}'s county {r['county_fips']}: {fmt(r['svi_overall'])} percentile ({'high, 75 or above' if high else 'below the high-vulnerability line of 75'})",
                value=num(r['svi_overall']), unit='percentile', facts={**r, 'high': high}, sources=[src('patient_svi', ch.pid)],
                table=[{'theme': k.replace('svi_', ''), 'value': v} for k, v in r.items() if k.startswith('svi_')])


# -- claims --------------------------------------------------------------------------------------------------------------------
def claims(ch, it):
    window, intent, attr = it.get('window'), it['intent'], it.get('attribute') or 'paid'
    wfrag, wparams = CI.window_sql('service_from', window)
    rows = ch.rows(f'SELECT payer_claim_id, service_from, claim_type, final_status, paid_cents, allowed_cents, charge_cents, patient_responsibility_cents, record_id '
                   f'FROM payer_claim WHERE patient_id = ? AND counts_in_metrics = 1{wfrag} ORDER BY service_from DESC', ch.pid, *wparams)
    notes = ['payer claims counted in metrics (current version, no duplicates); paid amount is net of reversals and recoupments; dated by service date', win_note(window)]
    sources = [src('payer_claim', r['payer_claim_id'], r['service_from']) for r in rows]
    totals = {'paid': usd(sum(r['paid_cents'] or 0 for r in rows)), 'allowed': usd(sum(r['allowed_cents'] or 0 for r in rows)),
              'billed': usd(sum(r['charge_cents'] or 0 for r in rows)), 'patient_responsibility': usd(sum(r['patient_responsibility_cents'] or 0 for r in rows))}
    by = {}
    for r in rows:
        b = by.setdefault(r['claim_type'] or 'unknown', {'claim_type': r['claim_type'] or 'unknown', 'claims': 0, 'paid': 0})
        b['claims'] += 1
        b['paid'] = round(b['paid'] + usd(r['paid_cents']), 2)
    table = sorted(by.values(), key=lambda b: -b['paid'])
    if intent == 'count_in_window':
        if attr == 'denied':
            n = sum(1 for r in rows if r['final_status'] in ('denied', 'denied_upheld'))
            return core('count', f'{n} denied claim(s) for {ch.pid} ({CI.describe_window(window)})', value=n, count=n, sources=sources, notes=notes)
        return core('count', f'{len(rows)} counted claim(s) for {ch.pid} ({CI.describe_window(window)})', value=len(rows), count=len(rows), table=table, sources=sources, notes=notes)
    if intent == 'list':
        return core('table', f'{len(rows)} counted claim(s) for {ch.pid} ({CI.describe_window(window)}), plan paid ${totals["paid"]:,.2f}', count=len(rows),
                    table=[{'date': r['service_from'], 'type': r['claim_type'], 'status': r['final_status'], 'paid': usd(r['paid_cents'])} for r in rows], sources=sources, facts=totals, notes=notes)
    names = {'paid': 'Plan paid', 'allowed': 'Allowed amount', 'billed': 'Billed charges', 'patient_responsibility': 'Patient responsibility'}
    key = attr if attr in totals else 'paid'
    return core('value', f"{names[key]} in claims for {ch.pid} ({CI.describe_window(window)}): ${totals[key]:,.2f} across {len(rows)} claim(s)", value=totals[key], unit='USD',
                table=table, facts={**totals, 'claims': len(rows)}, sources=sources, notes=notes)


def pharmacy_claims(ch, it):
    window = it.get('window')
    wfrag, wparams = CI.window_sql('fill_date', window)
    rows = ch.rows(f'SELECT record_id, fill_date, drug_name, days_supply, plan_paid_cents, patient_pay_cents FROM payer_pharmacy_claim WHERE patient_id = ? AND counts_as_fill = 1{wfrag} '
                   f'ORDER BY fill_date DESC', ch.pid, *wparams)
    paid = usd(sum(r['plan_paid_cents'] or 0 for r in rows))
    pay = usd(sum(r['patient_pay_cents'] or 0 for r in rows))
    sources = [src('payer_pharmacy_claim', r['record_id'], r['fill_date']) for r in rows]
    notes = ['pharmacy claims that count as fills (paid, not reversed)', win_note(window)]
    if it['intent'] == 'count_in_window':
        return core('count', f'{len(rows)} pharmacy claim fill(s) for {ch.pid} ({CI.describe_window(window)})', value=len(rows), count=len(rows), sources=sources, notes=notes)
    if it['intent'] == 'list':
        return core('table', f'{len(rows)} pharmacy claim fill(s) for {ch.pid}; plan paid ${paid:,.2f}', count=len(rows),
                    table=[{'date': r['fill_date'], 'drug': r['drug_name'], 'plan_paid': usd(r['plan_paid_cents']), 'patient_pay': usd(r['patient_pay_cents'])} for r in rows], sources=sources, notes=notes)
    return core('value', f"Pharmacy plan spend for {ch.pid} ({CI.describe_window(window)}): ${paid:,.2f} plan paid, ${pay:,.2f} patient pay, {len(rows)} fill(s)", value=paid, unit='USD',
                facts={'plan_paid': paid, 'patient_pay': pay, 'fills': len(rows)}, sources=sources, notes=notes)


EXECUTORS = {
    'lab': lab, 'demographics': demographics, 'practice': practice, 'coverage': coverage, 'death': death,
    'ed_visit': utilization, 'inpatient': utilization, 'observation': utilization, 'readmission': readmission, 'visit': visits, 'appointment': appointments,
    'medication': medication, 'fill': fills, 'prescription': prescriptions, 'returned_fill': returned_fills,
    'immunization': immunizations, 'screening': screenings, 'wellness': wellness, 'allergy': allergies, 'condition': condition, 'problem_list': problem_list,
    'smoking': smoking, 'social_need': social_needs, 'sdoh_vendor': sdoh_vendor, 'svi': svi, 'claims': claims, 'pharmacy_claim': pharmacy_claims,
}


def run(ch, it):
    """Execute one typed intent. -> Core dict, or None when the executor cannot answer this intent/domain pair."""
    fn = EXECUTORS.get(it.get('domain'))
    return fn(ch, it) if fn else None
