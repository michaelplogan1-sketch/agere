"""Declared retraction rules: when a new warehouse release may drop records that a prior release delivered.

A release that retracts prior records (healthcare-warehouse v2.6 onward removes deep-chart care dated after a patient's
death) is accepted only when every missing prior record is explained by a declared rule. One rule is declared:

post-death/1: the prior record's patient has a death in the NEW release, and the record is dated after that death.
  * Date of death comes from client-visible streams of the new release only (never private/): the `date_of_death` of
    `death_records` and of `demographic_updates` marked deceased (earliest when several). When neither exists, the
    `coverage_end` of an `enrollment_spans` row with termination_reason=death is used; coverage ends at or after the death
    (month end), so this upper bound can only explain fewer records, never more.
  * The record's date is the earliest of its event_date and any service-date fact (SERVICE_DATE_FIELDS). Billing for
    a service delivered on or before the death therefore stays unexplained even when the bill itself is dated later.
  * Undated records, records of patients absent from the new release, and records on or before the death are unexplained.

classify() takes the missing prior rows and the new release's death dates and returns the audit block (counts by stream,
unexplained counts by stream and reason, examples). It reads nothing itself.
"""
import json
from collections import Counter
from datetime import date

RULE = 'post-death/1'
SERVICE_DATE_FIELDS = ('service_date', 'service_from', 'service_from_date', 'service_start_date', 'date_of_service',
                       'admission_date', 'fill_date', 'date_filled', 'dispense_date')
EXAMPLES = 25


def _iso(value):
    """YYYY-MM-DD for a date or ISO timestamp string, else None."""
    if not isinstance(value, str) or len(value) < 10: return None
    try: return date.fromisoformat(value[:10]).isoformat()
    except ValueError: return None


def death_dates(db, schema='main'):
    """{patient_id: (date_of_death, basis)} from the client-visible streams of the release attached as `schema`."""
    reported, terminated = {}, {}
    for pid, table, value in db.execute(
            f"SELECT patient_id, source_table, json_extract(facts, '$.date_of_death') FROM {schema}.source_records "
            "WHERE source_table IN ('death_records', 'demographic_updates') AND json_extract(facts, '$.date_of_death') IS NOT NULL"):
        d = _iso(value)
        if d and (pid not in reported or d < reported[pid][0]): reported[pid] = (d, table)
    for pid, value in db.execute(
            f"SELECT patient_id, json_extract(facts, '$.coverage_end') FROM {schema}.source_records "
            "WHERE source_table = 'enrollment_spans' AND json_extract(facts, '$.termination_reason') = 'death'"):
        d = _iso(value)
        if d and (pid not in terminated or d < terminated[pid][0]): terminated[pid] = (d, 'enrollment_spans.coverage_end')
    return {**terminated, **reported}


def record_date(event_date, facts):
    """Earliest of the event date and any service-date fact, or None when the record carries no date."""
    if isinstance(facts, str):
        try: facts = json.loads(facts)
        except ValueError: facts = {}
    service = [facts.get(k) for k in SERVICE_DATE_FIELDS] if isinstance(facts, dict) else []
    dates = [d for d in map(_iso, [event_date, *service]) if d]
    return min(dates) if dates else None


def classify(missing, deaths, roster):
    """missing: iterable of (record_id, patient_id, source_table, event_date, facts) prior rows absent from the new release.
    deaths: death_dates() of the new release. roster: patient ids of the new release."""
    retracted, unexplained, reasons, patients, examples = Counter(), Counter(), Counter(), set(), []
    for rid, pid, table, event_date, facts in missing:
        death = deaths.get(pid)
        when = record_date(event_date, facts)
        if pid not in roster: reason = 'patient_absent_from_new_release'
        elif death is None: reason = 'no_death_in_new_release'
        elif when is None: reason = 'undated'
        elif when <= death[0]: reason = 'service_on_or_before_death' if (_iso(event_date) or '') > death[0] else 'dated_on_or_before_death'
        else:
            retracted[table] += 1; patients.add(pid); continue
        unexplained[table] += 1; reasons[reason] += 1
        if len(examples) < EXAMPLES:
            examples.append({'record_id': rid, 'patient_id': pid, 'source_table': table, 'event_date': event_date,
                             'record_date': when, 'date_of_death': death[0] if death else None, 'reason': reason})
    deceased = Counter(basis for _, basis in deaths.values())
    return {'retraction_rule': RULE, 'prior_rows_retracted': sum(retracted.values()),
            'retracted_by_table': dict(retracted.most_common()), 'retracted_patients': len(patients),
            'prior_rows_unexplained': sum(unexplained.values()), 'unexplained_by_table': dict(unexplained.most_common()),
            'unexplained_by_reason': dict(reasons.most_common()), 'unexplained_examples': examples,
            'deceased_patients_in_new_release': len(deaths), 'death_date_basis': dict(deceased)}


def missing_rows(db, prior='previous'):
    """Prior source_records rows whose record_id is absent from main (both releases attached to db)."""
    return db.execute(f'SELECT p.record_id, p.patient_id, p.source_table, p.event_date, p.facts FROM {prior}.source_records p '
                      'LEFT JOIN main.source_records n ON n.record_id = p.record_id WHERE n.record_id IS NULL ORDER BY p.record_id')


def validate(migration):
    """Raise unless the audit's retraction block shows every missing prior row explained by the declared rule."""
    keys = ('prior_rows_missing', 'prior_rows_retracted', 'prior_rows_unexplained')
    if migration.get('retraction') is not True or migration.get('retraction_rule') != RULE:
        raise ValueError('Retraction activation needs a retraction migration audit (declared rule ' + RULE + ')')
    if not all(type(migration.get(k)) is int and migration[k] >= 0 for k in keys):
        raise ValueError('Retraction audit must count missing, retracted and unexplained prior rows')
    missing, retracted, unexplained = (migration[k] for k in keys)
    if unexplained:
        raise ValueError(f'Retraction leaves {unexplained} missing prior rows unexplained by {RULE}: '
                         f"{migration.get('unexplained_by_reason')}")
    by_table = migration.get('retracted_by_table')
    if not isinstance(by_table, dict) or sum(by_table.values()) != retracted or retracted != missing:
        raise ValueError('Retracted prior rows must be counted per table and account for every missing row')
    return retracted
