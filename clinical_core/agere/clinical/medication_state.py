"""Medication state reconciliation: one governed classification of each patient x drug class (or GDMT pillar).

Reconciles the three medication sources Agere receives into one state with its evidence dates:
  filled_current                    a picked-up fill (pharmacy dispense, provider or payer pharmacy claim; reversals and
                                    returns to stock never count) whose days' supply covers the as-of date plus GRACE_DAYS;
  documented_stopped_or_intolerant  a note therapy change (stop / declined) or an extracted therapy barrier (intolerance,
                                    contraindication, allergy, cost, declined, not indicated) dated on or after the latest
                                    fill and listing; an undated one counts only when nothing else is recorded;
  listed_not_filled                 on the medication list, ordered, reported or administered, or filled before, with no fill
                                    covering the as-of date (+ grace). For "not on X" questions this is "likely not taking",
                                    shown with its last fill and last listing dates;
  never_recorded                    no medication record of any source for that class: the only "unknown".
Used by GDMT-pillar questions (Brief 1.4: a patient with all four pillars on the list and no fill since 2029 is
listed_not_filled for each pillar, never "unknown"). Exposed three ways from this one SQL definition: the governed
macro {{medication_state:<subjects>}} (general analysis), the registered engine operation 'medication_state'
(graph planner, typed-request analyses, operator router) and classify() for direct use.
"""
from pathlib import Path
import re
import sqlite3

VERSION = 'medication-state/1'
STATES = ('filled_current', 'documented_stopped_or_intolerant', 'listed_not_filled', 'never_recorded')
GRACE_DAYS = 30
DEFAULT_DAYS_SUPPLY = 30
FILL_EVIDENCE = ('dispensed', 'claim', 'payer_claim')
STOP_ACTIONS = ('stop', 'declined')
# GDMT pillars (2022 AHA/ACC/HFSA HFrEF guideline) -> received drug classes; therapy_barrier.gdmt_pillar uses the same names.
PILLARS = {'rasi': ['ace_inhibitor', 'arb', 'arni'], 'beta_blocker': ['hf_evidence_based_beta_blocker'],
           'mra': ['mra'], 'sglt2i': ['sglt2_inhibitor']}
GROUPS = {'gdmt': list(PILLARS)}
OUTPUTS = ('patients', 'summary', 'rows')
DISCLOSURE = (f'Medication state (governed {VERSION}): filled_current = a picked-up fill whose days supply covers the as-of date '
              f'plus {GRACE_DAYS} days (missing days supply = {DEFAULT_DAYS_SUPPLY}); documented_stopped_or_intolerant = a note '
              'stop/declined or an extracted therapy barrier dated on or after the latest fill and listing; listed_not_filled = '
              'listed, ordered or previously filled with no covering fill (likely not taking); never_recorded = no record of any '
              'source. A pickup is recorded supply, not confirmed ingestion.')


def subjects(names):
    """['gdmt', 'statin', 'rasi', ...] -> {subject: [drug classes]} (a group expands to its pillars)."""
    from . import drug_classes
    if isinstance(names, str): names = [n for n in re.split(r'[,\s]+', names) if n]
    out = {}
    for n in names or []:
        n = str(n).strip().lower()
        if n in GROUPS: out.update({p: PILLARS[p] for p in GROUPS[n]})
        elif n in PILLARS: out[n] = PILLARS[n]
        elif n in drug_classes.CLASSES: out[n] = [n]
        else: raise ValueError(f'Unknown medication subject: {n}. Use gdmt, a pillar ({", ".join(PILLARS)}) or a drug class.')
    if not out: raise ValueError('medication_state needs at least one subject (gdmt, a pillar or a drug class)')
    return out


def _q(v): return "'" + str(v).replace("'", "''") + "'"


