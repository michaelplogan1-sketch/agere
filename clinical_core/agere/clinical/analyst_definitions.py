"""Agere's decision-context definitions for the semantic analyst, inherited by construction (Mike, 2026-10-23).

Measured 2026-10-23: the semantic analyst (one Claude tool loop, the documented semantic layer, read-only SQL) lost mostly on DEFINITIONS
(deceased and duplicate charts counted, any-ever labs, condition code sets, stale age) and ARITHMETIC (right numerator and denominator,
wrong share). decision_context.py already defines all of these; this module puts them where the analyst works:

  1. DEFINITION VIEWS: temp views on the analyst's read-only connection (never written to the stack), documented FIRST in the analyst's
     system documentation with "always build on these":
         living_patient(<patient columns>, age)         no death row, a duplicate chart counted once, age from birth date at as-of
         condition_member(patient_id, concept, first_date, evidence)   the ONE condition-evidence rule, canonical code sets
         latest_lab(patient_id, concept, value, unit, event_date)      the latest result per patient and concept
         last_12_months(start_exclusive, end_inclusive)                the 12 months ending at as-of
         medicare_member(patient_id, coverage_type, age)               medicare + dual + medigap
     condition_member, latest_lab and medicare_member are living-only by construction.
  2. ARITHMETIC IN CODE: a share, rate or average carries numerator and denominator (and the SQL that computes each); code computes the
     value and the headline, and a stated value that disagrees is replaced and noted.
  3. A DETERMINISTIC SQL DEFINITIONS AUDIT (no model) before an answer ships: patient grain on living patients, lab thresholds on the latest
     result, conditions from the evidence rule, age from the birth date, events from the all-source relations, Medicare incl. dual/Medigap.
     A violation is rewritten when mechanical (re-executed, read-only and free) or the answer is declined with the reason.

The competitor baseline (bench frontier_semantic / reconcile) passes definitions=False and keeps the plain documented layer byte for byte.
"""
from __future__ import annotations

import json
import re
import sqlite3

from . import decision_context as DC

VERSION = 'analyst-definitions/2'
VIEW_NAMES = ('living_patient', 'condition_member', 'latest_lab', 'last_12_months', 'medicare_member')
LIVING_VIEWS = ('living_patient', 'condition_member', 'latest_lab', 'medicare_member')  # living-only by construction
_DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


# --- the views ----------------------------------------------------------------------------------------------------------------------
def age_sql(column, as_of):
    """Completed years from a birth-date column at as_of in plain SQLite (age.age_at: missing/future birth date -> NULL)."""
    return (f"CASE WHEN {column} IS NULL OR {column} > '{as_of}' THEN NULL ELSE CAST(strftime('%Y', '{as_of}') - strftime('%Y', {column}) "
            f"- (strftime('%m-%d', '{as_of}') < strftime('%m-%d', {column})) AS INTEGER) END")


def excluded_predicate(columns, qualifier=''):
    """patient_id NOT IN (death [+ duplicate charts]) for these tables, or '1 = 1' when the database has no death relation."""
    if 'death' not in columns: return '1 = 1'
    has_segment = 'data_segment' in columns.get('patient', [])
    return f'{qualifier}patient_id ' + DC.counted_predicate(has_segment)


def build_views(columns, as_of):
    """-> {view name: SELECT} for the definition views this database can support. as_of is an ISO date (validated)."""
    if not _DATE.match(str(as_of)): raise ValueError(f'as_of must be an ISO date: {as_of!r}')
    views = {}
    patient = columns.get('patient')
    if patient and 'patient_id' in patient:
        age = age_sql('p.birth_date', as_of) if 'birth_date' in patient else 'NULL'
        views['living_patient'] = f'SELECT p.*, {age} AS age FROM patient p WHERE {excluded_predicate(columns, "p.")}'
    if 'condition' in columns:
        from .concepts import CONDITIONS
        parts = []
        for name in CONDITIONS:
            sub = DC.condition_evidence_sql([name], columns)
            if sub: parts.append(f"SELECT patient_id, '{name}' AS concept, event_date, source FROM {sub}")
        if parts:
            views['condition_member'] = ('SELECT patient_id, concept, MIN(event_date) AS first_date, group_concat(DISTINCT source) AS evidence '
                                         f'FROM ({" UNION ALL ".join(parts)}) WHERE {excluded_predicate(columns)} GROUP BY patient_id, concept')
    if {'patient_id', 'concept', 'value_canonical'} <= set(columns.get('measurement_latest', [])):
        views['latest_lab'] = ('SELECT patient_id, concept, value_canonical AS value, unit_canonical AS unit, event_date FROM measurement_latest '
                               f'WHERE {excluded_predicate(columns)}')
    views['last_12_months'] = f"SELECT date('{as_of}', '-12 months') AS start_exclusive, '{as_of}' AS end_inclusive"
    if 'living_patient' in views and 'coverage_type' in patient:
        values = ','.join(f"'{v}'" for v in DC.MEDICARE_COVERAGE)
        views['medicare_member'] = f'SELECT patient_id, coverage_type, age FROM living_patient WHERE coverage_type IN ({values})'
    # The investigator and compiler inherit the SAME owner definitions. A coded
    # condition is not a substitute for a phenotype that also includes note evidence.
    from .clinical_definitions import PHENOTYPES
    for name, definition in PHENOTYPES.items():
        required = definition.get('requires')
        if 'living_patient' not in views or not required or not all(
                set(fields) <= set(columns.get(table, [])) for table, fields in required.items()):
            continue
        views['phenotype_' + name] = ('SELECT h.* FROM ' + definition['build'](columns)
                                     + ' h JOIN living_patient l USING(patient_id)')
    return views


