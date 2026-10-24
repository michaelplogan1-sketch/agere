"""Population SQL under the decision context, for the engine forms (population_engines.py): one small vocabulary of patient predicates, each
rendered from the SAME governed definitions the grammar uses -- living patients, the latest laboratory result, the canonical condition
evidence (concepts.condition_evidence_sql), the 12-month window (decision_context.rolling_year), picked-up fills, all-source ED visits and
inpatient stays. A predicate is a SQL expression over the patient row aliased `p`; a measure is a population predicate and a numerator
predicate, so every insight and form states the same definitions and computes the same patients.

Nothing here is a recipe for a particular question: a predicate is a building block, a measure a named population + numerator pair.
"""
import re
import sqlite3

from . import concepts, decision_context, sql_query

LIVING = "p.patient_id NOT IN (SELECT patient_id FROM death)"


def q(value):
    return "'" + str(value).replace("'", "''") + "'"


class Ctx:
    """The warehouse a form runs on: a read-only handle (through sql_query's authorizer), the as-of date and the shared windows."""

    def __init__(self, stack_path, target, as_of, question=''):
        self.stack_path, self.target, self.as_of, self.question = stack_path, target, str(as_of)[:10], question
        self.start, self.end = decision_context.rolling_year(self.as_of)
        with sqlite3.connect(f'file:{target}?mode=ro', uri=True) as db:
            names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")}
            self.tables = {t: [r[1] for r in db.execute(f'PRAGMA table_info("{t}")')]
                           for t in ('condition', 'payer_claim_diagnosis', 'problem_list_entry', 'enrollment_span') if t in names}
        self.queries = []  # every statement run, for the answer's evidence

    # -- running ---------------------------------------------------------------------------------------------------------
    def rows(self, sql):
        sql = ' '.join(sql.split())
        token = decision_context._SCOPE.set(None)  # the SQL below states the decision context itself; it is not rewritten a second time
        try:
            report = sql_query.execute(self.stack_path, {'query': sql, 'returns': 'table'}, target=self.target)
        finally:
            decision_context._SCOPE.reset(token)
        self.queries.append(sql)
        return report['rows']

    def one(self, sql, default=None):
        rows = self.rows(sql)
        return next(iter(rows[0].values())) if rows else default

    # -- windows ---------------------------------------------------------------------------------------------------------
    def in_window(self, column, start=None, end=None):
        return f"{column} >= {q(start or self.start)} AND {column} <= {q(end or self.end)}"

    def year(self, y):
        return f'{y}-01-01', f'{y}-12-31'

    # -- patient predicates (SQL over alias p) ---------------------------------------------------------------------------
    def age(self, lo=None, hi=None):
        parts = []
        if lo is not None: parts.append(f"age_at(p.birth_date, {q(self.as_of)}) >= {int(lo)}")
        if hi is not None: parts.append(f"age_at(p.birth_date, {q(self.as_of)}) <= {int(hi)}")
        return ' AND '.join(parts) or '1'

    def cond(self, name):
        evidence = concepts.condition_evidence_sql([name], self.tables)
        return f"p.patient_id IN (SELECT patient_id FROM {evidence})"

    def lab(self, concept, op, value):
        """The LATEST result of the concept meets the comparison."""
        return (f"EXISTS (SELECT 1 FROM measurement_latest m WHERE m.patient_id = p.patient_id AND m.concept = {q(concept)} "
                f"AND m.value_canonical {op} {value})")

    def has_lab(self, concept):
        return f"EXISTS (SELECT 1 FROM measurement_latest m WHERE m.patient_id = p.patient_id AND m.concept = {q(concept)} AND m.value_canonical IS NOT NULL)"

    def lab_in_window(self, concept):
        """A usable result (current, with a value, not excluded) dated in the 12 months."""
        return (f"EXISTS (SELECT 1 FROM measurement m WHERE m.patient_id = p.patient_id AND m.concept = {q(concept)} AND m.value_canonical IS NOT NULL "
                f"AND m.result_current = 1 AND m.excluded_reason IS NULL AND {self.in_window('m.event_date')})")

    def events(self, kind, minimum=1, start=None, end=None):
        return (f"(SELECT COUNT(*) FROM utilization_event u WHERE u.patient_id = p.patient_id AND u.kind = {q(kind)} "
                f"AND {self.in_window('u.event_date', start, end)}) >= {int(minimum)}")

    def fill(self, classes, start=None, end=None):
        marks = ','.join(q(c) for c in classes)
        return (f"EXISTS (SELECT 1 FROM fill f WHERE f.patient_id = p.patient_id AND f.drug_class IN ({marks}) "
                f"AND {self.in_window('f.fill_date', start, end)})")

    def visit_after(self, after_expr, concepts_=('primary_care_visit', 'specialist_visit', 'telehealth_contact')):
        marks = ','.join(q(c) for c in concepts_)
        return (f"EXISTS (SELECT 1 FROM visit v WHERE v.patient_id = p.patient_id AND v.type_concept IN ({marks}) "
                f"AND v.event_date > {after_expr} AND v.event_date <= {q(self.as_of)})")

    def social_need(self, need):
        return f"EXISTS (SELECT 1 FROM social_need s WHERE s.patient_id = p.patient_id AND s.need = {q(need)} AND s.positive = 1)"

    # -- a patient's practice (PCP practice, else the practice attributed on the latest enrollment span) -------------------
    @property
    def practice(self):
        return ("COALESCE(p.pcp_practice_id, (SELECT e.attribution_practice_id FROM enrollment_span e WHERE e.patient_id = p.patient_id "
                "AND e.attribution_practice_id IS NOT NULL ORDER BY e.coverage_start DESC LIMIT 1))")

    @property
    def lob(self):
        return "COALESCE(p.line_of_business, 'unknown')"

    def patients(self, *predicates, columns='p.patient_id'):
        where = ' AND '.join([LIVING, *[x for x in predicates if x]])
        return f"SELECT {columns} FROM patient p WHERE {where}"

    def ids(self, *predicates):
        return [r['patient_id'] for r in self.rows(self.patients(*predicates) + ' ORDER BY p.patient_id')]

    def count(self, *predicates):
        return self.one(f"SELECT COUNT(*) FROM ({self.patients(*predicates)})", 0)

    # -- a measure by group ----------------------------------------------------------------------------------------------
    def rates(self, group_expr, population, numerator, minimum=1, order='asc', extra_columns=''):
        """Rows [{group, n, k, rate}] of the patients of `population` that meet `numerator`, per group (largest-rate first for desc)."""
        sql = (f"SELECT {group_expr} AS grp, COUNT(*) AS n, SUM(CASE WHEN {numerator} THEN 1 ELSE 0 END) AS k, "
               f"1.0 * SUM(CASE WHEN {numerator} THEN 1 ELSE 0 END) / COUNT(*) AS rate{extra_columns} "
               f"FROM patient p WHERE {LIVING} AND {population} GROUP BY grp HAVING grp IS NOT NULL AND COUNT(*) >= {int(minimum)} "
               f"ORDER BY rate {'ASC' if order == 'asc' else 'DESC'}, grp")
        return self.rows(sql)

    def overall(self, population, numerator):
        row = self.rows(f"SELECT COUNT(*) AS n, SUM(CASE WHEN {numerator} THEN 1 ELSE 0 END) AS k FROM patient p WHERE {LIVING} AND {population}")[0]
        row['rate'] = (row['k'] / row['n']) if row['n'] else None
        return row


def pct(x, places=1):
    return None if x is None else round(100.0 * x, places)


def usd(x):
    return f'${x:,.0f}'