def state_sql(subject_map, as_of, tables, *, grace=GRACE_DAYS, population=False):
    """One SELECT: patient_id, subject, state, last_fill_date, covered_through, last_listed_date, stop_date, stop_evidence,
    evidence_record_ids, basis. tables: {table: columns} of the warehouse (absent sources are simply not read)."""
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(as_of or '')): raise ValueError('medication_state needs an explicit as-of date')
    if 'medication' not in tables: raise ValueError('No medication relation was received; medication state cannot be classified')
    from .concepts import picked_up_sql
    a, g = _q(as_of), int(grace)
    pairs = ', '.join(f'({_q(s)}, {_q(c)}, {_q(s) if s in PILLARS else "NULL"})' for s, cs in subject_map.items() for c in cs)
    supply = (f"coalesce(CAST(json_extract(r.payload, '$.days_supply_reported') AS INTEGER), "
              f"CAST(json_extract(r.payload, '$.days_supply') AS INTEGER), {DEFAULT_DAYS_SUPPLY})"
              if 'record' in tables else str(DEFAULT_DAYS_SUPPLY))
    record_join = 'LEFT JOIN record r ON r.record_id = m.record_id ' if 'record' in tables else ''
    fill = picked_up_sql('m', reversed_column='fill_reversed' in tables['medication'])
    stops = []
    if 'note_therapy_change' in tables:
        stops.append("SELECT n.patient_id, s.subject, substr(n.event_date, 1, 10) AS day, 'note_' || n.action AS kind, n.record_id "
                     "FROM note_therapy_change n JOIN ms_subject s ON s.drug_class = n.drug_class "
                     f"WHERE n.action IN ({', '.join(map(_q, STOP_ACTIONS))})")
    if 'therapy_barrier' in tables:
        stops.append("SELECT b.patient_id, s.subject, substr(coalesce(b.last_date, b.event_date), 1, 10) AS day, "
                     "'barrier_' || b.barrier_type AS kind, b.record_id FROM therapy_barrier b JOIN ms_subject s "
                     "ON s.drug_class = b.drug_class OR (s.pillar IS NOT NULL AND s.pillar = b.gdmt_pillar)")
    stop_rel = (' UNION ALL '.join(stops) if stops else
                'SELECT NULL AS patient_id, NULL AS subject, NULL AS day, NULL AS kind, NULL AS record_id WHERE 0')
    scope = ' WHERE p.patient_id IN (SELECT patient_id FROM population)' if population else ''
    return (
        f"SELECT * FROM (WITH ms_subject(subject, drug_class, pillar) AS (VALUES {pairs}), "
        f"ms_fill AS (SELECT m.patient_id, s.subject, substr(m.event_date, 1, 10) AS day, max({supply}) AS supply, "
        f"group_concat(m.record_id) AS records FROM medication m JOIN ms_subject s ON s.drug_class = m.drug_class {record_join}"
        f"WHERE {fill} AND m.event_date IS NOT NULL AND substr(m.event_date, 1, 10) <= {a} GROUP BY 1, 2, 3), "
        "ms_filled AS (SELECT patient_id, subject, max(day) AS last_fill, "
        "max(date(day, '+' || (max(supply, 1) - 1) || ' days')) AS covered_through, group_concat(records) AS records "
        "FROM ms_fill GROUP BY 1, 2), "
        f"ms_listed AS (SELECT m.patient_id, s.subject, max(substr(m.event_date, 1, 10)) AS last_listed, "
        f"group_concat(m.record_id) AS records FROM medication m JOIN ms_subject s ON s.drug_class = m.drug_class "
        f"WHERE m.evidence NOT IN ({', '.join(map(_q, FILL_EVIDENCE))}) AND (m.event_date IS NULL OR substr(m.event_date, 1, 10) <= {a}) "
        "GROUP BY 1, 2), "
        f"ms_stop AS (SELECT patient_id, subject, max(day) AS stop_date, "
        "group_concat(kind || '@' || coalesce(day, 'undated') || coalesce('#' || record_id, ''), ' | ') AS stop_evidence, "
        f"group_concat(record_id) AS records FROM (SELECT DISTINCT * FROM ({stop_rel})) WHERE day IS NULL OR day <= {a} GROUP BY 1, 2), "
        f"ms_base AS (SELECT p.patient_id, s.subject FROM patient p CROSS JOIN (SELECT DISTINCT subject FROM ms_subject) s{scope}) "
        "SELECT b.patient_id, b.subject, CASE "
        "WHEN st.stop_date IS NOT NULL AND st.stop_date >= coalesce(f.last_fill, '') AND st.stop_date >= coalesce(l.last_listed, '') "
        "THEN 'documented_stopped_or_intolerant' "
        f"WHEN f.covered_through IS NOT NULL AND date(f.covered_through, '+{g} days') >= {a} THEN 'filled_current' "
        "WHEN f.patient_id IS NOT NULL OR l.patient_id IS NOT NULL THEN 'listed_not_filled' "
        "WHEN st.patient_id IS NOT NULL THEN 'documented_stopped_or_intolerant' ELSE 'never_recorded' END AS state, "
        "f.last_fill AS last_fill_date, f.covered_through, l.last_listed AS last_listed_date, st.stop_date, st.stop_evidence, "
        "trim(coalesce(f.records, '') || ',' || coalesce(l.records, '') || ',' || coalesce(st.records, ''), ',') AS evidence_record_ids, "
        "CASE WHEN st.stop_date IS NOT NULL AND st.stop_date >= coalesce(f.last_fill, '') AND st.stop_date >= coalesce(l.last_listed, '') "
        "THEN 'documented ' || st.stop_evidence "
        f"WHEN f.covered_through IS NOT NULL AND date(f.covered_through, '+{g} days') >= {a} "
        f"THEN 'fill ' || f.last_fill || ' covers through ' || f.covered_through || ' (+{g}d grace)' "
        "WHEN l.patient_id IS NOT NULL THEN 'listed/ordered ' || coalesce(l.last_listed, 'undated') || ', last fill ' || coalesce(f.last_fill, 'none') "
        "WHEN f.patient_id IS NOT NULL THEN 'fill lapsed: last fill ' || f.last_fill || ' covered through ' || f.covered_through "
        "WHEN st.patient_id IS NOT NULL THEN 'documented (undated) ' || st.stop_evidence ELSE 'no record of any source' END AS basis "
        "FROM ms_base b LEFT JOIN ms_filled f ON f.patient_id = b.patient_id AND f.subject = b.subject "
        "LEFT JOIN ms_listed l ON l.patient_id = b.patient_id AND l.subject = b.subject "
        "LEFT JOIN ms_stop st ON st.patient_id = b.patient_id AND st.subject = b.subject)")