def definitions_doc(names):
    """The documentation of the views present, placed first in the analyst's cached system documentation."""
    have = set(names)
    lines = ['AGERE DEFINITION VIEWS: ALWAYS BUILD ON THESE (Mike, 2026-10-23). They are temporary read-only views on your connection that carry '
             "Agere's decision context, so you do not re-derive it. :as_of is already bound inside them. Do not re-implement these definitions "
             'from the base tables, and do not describe them in grounding as anything but what they are.']
    if 'living_patient' in have:
        lines.append("- living_patient (all patient columns + age): patients are LIVING PEOPLE: no row in death, and a duplicate chart "
                     "(data_segment = 'duplicate_chart') counted once. `age` = completed years from birth_date at the as-of date. EVERY count or list of "
                     'patients starts here: SELECT COUNT(*) FROM living_patient WHERE ... Never use patient.age_reported or age_latest (feed ages are stale); '
                     'use `age`. Denominators of patient shares come from here too.')
    if 'condition_member' in have:
        lines.append('- condition_member(patient_id, concept, first_date, evidence): ONE row per living patient and condition concept, from the '
                     'canonical evidence (an EHR diagnosis, a counted payer-claim diagnosis by the concept\'s ICD-10 code set; diabetes and CKD also '
                     "laboratory evidence; rule-outs never; claims-only members count). Use WHERE concept = 'diabetes' (concepts: the concept library "
                     'below), COUNT(DISTINCT patient_id). Join it to living_patient for age, sex, coverage. Never read the condition table or ICD-10 '
                     'prefixes yourself to define a condition.')
    if 'latest_lab' in have:
        lines.append('- latest_lab(patient_id, concept, value, unit, event_date): the LATEST result per living patient and lab concept (canonical unit). '
                     'Every lab threshold ("A1c above 9", "potassium over 5.5", "eGFR under 60") uses it. Use the measurement table (every result) ONLY '
                     'when the question says ever, any, at any time, history of, at least once, highest, lowest, or asks for a trend.')
    if 'last_12_months' in have:
        lines.append("- last_12_months(start_exclusive, end_inclusive): one row. 'Last year / past year / last 12 months' = event_date > start_exclusive "
                     'AND event_date <= end_inclusive. A calendar year word ("in 2029") is that calendar year.')
    if 'medicare_member' in have:
        lines.append("- medicare_member(patient_id, coverage_type, age): living patients with Medicare: coverage_type IN ('medicare','dual','medigap'). "
                     "Dual-eligible and Medigap members HAVE Medicare. Only 'traditional / original / fee-for-service / Medicare only' means "
                     "coverage_type = 'medicare' (use living_patient).")
    from .clinical_definitions import PHENOTYPES
    for name, definition in PHENOTYPES.items():
        view = 'phenotype_' + name
        if view in have:
            lines.append(f'- {view}: ONE row per living patient qualifying for {definition["label"]}. '
                         f'Owner definition v{definition["version"]}, {definition["decision"]}: '
                         + definition['disclosure'] + ' Use this view for the phenotype; condition_member supplies coded '
                         'condition membership only. The view preserves the compiler macro columns, including the evidence basis.')
    lines.append('These views exclude the deceased by construction. When the question asks about the deceased (died, death, mortality, "including '
                 'deceased", "all patients ever"), use the base tables and the death table instead and say so.')
    lines.append('SHARES, RATES AND AVERAGES: compute the numerator and the denominator as two single-value queries and report both '
                 '(see the answer contract); the system computes the share.')
    return '\n'.join(lines)


CONTRACT_SUFFIX = '''
Agere addendum to the answer contract. Add "sql" to every answered object: the single SELECT (read-only) whose result is the answer
(a count or value as its only value, a patient_set as a patient_id column). For a SHARE, RATE, PERCENT or AVERAGE (anything that is one
number divided by another) set answer_type to value and add
"ratio":{"numerator":<number>,"denominator":<number>,"numerator_sql":"<one SELECT returning only the numerator>",
"denominator_sql":"<one SELECT returning only the denominator>","scale":"fraction|percent|per_1000|per_100000|mean"}.
For an average, numerator is the sum and denominator the number of items (scale mean). The system computes value from
numerator and denominator itself: do not do the division in SQL and do not round it.'''

_CONTEXT = {}


def agere_context(semantic, base):
    """(documentation with the definition views first, example IDs) from the plain (docs, ids) of semantic_context; cached per database."""
    key = (str(semantic.path), semantic.meta.get('source_digest'), semantic.meta.get('version'), VERSION)
    cached = _CONTEXT.get(key)
    if cached is None:
        names = build_views(semantic.columns, semantic.as_of or '2000-01-01')
        cached = _CONTEXT.setdefault(key, (definitions_doc(names) + '\n\n' + base[0], base[1]))
    return cached


# --- SQL audit ----------------------------------------------------------------------------------------------------------------------
_COUNT_STAR = re.compile(r'(?is)\bcount\s*\(\s*(?:\*|1)\s*\)')
_ENTITY_FROM = re.compile(r'(?is)\b(?:FROM|JOIN)\s+"?(?:patient|living_patient|medicare_member|condition_member|latest_lab|concept_patient)"?(?=[\s,;)]|$)')
_RAW_PATIENT = re.compile(r'(?is)\b(?:FROM|JOIN)\s+"?patient"?(?=[\s,;)]|$)')
_RAW_CONDITION = re.compile(r'(?is)\b(?:FROM|JOIN)\s+"?condition"?(?=[\s,;)]|$)')
_RAW_MEASUREMENT = re.compile(r'(?is)\b(?:FROM|JOIN)\s+"?measurement"?(?=[\s,;)]|$)')
# `value_canonical IS NOT NULL` asks whether a result EXISTS (a missing-lab question), it applies no threshold to the value
_VALUE_IS_NULL = re.compile(r'\bvalue(?:_canonical)?\s+is\s+(?:not\s+)?null\b')
_AGE_FEED = re.compile(r'(?i)\b(?:(\w+)\.)?(age_reported|age_latest)\b')
_MEDICARE_ONLY = re.compile(r"(?is)\bcoverage_type\s*(?:=\s*'medicare'|IN\s*\(\s*'medicare'\s*\))")
_LIVING_VIEW_USE = re.compile(r'(?i)\b(?:' + '|'.join(LIVING_VIEWS) + r')\b')


def _patient_grain(masked, sql, returns):
    return DC.patient_grain(sql, returns) or bool(_COUNT_STAR.search(masked) and _ENTITY_FROM.search(masked)) or bool(_RAW_PATIENT.search(masked))