def macro_sql(arg, catalog):
    """{{medication_state:gdmt}} for staged analyses: a relation over the (population-scoped) catalog tables."""
    try:
        return '(' + state_sql(subjects(arg or 'gdmt'), catalog.identity.get('as_of'), catalog.tables) + ')'
    except ValueError as exc:
        from .request_plan import PlanError
        raise PlanError(str(exc)) from exc


def _tables(db):
    names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")]
    return {n: {r[1] for r in db.execute(f'PRAGMA table_info("{n}")')} for n in names}


def classify(db, as_of, names, keep=None, grace=GRACE_DAYS):
    """Rows of state_sql on an open semantic-layer connection, restricted to keep (patient ids) when given."""
    sql = state_sql(subjects(names), as_of, _tables(db), grace=grace)
    db.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in db.execute(sql)]
    finally:
        db.row_factory = None
    return [r for r in rows if keep is None or r['patient_id'] in keep]


# ---- registered engine ----------------------------------------------------------------------------------------------
def validate(p):
    if not isinstance(p, dict) or set(p) - {'subjects', 'states', 'output', 'grace_days'}:
        raise ValueError('medication_state parameters: subjects, states, output, grace_days')
    names = p.get('subjects') or ['gdmt']
    subjects(names)
    states = p.get('states') or []
    if not isinstance(states, list) or any(s not in STATES for s in states): raise ValueError(f'states: subset of {STATES}')
    if p.get('output', 'patients') not in OUTPUTS: raise ValueError(f'output: {OUTPUTS}')
    grace = p.get('grace_days', GRACE_DAYS)
    if type(grace) is not int or not 0 <= grace <= 365: raise ValueError('grace_days: 0..365')
    return {'subjects': list(names) if isinstance(names, list) else [names], 'states': states,
            'output': p.get('output', 'patients'), 'grace_days': grace}


def execute(path, patient_ids, params, as_of):
    from . import semantic_layer, sql_query
    p = validate(params)
    target = semantic_layer.ensure(path) if (Path(path)/'run.json').exists() else Path(path)/'semantic.sqlite3'  # as Catalog.load
    db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
    try:
        tables = _tables(db)
    finally:
        db.close()
    sql = state_sql(subjects(p['subjects']), str(as_of)[:10], tables, grace=p['grace_days'], population=patient_ids is not None)
    rows = sql_query.execute(path, {'query': sql, 'returns': 'table'}, patient_ids=patient_ids, target=target)['rows']
    return report(rows, p, str(as_of)[:10])


def report(rows, p, as_of):
    order = list(dict.fromkeys(r['subject'] for r in rows))
    patients = {}
    for r in rows:
        patients.setdefault(r['patient_id'], {'patient_id': r['patient_id']})
        patients[r['patient_id']].update({r['subject']: r['state'], f"{r['subject']}_evidence": r['basis']})
    wanted = set(p['states'])
    selected = sorted(pid for pid, row in patients.items() if not wanted or any(row.get(s) in wanted for s in order))
    summary = [{'subject': s, **{st: sum(1 for r in rows if r['subject'] == s and r['state'] == st) for st in STATES}} for s in order]
    return {'schema': VERSION, 'as_of': as_of, 'parameters': p, 'subjects': order, 'rows': rows, 'summary': summary,
            'patients': [patients[pid] for pid in selected], 'patient_ids': selected,
            'method': DISCLOSURE, 'definition': DISCLOSURE}


def answer(report_, plan, question):
    """Answer contract for analysis_graph.build_answer_core."""
    partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
    p, wanted = report_['parameters'], report_['parameters']['states']
    table = (report_['summary'] if p['output'] == 'summary' else report_['rows'] if p['output'] == 'rows' else report_['patients'])
    counts = '; '.join(f"{s['subject']}: " + ', '.join(f'{k} {s[k]}' for k in STATES if s[k]) for s in report_['summary'])
    head = (f"{len(report_['patient_ids'])} patients with " + ' or '.join(wanted) if wanted else
            f"Medication state for {len(report_['patient_ids'])} patients")
    return {'status': 'partial' if partial else 'answered', 'answer_type': 'table', 'table': table,
            'count': len(report_['patient_ids']), 'patient_ids': report_['patient_ids'], 'value': f'{head} ({counts}).',
            'method': report_['method'], 'definitions': [{'term': 'medication state', 'basis': report_['definition']}],
            'limitations': [report_['definition']], 'membership_role': 'selected' if wanted else 'evaluated'}