def audit_sql(question, as_of, sql, columns, returns=None):
    """-> [{'rule', 'detail', 'mechanical'}]: how this answer SQL departs from the decision context ([] = conforms). Deterministic, no model."""
    if not isinstance(sql, str) or not sql.strip(): return []
    masked, held = DC._mask(sql)
    text = DC._unmask(masked, held).lower()
    findings = []

    def add(rule, detail, mechanical): findings.append({'rule': rule, 'detail': detail, 'mechanical': mechanical})
    grain = _patient_grain(masked, sql, returns)
    state = DC.Scope(question, as_of)
    if grain and state.living_only and 'death' in columns:
        dup_needed = 'data_segment' in columns.get('patient', [])
        if not _LIVING_VIEW_USE.search(masked):
            if not DC.references_death(sql):
                add('living', 'a patient count or list must start from living_patient (no death row, a duplicate chart once)', True)
            elif dup_needed and not re.search(r'duplicate_chart|data_segment', text):
                add('living', 'excludes the deceased but counts duplicate charts twice: use living_patient', bool(_RAW_PATIENT.search(masked)))
    if grain and state.lab_mode == 'latest' and 'measurement_latest' in columns and _RAW_MEASUREMENT.search(masked) and 'value' in _VALUE_IS_NULL.sub(' ', masked.lower()) \
            and not re.search(r'(?i)\bhaving\b|\bover\s*\(|\b(?:measurement_latest|latest_lab)\b', masked):
        add('latest_lab', 'a lab threshold reads the latest result (latest_lab), not every result: the question does not say ever / any / history of', True)
    if grain and _RAW_CONDITION.search(masked) and not re.search(r'(?i)\b(?:condition_member|diagnosis_any|concept_patient)\b', masked) \
            and not ('is_diagnosis' in text and 'counts_in_metrics' in text):
        add('condition_evidence', 'a condition is defined by condition_member (canonical evidence and code sets), not by reading the condition table', False)
    if grain and re.search(r'(?i)\bpayer_claim_diagnosis\b', masked) and 'counts_in_metrics' not in text \
            and not re.search(r'(?i)\bcondition_member\b', masked):
        add('condition_evidence', 'payer_claim_diagnosis rows count only with counts_in_metrics = 1; use condition_member', False)
    if _AGE_FEED.search(masked):
        add('age', 'age comes from the birth date at the as-of date (living_patient.age), never the feed-reported age', True)
    for reason in DC.event_source_findings(question, [sql]): add('event_source', reason, False)
    if re.search(r'(?i)\bmedicare\b', question or '') and not DC.TRADITIONAL_MEDICARE.search(question or '') and _MEDICARE_ONLY.search(text):
        add('medicare', "Medicare includes dual and Medigap members: coverage_type IN ('medicare','dual','medigap') (medicare_member)", True)
    return findings


def _wrap(masked, predicates):
    """Wrap each FROM/JOIN of a named table in a filtered subquery that keeps its name or alias."""
    ctes = DC._cte_names(masked)

    def sub(m):
        kw, ws, quote, name, ws2, as_kw, alias = m.groups()
        tail = ''
        if alias and alias.lower() in DC._KEYWORDS: tail, alias = (ws2 or '') + alias, None
        table = name.lower()
        if table in ctes or table not in predicates: return m.group()
        label = alias or f'{quote}{name}{quote}'
        return f'{kw}{ws}(SELECT * FROM {table} WHERE {predicates[table]}) AS {label}{tail}'
    return DC._FROM.sub(sub, masked)


def rewrite_sql(question, as_of, sql, columns, findings, returns=None):
    """The mechanical rewrite of the findings -> (sql, notes). Residual findings are the caller's to re-audit."""
    masked, held = DC._mask(sql)
    notes = []
    rules = {f['rule'] for f in findings}
    if 'age' in rules:
        def age(m):
            return '(' + age_sql(f'{m.group(1)}.birth_date' if m.group(1) else 'birth_date', as_of) + ')'
        masked = _AGE_FEED.sub(age, masked)
        notes.append('age from birth_date')
    out = DC._unmask(masked, held)
    if 'medicare' in rules:
        values = ','.join(f"'{v}'" for v in DC.MEDICARE_COVERAGE)
        out = _MEDICARE_ONLY.sub(f'coverage_type IN ({values})', out)
        notes.append('Medicare includes dual and Medigap')
    if 'living' in rules and any(f['rule'] == 'living' and 'twice' in f['detail'] for f in findings):
        masked, held = DC._mask(out)
        out = DC._unmask(_wrap(masked, {'patient': "COALESCE(data_segment, '') <> 'duplicate_chart'"}), held)
        notes.append('duplicate charts counted once')
    if rules & {'living', 'latest_lab'} and not any(f['rule'] == 'living' and 'twice' in f['detail'] for f in findings):
        tables = set(columns) | {f'{t}.{c}' for t, cs in columns.items() for c in cs if c == 'data_segment'}
        with DC.scope(question, as_of) as state:
            out2, receipt = DC.enforce_sql(out, returns='patient_set', tables=tables)
            if state.problems: notes.append('unrestrictable: ' + '; '.join(state.problems))
        if receipt and receipt.get('rewritten'):
            out = out2
            notes.append('living patients' if receipt['living'] else 'latest labs')
            if receipt['latest_labs']: notes.append('latest labs')
    return out, notes


# --- executing and finalizing an answer ---------------------------------------------------------------------------------------------------
class Decline(Exception):
    pass


def _scalar(semantic, sql, as_of):
    out = semantic.query(sql, as_of=as_of, max_rows=2, max_chars=2000)
    rows = out['rows']
    if len(rows) != 1 or len(out['columns']) != 1 or out['truncated']: raise Decline('a numerator, denominator or count query must return one value')
    value = next(iter(rows[0].values()))
    if isinstance(value, bool) or not isinstance(value, (int, float)): raise Decline('a numerator, denominator or count query must return a number')
    return value


def _successful(result):
    return [c.get('sql') for c in result.get('tool_calls') or [] if c.get('sql') and 'error' not in (c.get('result') or {}) and not c.get('audit')]


def _same(x, y):
    return x is not None and y is not None and abs(float(x) - float(y)) <= 1e-9 * max(1.0, abs(float(x)), abs(float(y)))


SCALES = {'fraction': 1, 'percent': 100, 'per_100': 100, 'per_1000': 1000, 'per_100000': 100000, 'per_100k': 100000, 'mean': 1, 'average': 1, 'ratio': 1}


def _decimals(x):
    text = repr(float(x))
    return len(text.split('.')[1].rstrip('0')) if '.' in text and 'e' not in text else 0


def _format(value, scale):
    shown = f'{value:,.2f}'.rstrip('0').rstrip('.') if abs(value) < 1e15 else str(value)
    return shown + ('%' if scale in ('percent', 'per_100') else '')


def compute_ratio(ratio):
    """-> (value, scale, note|None) from a ratio object's numerator and denominator; raises Decline on a zero or missing part."""
    n, d = ratio.get('numerator'), ratio.get('denominator')
    scale = str(ratio.get('scale') or 'fraction').lower()
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (n, d)): raise Decline('the ratio carries no numeric numerator and denominator')
    if d == 0: raise Decline('the denominator is 0, so the share is undefined')
    if scale not in SCALES: raise Decline(f'unknown ratio scale {scale!r}')
    return n / d * SCALES[scale], scale, None


def _target_sqls(answer, result):
    ratio = answer.get('ratio') if isinstance(answer.get('ratio'), dict) else None
    if ratio:
        return [('numerator', ratio.get('numerator_sql')), ('denominator', ratio.get('denominator_sql'))], ratio
    sql = answer.get('sql') if isinstance(answer.get('sql'), str) and answer.get('sql').strip() else None
    if sql is None:
        for candidate in reversed(_successful(result)):
            m, _ = DC._mask(candidate)
            if _patient_grain(m, candidate, None): sql = candidate; break
    return [('answer', sql)], None


def finalize(question, as_of, semantic, result):
    """The audit and the arithmetic on a finished analyst result (mutates result['answer'] and appends the executed audit queries to
    result['tool_calls']). -> result. Adds result['definitions_audit'] (what was checked, rewritten or declined) and, on decline,
    result['declined_answer'] (the model's own answer) with a status-unsupported answer carrying the reason."""
    answer = result.get('answer')
    if not isinstance(answer, dict) or answer.get('status') not in ('answered', 'partial'): return result
    receipt = {'version': VERSION, 'checked': [], 'rewrites': [], 'arithmetic': None, 'action': 'ok'}
    result['definitions_audit'] = receipt
    original = json.loads(json.dumps(answer, default=str))
    columns = semantic.columns
    returns = 'patient_set' if answer.get('answer_type') == 'patient_set' else None
    try:
        targets, ratio = _target_sqls(answer, result)
        values = {}
        for role, sql in targets:
            if not isinstance(sql, str) or not sql.strip():
                if ratio: raise Decline(f'the ratio carries no {role}_sql, so its {role} cannot be checked')
                continue
            findings = audit_sql(question, as_of, sql, columns, returns if role == 'answer' else None)
            receipt['checked'].append({'role': role, 'sql': sql, 'findings': findings})
            final = sql
            if findings:
                if not all(f['mechanical'] for f in findings):
                    raise Decline('; '.join(f['detail'] for f in findings if not f['mechanical']))
                if role == 'answer' and answer.get('answer_type') == 'table': raise Decline('; '.join(f['detail'] for f in findings))
                final, notes = rewrite_sql(question, as_of, sql, columns, findings, returns if role == 'answer' else None)
                residual = audit_sql(question, as_of, final, columns, returns if role == 'answer' else None)
                if residual or any(n.startswith('unrestrictable') for n in notes):
                    raise Decline('; '.join([f['detail'] for f in residual] + [n for n in notes if n.startswith('unrestrictable')]))
                receipt['rewrites'].append({'role': role, 'rules': [f['rule'] for f in findings], 'notes': notes, 'before': sql, 'after': final})
                receipt['action'] = 'rewritten'
            if ratio:
                values[role] = _scalar(semantic, final, as_of)
                result.setdefault('tool_calls', []).append({'sql': final, 'result': {'value': values[role]}, 'audit': role})
            elif final is not sql or role == 'answer':
                _apply_answer(semantic, answer, sql, final, as_of, result, rewritten=final != sql)
        if ratio:
            _apply_ratio(answer, ratio, values, receipt)
    except Decline as why:
        reason = str(why)
        receipt['action'] = 'declined'
        receipt['reason'] = reason
        result['declined_answer'] = original
        result['answer'] = {'status': 'unsupported', 'answer_type': 'unsupported', 'count': None, 'patient_ids': None, 'value': None,
                            'table': None, 'unknown_count': 0, 'grounding': [],
                            'missing': ['The analyst\'s query did not follow the decision-context definitions, so the answer was withheld: ' + reason]}
    except (sqlite3.Error, ValueError) as exc:
        receipt['action'] = 'declined'
        receipt['reason'] = f'audit query failed: {exc}'
        result['declined_answer'] = original
        result['answer'] = {'status': 'unsupported', 'answer_type': 'unsupported', 'count': None, 'patient_ids': None, 'value': None,
                            'table': None, 'unknown_count': 0, 'grounding': [],
                            'missing': ['The analyst\'s corrected query could not be run: ' + str(exc)]}
    else:
        if receipt['action'] != 'ok' or receipt['arithmetic']:
            answer['definitions_audit'] = {'action': receipt['action'], 'rewrites': receipt['rewrites'], 'arithmetic': receipt['arithmetic']}
    return result


def _apply_answer(semantic, answer, before, after, as_of, result, rewritten):
    """A rewritten answer SQL replaces the count / value / patient_ids it produced; the original SQL must reproduce the stated answer."""
    kind = answer.get('answer_type')
    if kind == 'patient_set':
        was = semantic.query(before, as_of=as_of, max_rows=2000, max_chars=10**6)
        now = semantic.query(after, as_of=as_of, max_rows=2000, max_chars=10**6)
        if 'patient_id' not in now['columns'] or now['truncated'] or was['truncated']: raise Decline('the patient list could not be recomputed under the definitions')
        stated = sorted(answer.get('patient_ids') or [])
        if sorted(r['patient_id'] for r in was['rows']) != stated: raise Decline('the stated patient list does not come from the declared sql')
        ids = sorted({r['patient_id'] for r in now['rows']})
        answer['patient_ids'], answer['count'] = ids, len(ids)
        result['tool_calls'].append({'sql': after, 'result': {'rows': len(ids)}, 'audit': 'answer'})
    elif kind in ('count', 'value'):
        if not rewritten: return
        was, now = _scalar(semantic, before, as_of), _scalar(semantic, after, as_of)
        if not _same(was, answer.get(kind)): raise Decline('the stated answer does not come from the declared sql')
        answer[kind] = now
        result['tool_calls'].append({'sql': after, 'result': {'value': now}, 'audit': 'answer'})
    elif rewritten:
        raise Decline('a rewritten query cannot be mapped to this answer type')


def _apply_ratio(answer, ratio, values, receipt):
    stated = {k: ratio.get(k) for k in ('numerator', 'denominator')}
    notes = []
    for role in ('numerator', 'denominator'):
        got = values.get(role)
        if got is not None and not _same(got, stated[role]):
            notes.append(f'{role} stated {stated[role]} but its query returns {got}')
            ratio[role] = got
    value, scale, _ = compute_ratio(ratio)
    prior = answer.get('value')
    digits = _decimals(prior) if isinstance(prior, (int, float)) and not isinstance(prior, bool) else None
    agrees = digits is not None and abs(prior - value) <= 0.5 * 10 ** -digits + 1e-12
    if agrees:
        shown = prior
    else:
        shown = round(value, 2 if scale in ('percent', 'per_100', 'mean', 'average') else 4)
        if prior is not None: notes.append(f'stated value {prior} replaced by numerator / denominator = {shown}')
    answer['value'] = shown
    answer['answer_type'] = 'value'
    answer['headline'] = f"{_format(shown, scale)} ({ratio['numerator']:,} of {ratio['denominator']:,})"
    receipt['arithmetic'] = {'numerator': ratio['numerator'], 'denominator': ratio['denominator'], 'scale': scale, 'computed': value,
                             'stated': prior, 'replaced': not agrees and prior is not None, 'notes': notes}
