"""Analyst-style insight families (2026-10-24, from the independent dev set), registered with population_engines like insight_families.py.

Executives and analysts ask a handful of recurring SHAPES of "why / where / how concentrated / who is lagging" questions in their own words. Each family
here is a shape, never a particular question: it recognises the shape with vocabulary (measure words, event words, group words, condition words),
binds every content word of the question to a recognised phrase or a family word (anything left over declines the form; nothing is silently
dropped), and computes each fact from the decision-context definitions in population_sql with its numerator and denominator.

  readmission_drivers   what is behind the 30-day readmission rate: the rate, the conditions and the hospitals it concentrates in
  use_concentration     is ED use / admissions driven by a small group of patients: users, share of visits by the top users, patients needed for half
  evidence_gap          who is lagging on <vaccine / screening / test>: the share of living patients with it, per payer type or practice, the lowest named
  period_series         which month / quarter was highest or lowest, and the trend, of spend / utilization in a year
  cohort_defined        a cohort defined by ED / admission counts in the last year ("two or more ED visits but no admission"): how many, what they cost
  comorbidity_cost      how much more do patients with N or more of the chronic conditions cost than the rest
  drug_spend_drivers    are <drug classes> what is pushing up the pharmacy bill: the bill by year, the classes' share of it and of the change
  outcome_by_payer      which payer's claims are denied / rejected the most, and the usual reason
  need_gap              do patients with a social need miss more appointments than everyone else
"""
import re

from .population_engines import Declined, family, fact
from .population_sql import q, pct, usd
from .insight_families import FULL_YEAR_NOTE, years_in, span, cap, last_full_year, CONDITION_WORDS as _BASE_CONDITION_WORDS

CONDITION_WORDS = _BASE_CONDITION_WORDS + [(r'copd|chronic\s+obstructive\s+pulmonary\s+disease', 'copd'), (r'asthma', 'asthma')]

HOSPITAL_MIN_STAYS = 20  # a hospital's readmission rate is ranked only above this many stays in the year (stated in the answer)
CONDITION_LABELS = {'diabetes': 'diabetes', 'heart_failure': 'heart failure', 'chronic_kidney_disease': 'CKD', 'copd': 'COPD', 'hypertension': 'hypertension',
                    'asthma': 'asthma', 'coronary_artery_disease': 'coronary artery disease', 'atrial_fibrillation': 'atrial fibrillation'}
MAJOR_CONDITIONS = ['diabetes', 'heart_failure', 'chronic_kidney_disease', 'copd', 'hypertension']


def pick_year(ctx, text):
    years = years_in(text)
    if years: return years[-1], []
    y = last_full_year(ctx)
    return y, [FULL_YEAR_NOTE.format(y=y, a=ctx.as_of[:4])]


def living_events(ctx, kind, where):
    """SQL: the utilization events of one kind of living patients under a condition."""
    return (f"SELECT u.patient_id AS patient_id, u.event_date AS event_date, u.organization_id AS org, u.readmitted_30d AS readmitted "
            f"FROM utilization_event u JOIN patient p ON p.patient_id = u.patient_id WHERE u.kind = {q(kind)} AND {ctx.living} AND {where}")


# ============================================================================================================================
@family
class ReadmissionDrivers:
    NAME = 'readmission_drivers'
    WORDS = set('''what whats what's is are behind our driving where coming from the in one is it concentrated why high so which hospital hospitals
        conditions condition patients come drive driven who who's getting'''.split())
    READMIT = r'readmission\s+rates?|readmissions?|readmit\w*|bounce[- ]?backs?|30[- ]day\s+(?:returns?|bounce[- ]?backs?)'
    ASKS = r"what(?:'s|\s+is)\s+(?:behind|driving)|what\s+drives|why\b.*\b(?:high|so\s+high)|where\b.*\bcom(?:e|ing)\b|concentrated|which\s+(?:hospitals?|conditions?)|driv\w+|who(?:'s|\s+is|\s+are)\s+(?:getting\s+)?readmitted"

    @classmethod
    def parse(cls, text, catalog):
        t = text.strip().rstrip('?.')
        readmit = span(t, cls.READMIT)
        ask = span(t, cls.ASKS)
        if not readmit or not ask: return None
        day = span(t, r'30[- ]day')
        return {'params': {'year': (years_in(t) or [None])[-1], 'window_days': 30},  # the readmission flag is the 30-day window
                'quotes': [readmit, ask, day or '', span(t, r'bounce[- ]?backs?') or '', span(t, r'hospitals?') or '', *[str(y) for y in years_in(t)]]}

    @classmethod
    def run(cls, ctx, params):
        y = params['year'] or last_full_year(ctx)
        notes = [] if params['year'] else [FULL_YEAR_NOTE.format(y=y, a=ctx.as_of[:4])]
        year = f"substr(u.event_date, 1, 4) = {q(str(y))}"
        base = living_events(ctx, 'inpatient', year)
        overall = ctx.rows(f"SELECT COUNT(*) AS n, COALESCE(SUM(readmitted), 0) AS k FROM ({base})")[0]
        if not overall['n']:
            return dict(headline=f'No living-patient admissions in {y}', facts=[fact('none', f'There are no admissions in {y}.')], read_back='', notes=notes)
        facts = [fact('overall', f"The 30-day readmission rate on {y} admissions is {pct(overall['k'] / overall['n'])}% ({overall['k']:,} of {overall['n']:,} admissions) = {overall['k'] / overall['n']:.4f}.",
                      value=pct(overall['k'] / overall['n']), numerator=overall['k'], denominator=overall['n'], unit='%')]
        tables = {}
        by_condition = []
        for name in MAJOR_CONDITIONS:
            r = ctx.rows(f"SELECT COUNT(*) AS n, COALESCE(SUM(readmitted), 0) AS k FROM ({base}) b JOIN patient p ON p.patient_id = b.patient_id WHERE {ctx.cond(name)}")[0]
            if r['n']: by_condition.append({'condition': CONDITION_LABELS.get(name, name), 'admissions': r['n'], 'readmitted': r['k'], 'rate': r['k'] / r['n']})
        ranked = sorted(by_condition, key=lambda r: (-r['rate'], r['condition']))
        for i, r in enumerate(ranked):
            facts.append(fact(f"cond_{i + 1}", f"{r['condition']} patients: {pct(r['rate'])}% of their {y} admissions were followed by a readmission within 30 days ({r['readmitted']} of {r['admissions']}) = {r['rate']:.4f}.",
                              value=pct(r['rate']), numerator=r['readmitted'], denominator=r['admissions'], unit='%'))
        if ranked:
            facts.insert(1, fact('top_condition', f"{ranked[0]['condition']} patients readmit most among {', '.join(r['condition'] for r in by_condition)}: {pct(ranked[0]['rate'])}% "
                                                  f"({ranked[0]['readmitted']} of {ranked[0]['admissions']} admissions); {ranked[0]['condition']} is the highest-readmission condition group.",
                                 value=ranked[0]['condition']))
        hospitals = ctx.rows(f"SELECT org AS grp, COUNT(*) AS n, SUM(readmitted) AS k, 1.0 * SUM(readmitted) / COUNT(*) AS rate FROM ({base}) WHERE org IS NOT NULL "
                             f"GROUP BY org HAVING COUNT(*) >= {HOSPITAL_MIN_STAYS} ORDER BY rate DESC, grp")
        for i, r in enumerate(hospitals[:3]):
            facts.append(fact(f"hospital_{i + 1}", f"Among hospitals with at least {HOSPITAL_MIN_STAYS} stays, {r['grp']} has {'the highest' if i == 0 else 'the next-highest'} {y} readmission rate: {pct(r['rate'])}% ({r['k']} of {r['n']} stays) = {r['rate']:.4f}.",
                              value=pct(r['rate']), numerator=r['k'], denominator=r['n'], unit='%'))
        tables['by_condition'] = [{'condition': r['condition'], 'admissions': r['admissions'], 'readmitted': r['readmitted'], 'rate_percent': pct(r['rate'])} for r in ranked]
        tables['by_hospital'] = [{'hospital': r['grp'], 'stays': r['n'], 'readmitted': r['k'], 'rate_percent': pct(r['rate'])} for r in hospitals]
        head = f"{y} 30-day readmission rate {pct(overall['k'] / overall['n'])}%" + (f"; highest among conditions: {ranked[0]['condition']} ({pct(ranked[0]['rate'])}%)" if ranked else '') \
            + (f"; highest hospital among those with at least {HOSPITAL_MIN_STAYS} stays: {hospitals[0]['grp']} ({pct(hospitals[0]['rate'])}%)" if hospitals else '')
        notes += [f'Admissions are the all-source inpatient utilization_event rows of living patients, counted by admission year; a readmission is a new admission within 30 days of discharge (readmitted_30d).',
                  f'Hospitals are ranked with at least {HOSPITAL_MIN_STAYS} stays in the year; a patient counts in every condition group they have (canonical condition evidence).']
        return dict(headline=head, facts=facts, table=tables['by_condition'], tables=tables,
                    read_back=f"30-day readmission rate for {y} admissions of living patients, by condition group and by hospital (hospitals with at least {HOSPITAL_MIN_STAYS} stays).", notes=notes)


# ============================================================================================================================
@family
class UseConcentration:
    NAME = 'use_concentration'
    WORDS = set('''is our driven by a small group of patients how concentrated it how much comes from the few top volume do account for most handful drive generate
        make up are responsible majority bulk half'''.split())
    EVENTS = [(r'ed\s+(?:volume|visits?|use|utilization)|emergency(?:\s+(?:department|room))?\s+(?:volume|visits?|use|utilization)', 'ed_visit', 'ED visits'),
              (r'(?:hospital\s+|inpatient\s+)?admissions?\s+volume|(?:hospital|inpatient)\s+(?:admissions|stays)', 'inpatient', 'admissions')]

    @classmethod
    def parse(cls, text, catalog):
        t = text.strip().rstrip('?.')
        ask = span(t, r'concentrat\w+|driven\s+by\s+(?:a\s+)?(?:small|few|handful)\b[\w\s]*?(?:group|number|set)\s+of\s+patients|small\s+group\s+of\s+patients|'
                      r'(?:a\s+)?(?:few|handful\s+of|small\s+number\s+of)\s+patients\s+(?:account|drive|generate|make\s+up|are\s+responsible)\b[\w\s]*?(?:most|majority|bulk|half)\s+of')
        event = next(((span(t, p), kind, label) for p, kind, label in cls.EVENTS if span(t, p)), None)
        if not ask or not event: return None
        return {'params': {'kind': event[1], 'label': event[2]}, 'quotes': [ask, event[0], span(t, r'driven\s+by') or '', span(t, r'small\s+group') or '', span(t, r'patients') or '']}

    @classmethod
    def run(cls, ctx, params):
        kind, label = params['kind'], params['label']
        rows = ctx.rows(f"SELECT u.patient_id AS patient_id, COUNT(*) AS n FROM utilization_event u JOIN patient p ON p.patient_id = u.patient_id WHERE u.kind = {q(kind)} "
                        f"AND {ctx.in_window('u.event_date')} AND {ctx.living} GROUP BY u.patient_id ORDER BY n DESC, u.patient_id")
        users = len(rows)
        total = sum(r['n'] for r in rows)
        if not users:
            return dict(headline=f'No {label} in the last 12 months', facts=[fact('none', f'No living patient has {label} in the last 12 months.')], read_back='', notes=[])
        facts = [fact('users', f"{users:,} patients had at least one of the {total:,} {label} in the last 12 months.", value=users, unit='patients', visits=total)]
        table = []
        for share in (0.01, 0.05, 0.10, 0.20):
            k = max(1, int(users * share + 0.5))
            part = sum(r['n'] for r in rows[:k])
            facts.append(fact(f'top{int(share * 100)}', f"The top {int(share * 100)}% of {label} users ({k:,} patients) account for {pct(part / total)}% of {label} ({part:,} of {total:,}) = {part / total:.4f}.",
                              value=pct(part / total), numerator=part, denominator=total, unit='%', patients=k))
            table.append({'top_percent': int(share * 100), 'patients': k, label.replace(' ', '_'): part, 'share_percent': pct(part / total)})
        running, half = 0, None
        for i, r in enumerate(rows, 1):
            running += r['n']
            if running * 2 >= total:
                half = i
                break
        facts.append(fact('half', f"The most frequent {half:,} {label} users account for half of the {total:,} {label} ({pct(half / users)}% of users).", value=half, unit='patients', denominator=users))
        repeat = sum(1 for r in rows if r['n'] >= 2)
        facts.append(fact('repeat', f"{repeat:,} patients ({pct(repeat / users)}% of users) had two or more {label}; they account for {pct(sum(r['n'] for r in rows if r['n'] >= 2) / total)}% of {label}.",
                          value=repeat, unit='patients'))
        return dict(headline=f"{cap(label)} are concentrated: the top 5% of users account for {pct(table[1]['share_percent'] / 100)}% and {half:,} of {users:,} users account for half",
                    facts=facts, table=table, tables={'concentration': table},
                    read_back=f"Concentration of {label} in the 12 months ending {ctx.as_of} across living patients with at least one: share of visits by the top users and the number of users that account for half.",
                    notes=[f'{label.capitalize()} are the all-source utilization_event rows (EHR, ADT and claims, de-duplicated); counts are per living patient.'])


# ============================================================================================================================
# who is lagging on a vaccine / screening / test, by payer type or practice
# ============================================================================================================================
COVERAGE_GROUPS = {  # phrase -> (label, patient.coverage_type values)
    'commercial': ('commercial', ['commercial']), 'medicare': ('medicare', ['medicare', 'dual', 'medigap']), 'medicaid': ('medicaid', ['medicaid']),
    'uninsured': ('uninsured', ['uninsured']), 'dual': ('dual', ['dual']), 'medigap': ('medigap', ['medigap']),
}
GROUP_DIMS = r'payer\s+types?|coverage(?:\s+types?)?|lines?\s+of\s+business|insurance(?:\s+types?)?|payers?'
LAG = (r"(?:who(?:'s|\s+is|\s+are)|is\s+anyone|are\s+(?:we|any\s+(?:payers?|groups?|practices?))|which\s+(?:payers?|coverage\s+types?|lines?\s+of\s+business|practices?|groups?)\s+(?:is|are))"
       r"\s+(?:lagging|behind|falling\s+behind|low|worst|weakest)\s+(?:on|in|with)\s+")


def evidence_sql(ctx, parser, thing, window):
    """(SQL predicate over alias p, quote) for a named kind of received evidence (vaccine, screening, wellness visit...) dated in the window, or None."""
    found = parser.thing(thing)
    if not found or found[0] == 'measurement' or found[4].strip(): return None
    table, field, atoms, quote, _ = found
    parts = []
    for a in atoms:
        col = a['field'].partition('.')[2]
        op, v = a['op'], a['value']
        if op == '=': parts.append(f"t.{col} = {q(v)}")
        elif op == 'in': parts.append(f"t.{col} IN ({','.join(q(x) for x in v)})")
        elif op == 'like': parts.append(f"t.{col} LIKE {q(v)}")
        elif op == 'not_null': parts.append(f"t.{col} IS NOT NULL")
        else: return None
    parts.append(ctx.in_window(f't.{field}', *window))
    return f"EXISTS (SELECT 1 FROM {table} t WHERE t.patient_id = p.patient_id AND {' AND '.join(parts)})", quote


@family
class EvidenceGap:
    NAME = 'evidence_gap'
    WORDS = set('''is anyone lagging on break it out who's are we which has the lowest rate by across'''.split())

    @classmethod
    def parse(cls, text, catalog):
        t = text.strip().rstrip('?.')
        lag = re.search(LAG + r"(?P<thing>[^—–,?]+?)(?=\s*(?:[—–,]|$))", t, re.I)
        if not lag: return None
        from .request_language import Parser
        parser = Parser(catalog, t)
        thing = lag['thing'].strip()
        probe = parser.thing(thing)
        if not probe or probe[0] == 'measurement' or probe[4].strip(): return None
        tail = t[lag.end():]
        by = re.search(r"(?:break\s+(?:it\s+)?(?:out|down)(?:\s+by)?|broken\s+(?:out|down)\s+by|split(?:\s+it)?\s+(?:up\s+)?by|by|across|for)\s+(?P<g>.+)$", tail, re.I)
        if not by: return None
        g = by['g'].strip()
        dim = re.fullmatch(GROUP_DIMS, g, re.I) or re.fullmatch(r'practices?', g, re.I)
        groups = None
        if not dim:
            items = [re.sub(r'^(?:and|or)\s+', '', x.strip(), flags=re.I) for x in re.split(r',|\s+and\s+|\s+or\s+', g) if x.strip()]
            groups = [COVERAGE_GROUPS[i.lower()] for i in items if i.lower() in COVERAGE_GROUPS]
            if len(groups) != len(items) or len(items) < 2: return None
        window = None
        years = years_in(t)
        return {'params': {'thing': thing, 'group': 'practice' if (dim and re.fullmatch(r'practices?', g, re.I)) else ('payer_type' if dim or groups else None),
                           'cats': [list(x[:1]) + [x[1]] for x in groups] if groups else None, 'year': years[-1] if years else None},
                'quotes': [lag.group(), thing, by.group(), *[str(y) for y in years]]}

    @classmethod
    def run(cls, ctx, params):
        from .request_language import Parser
        parser = Parser_for(ctx, params['thing'])
        year = params['year']
        window = (f'{year}-01-01', f'{year}-12-31') if year else (None, None)
        got = evidence_sql(ctx, parser, params['thing'], window)
        if not got: raise Declined('unrecognised evidence')
        predicate, quote = got
        if params['group'] == 'practice':
            groups_sql, label_name = ctx.practice, 'practice'
            rows = ctx.rows(f"SELECT {groups_sql} AS grp, COUNT(*) AS n, SUM(CASE WHEN {predicate} THEN 1 ELSE 0 END) AS k FROM patient p WHERE {ctx.living} GROUP BY grp HAVING grp IS NOT NULL AND COUNT(*) >= 30")
        else:
            cats = params['cats'] or [[label, vals] for label, vals in COVERAGE_GROUPS.values() if label in ('commercial', 'medicare', 'medicaid')]
            rows = []
            for label, vals in cats:
                r = ctx.rows(f"SELECT COUNT(*) AS n, SUM(CASE WHEN {predicate} THEN 1 ELSE 0 END) AS k FROM patient p WHERE {ctx.living} AND p.coverage_type IN ({','.join(q(v) for v in vals)})")[0]
                rows.append({'grp': label, 'n': r['n'], 'k': r['k'] or 0})
            label_name = 'payer type'
        for r in rows: r['rate'] = r['k'] / r['n'] if r['n'] else None
        rows = [r for r in rows if r['rate'] is not None]
        if not rows: return dict(headline='No group has patients', facts=[fact('none', 'No group has living patients.')], read_back='', notes=[])
        period = f'in {year}' if year else 'in the last 12 months'
        ordered = sorted(rows, key=lambda r: (r['rate'], str(r['grp'])))
        facts = [fact('lowest', f"{ordered[0]['grp']} has the lowest {quote} share {period}: {pct(ordered[0]['rate'])}% ({ordered[0]['k']:,} of {ordered[0]['n']:,} living patients).", value=ordered[0]['grp'])]
        for r in sorted(rows, key=lambda r: str(r['grp'])):
            facts.append(fact(f"g_{r['grp']}", f"{r['grp']} {quote} share {period}: {pct(r['rate'])}% ({r['k']:,} of {r['n']:,} living patients) = {r['rate']:.4f}.",
                              value=pct(r['rate']), numerator=r['k'], denominator=r['n'], unit='%'))
        return dict(headline=f"{ordered[0]['grp']} is lowest on {quote} ({pct(ordered[0]['rate'])}%) {period}", facts=facts,
                    table=[{label_name: r['grp'], 'patients': r['n'], 'with_evidence': r['k'], 'share_percent': pct(r['rate'])} for r in ordered],
                    read_back=f"Share of living patients with {quote} {period}, by {label_name}; the group with the lowest share is named.",
                    notes=['The denominator is every living patient of the group; a patient has the evidence when a completed record is dated in the window.'])


def Parser_for(ctx, text):
    from .request_language import Parser
    from .request_plan import Catalog
    return Parser(Catalog.load(ctx.stack_path), text)


# ============================================================================================================================
# a measure over the months / quarters of a year: highest, lowest and the trend
# ============================================================================================================================
MONTH_NAMES_LONG = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
SERIES_METRICS = [  # phrase -> key
    (r'medical\s+(?:claims?|spend(?:ing)?|paid|costs?)|claims?\s+spend|paid\s+claims', 'medical'),
    (r'pharmacy\s+(?:claims?|spend(?:ing)?|costs?|bill)|drug\s+spend', 'pharmacy'),
    (r'medical\s+pmpm|pmpm|per\s+member\s+per\s+month', 'pmpm'),
    (r'ed\s+visits?|emergency(?:\s+department)?\s+visits?', 'ed'),
    (r'(?:hospital\s+|inpatient\s+)?admissions', 'admissions'),
]
SERIES_LABELS = {'medical': ('medical claims paid', 'USD'), 'pharmacy': ('pharmacy plan-paid', 'USD'), 'pmpm': ('medical paid PMPM', 'USD per member month'),
                 'ed': ('ED visits', 'visits'), 'admissions': ('hospital admissions', 'admissions')}


@family
class PeriodSeries:
    NAME = 'period_series'
    WORDS = set('''which of was were our most least expensive costly cheapest costliest highest lowest and the for on by in over time trend monthly month quarterly quarter
        what is show me how has did change changed'''.split())
    SUP = {'most': 'high', 'costliest': 'high', 'highest': 'high', 'expensive': 'high', 'least': 'low', 'cheapest': 'low', 'lowest': 'low'}

    @classmethod
    def parse(cls, text, catalog):
        t = text.strip().rstrip('?.')
        metric = next(((span(t, p), k) for p, k in SERIES_METRICS if span(t, p)), None)
        if not metric: return None
        years = years_in(t)
        grain = 'quarter' if span(t, r'quarter\w*') else 'month'
        rank = re.match(r'^which\s+(?:month|quarter)\s+(?:of|in)\s+(?:20\d\d)\s+(?:was|were|is)\s+(?:our\s+)?(?:the\s+)?(?:most|least)\s+(?:expensive|costly)\b', t, re.I) \
            or re.match(r'^which\s+(?:month|quarter)\s+(?:of|in)\s+(?:20\d\d)\s+(?:had|has)\s+(?:the\s+)?(?:highest|lowest|most|fewest)\b', t, re.I)
        trend = span(t, r'trend|monthly|quarterly|by\s+(?:month|quarter)|month\s+by\s+month|over\s+time|each\s+month')
        if not rank and not trend: return None
        if len(years) > 1: return None
        extra = [span(t, r'and\s+which\s+(?:was|were)\s+the\s+(?:cheapest|costliest|most\s+expensive|least\s+expensive|highest|lowest)') or '', span(t, r'cheapest|costliest') or '']
        return {'params': {'metric': metric[1], 'grain': grain, 'year': years[0] if years else None, 'ranked': bool(rank)},
                'quotes': [metric[0], rank.group() if rank else trend, *extra, *[str(y) for y in years], span(t, r'quarter\w*|month\w*') or '']}

    @classmethod
    def run(cls, ctx, params):
        y = params['year'] or last_full_year(ctx)
        notes = [] if params['year'] else [FULL_YEAR_NOTE.format(y=y, a=ctx.as_of[:4])]
        metric, grain = params['metric'], params['grain']
        label, unit = SERIES_LABELS[metric]
        bucket = {'month': lambda col: f"substr({col}, 1, 7)", 'quarter': lambda col: f"substr({col}, 1, 4) || '-Q' || ((CAST(substr({col}, 6, 2) AS INTEGER) + 2) / 3)"}[grain]
        if metric == 'medical':
            rows = ctx.rows(f"SELECT {bucket('c.service_from')} AS period, SUM(c.paid_cents) / 100.0 AS value FROM payer_claim c WHERE c.counts_in_metrics = 1 AND substr(c.service_from, 1, 4) = {q(str(y))} GROUP BY period ORDER BY period")
            notes.append('Medical paid is paid dollars on counted payer-adjudicated claims (current non-duplicate versions) by service date.')
        elif metric == 'pharmacy':
            rows = ctx.rows(f"SELECT {bucket('fill_date')} AS period, SUM(plan_paid_cents) / 100.0 AS value FROM payer_pharmacy_claim WHERE counts_as_fill = 1 AND substr(fill_date, 1, 4) = {q(str(y))} GROUP BY period ORDER BY period")
            notes.append('Pharmacy spend is plan-paid dollars on pharmacy claims that count as fills, by fill date.')
        elif metric == 'pmpm':
            rows = ctx.rows(f"WITH mm AS (SELECT {bucket('m.month')} AS period, COUNT(*) AS months FROM member_month m WHERE substr(m.month, 1, 4) = {q(str(y))} GROUP BY period), "
                            f"paid AS (SELECT {bucket('c.service_from')} AS period, SUM(c.paid_cents) / 100.0 AS dollars FROM payer_claim c JOIN member_month m ON m.patient_id = c.patient_id AND m.month = substr(c.service_from, 1, 7) "
                            f"WHERE c.counts_in_metrics = 1 AND substr(c.service_from, 1, 4) = {q(str(y))} GROUP BY period) "
                            f"SELECT mm.period AS period, COALESCE(paid.dollars, 0) / mm.months AS value FROM mm LEFT JOIN paid USING (period) ORDER BY period")
            notes.append('PMPM = paid dollars on counted claims incurred in an enrolled member month, over that period\'s member months (the governed paid PMPM rule).')
        else:
            kind = 'ed_visit' if metric == 'ed' else 'inpatient'
            rows = ctx.rows(f"SELECT {bucket('u.event_date')} AS period, COUNT(*) AS value FROM utilization_event u WHERE u.kind = {q(kind)} AND substr(u.event_date, 1, 4) = {q(str(y))} GROUP BY period ORDER BY period")
            notes.append(f'{cap(label)} are the all-source utilization_event rows (EHR, ADT and claims, de-duplicated), by event date.')
        rows = [r for r in rows if r['value'] is not None]
        if len(rows) < 2: return dict(headline=f'Not enough {grain}s of {label} in {y}', facts=[fact('none', f'Fewer than two {grain}s of {label} exist in {y}.')], read_back='', notes=notes)
        money = unit.startswith('USD')

        def name(period):
            if grain == 'month': return f"{MONTH_NAMES_LONG[int(period[5:7]) - 1]} {period[:4]} ({period})"
            return period
        fmt = (lambda v: usd(v)) if money and metric != 'pmpm' else (lambda v: f'${v:,.2f}') if money else (lambda v: f'{v:,.0f}')
        hi = max(rows, key=lambda r: (r['value'], r['period']))
        lo = min(rows, key=lambda r: (r['value'], r['period']))
        facts = [fact('highest', f"{name(hi['period'])} was the highest {grain} of {label} in {y}: {fmt(hi['value'])}.", value=hi['period'], amount=round(hi['value'], 2)),
                 fact('lowest', f"{name(lo['period'])} was the lowest {grain} of {label} in {y}: {fmt(lo['value'])}.", value=lo['period'], amount=round(lo['value'], 2))]
        if money:
            facts[0]['text'] = f"{name(hi['period'])} was the most expensive {grain} of {label} in {y} ({fmt(hi['value'])})."
            facts[1]['text'] = f"{name(lo['period'])} was the cheapest {grain} of {label} in {y} ({fmt(lo['value'])})."
        first, last = rows[0], rows[-1]
        change = (last['value'] - first['value']) / first['value'] if first['value'] else None
        facts.append(fact('trend', f"{cap(label)} went from {fmt(first['value'])} in {name(first['period'])} to {fmt(last['value'])} in {name(last['period'])}"
                          + (f" ({'+' if change > 0 else ''}{change * 100:.1f}%)." if change is not None else '.'), value=round(change * 100, 1) if change is not None else None, unit='%'))
        for r in rows:
            facts.append(fact(f"p_{r['period']}", f"{name(r['period'])}: {fmt(r['value'])}.", value=round(r['value'], 2), unit=unit))
        table = [{grain: r['period'], label.replace(' ', '_'): round(r['value'], 2)} for r in rows]
        return dict(headline=f"{name(hi['period'])} is the highest {grain} of {label} in {y} ({fmt(hi['value'])}); {name(lo['period'])} the lowest ({fmt(lo['value'])})", facts=facts, table=table, tables={'series': table},
                    read_back=f"{cap(label)} by {grain} for {y}: the highest and lowest {grain} and the change from the first to the last {grain}.", notes=notes)


# ============================================================================================================================
# a cohort the question defines by ED visits / admissions in the last year: how many, and what they cost
# ============================================================================================================================
NUM_WORDS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6}


@family
class CohortDefined:
    NAME = 'cohort_defined'
    WORDS = set('''who's whos who are here i mean how many and what do they cost rising risk in the last year but no yet hospital'''.split())
    DEF = re.compile(r"(?P<n>\d+|one|two|three|four|five|six)\s+or\s+more\s+(?:ed|er|emergency(?:\s+(?:department|room))?)\s+visits?\s+(?:in|during|over)\s+the\s+(?:last|past)\s+(?:year|12\s+months)"
                     r"\s*,?\s*(?:but|and)\s+(?:no|zero|not\s+(?:a\s+single|one))\s+(?:hospital\s+|inpatient\s+)?(?:admissions?|stays?)(?:\s+yet)?", re.I)

    @classmethod
    def parse(cls, text, catalog):
        t = text.strip().rstrip('?.')
        d = cls.DEF.search(t)
        if not d: return None
        # the cohort must be DEFINED in the question ("I mean ..."): a label alone ("rising risk") has no single definition and is not guessed
        lead = re.search(r"(?:\bi\s+mean|\bmeaning|\bdefined\s+as|\bthat\s+is|\bi\.e\.)\s+$", t[:d.start()], re.I)
        if not lead: return None
        tail = t[d.end():]
        wants_cost = bool(re.search(r'what\s+do\s+they\s+cost|what\s+they\s+cost|their\s+cost|total\s+cost', tail, re.I))
        wants_count = bool(re.search(r'how\s+many|number\s+of', t, re.I))
        if not (wants_cost or wants_count): return None
        n = int(d['n']) if d['n'].isdigit() else NUM_WORDS[d['n'].lower()]
        return {'params': {'n': n, 'cost': wants_cost, 'window': 'rolling_12_months'}, 'quotes': [t[:d.start()].strip(), d.group(), tail.strip(' ,—–')]}

    @classmethod
    def run(cls, ctx, params):
        n = params['n']
        pop = f"{ctx.events('ed_visit', n)} AND NOT {ctx.events('inpatient', 1)}"
        ids_sql = ctx.patients(pop)
        count = ctx.count(pop)
        facts = [fact('count', f"{count:,} patients had {n} or more ED visits in the last 12 months and no hospital admission in that time.", value=count, unit='patients')]
        table = [{'patients': count}]
        headline = f"{count:,} patients with {n}+ ED visits and no admission in the last 12 months"
        if params['cost']:
            paid = ctx.one(f"SELECT COALESCE(SUM(c.paid_cents), 0) FROM payer_claim c WHERE c.counts_in_metrics = 1 AND {ctx.in_window('c.service_from')} AND c.patient_id IN ({ids_sql})", 0) / 100.0
            facts.append(fact('cost', f"They accounted for {usd(paid)} of medical paid on counted claims in the last 12 months ({usd(paid / count) if count else '$0'} per patient).", value=round(paid, 2), unit='USD'))
            table[0]['paid_dollars'] = round(paid, 2)
            headline += f"; {usd(paid)} paid"
        ids = ctx.ids(pop)
        return dict(headline=headline, facts=facts, table=table, patient_ids=ids, cohort_role='listed',
                    read_back=f"Living patients with {n} or more all-source ED visits and no inpatient event in the 12 months ending {ctx.as_of}" + (', and the medical paid dollars of those patients in the same window.' if params['cost'] else '.'),
                    notes=['ED visits and admissions are the all-source utilization_event rows; medical paid is paid dollars on counted payer-adjudicated claims by service date.'])


# ============================================================================================================================
# patients with N or more of a list of chronic conditions versus the rest: cost per patient
# ============================================================================================================================
@family
class ComorbidityCost:
    NAME = 'comorbidity_cost'
    WORDS = set('''how much more do patients with or chronic conditions cost than the rest'''.split())

    @classmethod
    def parse(cls, text, catalog):
        t = text.strip().rstrip('?.')
        m = re.match(r"^how\s+much\s+more\s+do\s+patients\s+with\s+(?P<n>\d+|two|three|four|five)\s+or\s+more\s+(?:chronic\s+)?conditions\s+cost\s+than\s+(?:the\s+rest|everyone\s+else|others|the\s+other\s+patients|all\s+(?:the\s+)?others)"
                     r"\s*\((?P<list>[^)]+)\)\s*(?:in\s+(?P<year>20\d\d))?$", t, re.I)
        if not m: return None
        names = []
        for item in re.split(r',|\s+and\s+|\s+or\s+', m['list']):
            item = item.strip()
            if not item: continue
            hit = next((c for p, c in CONDITION_WORDS if re.fullmatch(p, item, re.I)), None)
            if not hit: return None
            names.append(hit)
        if len(set(names)) < 2: return None
        n = int(m['n']) if m['n'].isdigit() else NUM_WORDS[m['n'].lower()]
        if n > len(names): return None
        return {'params': {'n': n, 'conditions': names, 'year': int(m['year']) if m['year'] else None},
                'quotes': [m.group(), m['list']]}

    @classmethod
    def run(cls, ctx, params):
        y = params['year'] or last_full_year(ctx)
        notes = [] if params['year'] else [FULL_YEAR_NOTE.format(y=y, a=ctx.as_of[:4])]
        score = ' + '.join(f"(CASE WHEN {ctx.cond(c)} THEN 1 ELSE 0 END)" for c in params['conditions'])
        rows = ctx.rows(f"WITH paid AS (SELECT patient_id, SUM(paid_cents) AS cents FROM payer_claim WHERE counts_in_metrics = 1 AND substr(service_from, 1, 4) = {q(str(y))} GROUP BY patient_id), "
                        f"scored AS (SELECT p.patient_id AS patient_id, ({score}) AS k, COALESCE(paid.cents, 0) AS cents FROM patient p LEFT JOIN paid ON paid.patient_id = p.patient_id WHERE {ctx.living}) "
                        f"SELECT (k >= {params['n']}) AS high, COUNT(*) AS patients, SUM(cents) / 100.0 AS dollars FROM scored GROUP BY high")
        by = {int(r['high']): r for r in rows}
        hi, lo = by.get(1), by.get(0)
        if not hi or not lo: return dict(headline='One of the two groups is empty', facts=[fact('none', 'One of the two groups has no patients.')], read_back='', notes=notes)
        a, b = hi['dollars'] / hi['patients'], lo['dollars'] / lo['patients']
        names = ', '.join(CONDITION_LABELS.get(c, c) for c in params['conditions'])
        facts = [fact('n', f"{hi['patients']:,} living patients have {params['n']} or more of the {len(params['conditions'])} conditions ({names}).", value=hi['patients'], unit='patients'),
                 fact('avg_high', f"Average {y} medical paid per patient for them is {usd(a)} ({usd(hi['dollars'])} over {hi['patients']:,} patients, patients without claims included).", value=round(a, 2),
                      numerator=round(hi['dollars'], 2), denominator=hi['patients'], unit='USD'),
                 fact('avg_rest', f"Average {y} medical paid per patient for everyone else is {usd(b)} ({usd(lo['dollars'])} over {lo['patients']:,} patients).", value=round(b, 2),
                      numerator=round(lo['dollars'], 2), denominator=lo['patients'], unit='USD'),
                 fact('gap', f"They cost {usd(a - b)} more per patient ({a / b:.2f} times the rest).", value=round(a - b, 2), unit='USD')]
        return dict(headline=f"Patients with {params['n']}+ of the conditions cost {usd(a)} per patient in {y} versus {usd(b)} for the rest ({usd(a - b)} more)", facts=facts,
                    table=[{'group': f"{params['n']}+ conditions", 'patients': hi['patients'], 'avg_paid_dollars': round(a, 2)}, {'group': 'everyone else', 'patients': lo['patients'], 'avg_paid_dollars': round(b, 2)}],
                    read_back=f"Average {y} counted medical paid dollars per living patient, for patients with {params['n']} or more of ({names}) versus all other living patients; patients with no claims count as $0.",
                    notes=notes + ['Conditions use the canonical condition evidence (EHR, problem list and counted claim diagnoses; diabetes and CKD also by lab rule).'])


# ============================================================================================================================
# are <drug classes> what is pushing up the pharmacy bill
# ============================================================================================================================
@family
class DrugSpendDrivers:
    NAME = 'drug_spend_drivers'
    WORDS = set('''are is the what's whats what pushing driving up our pharmacy bill spend behind'''.split())

    @classmethod
    def parse(cls, text, catalog):
        t = text.strip().rstrip('?.')
        m = re.match(r"^(?:are|is)\s+(?:the\s+)?(?P<classes>.+?)\s+what(?:'s|\s+is)\s+(?:pushing|driving|behind)\s+(?:up\s+)?(?:our\s+)?(?:the\s+)?pharmacy\s+(?:bill|spend(?:ing)?|costs?)(?:\s+(?:in|for)\s+(?P<year>20\d\d))?$", t, re.I)
        if not m: return None
        from .request_language import Parser
        drugs = Parser(catalog, t).drugs
        classes, quotes = [], []
        for item in re.split(r',|\s+and\s+|\s+or\s+|&', m['classes']):
            item = re.sub(r'^(?:the\s+)', '', item.strip(), flags=re.I)
            if not item: continue
            hit = drugs.get(item.lower()) or drugs.get(item.lower().rstrip('s'))
            if not hit: return None
            classes.extend(c for c in hit if c not in classes)
            quotes.append(item)
        if not classes: return None
        label = (', '.join(quotes[:-1]) + ' and ' + quotes[-1]) if len(quotes) > 1 else quotes[0]  # the classes in the asker's own words
        return {'params': {'classes': classes, 'label': label, 'year': int(m['year']) if m['year'] else None}, 'quotes': [m.group(), *quotes]}

    @classmethod
    def run(cls, ctx, params):
        y1 = params['year'] or last_full_year(ctx)
        y0 = y1 - 1
        notes = [] if params['year'] else [FULL_YEAR_NOTE.format(y=y1, a=ctx.as_of[:4])]
        marks = ','.join(q(c) for c in params['classes'])
        totals, part = {}, {}
        names = [r['drug_name'] for r in ctx.rows(f"SELECT DISTINCT drug_name FROM fill WHERE drug_class IN ({marks}) AND drug_name IS NOT NULL")]
        for y in (y0, y1):
            totals[y] = (ctx.one(f"SELECT COALESCE(SUM(plan_paid_cents), 0) FROM payer_pharmacy_claim WHERE counts_as_fill = 1 AND substr(fill_date, 1, 4) = {q(str(y))}", 0) or 0) / 100.0
            part[y] = (ctx.one(f"SELECT COALESCE(SUM(plan_paid_cents), 0) FROM payer_pharmacy_claim WHERE counts_as_fill = 1 AND substr(fill_date, 1, 4) = {q(str(y))} "
                               f"AND drug_name IN ({','.join(q(n) for n in names) or q('')})", 0) or 0) / 100.0
        label = params.get('label') or ', '.join(c.replace('_', ' ') for c in params['classes'])
        d_total, d_part = totals[y1] - totals[y0], part[y1] - part[y0]
        share1, share0 = part[y1] / totals[y1] if totals[y1] else None, part[y0] / totals[y0] if totals[y0] else None
        facts = [fact('total0', f"Plan-paid pharmacy spend in {y0} was {usd(totals[y0])}.", value=round(totals[y0], 2), unit='USD'),
                 fact('total1', f"Plan-paid pharmacy spend in {y1} was {usd(totals[y1])}.", value=round(totals[y1], 2), unit='USD'),
                 fact('total_change', f"Total plan-paid pharmacy spend {'rose' if d_total > 0 else 'fell'} by {usd(abs(d_total))} ({'+' if d_total > 0 else '-'}{abs(d_total) / totals[y0] * 100:.1f}%) from {y0} to {y1}.",
                      value=round(d_total, 2), unit='USD'),
                 fact('class_share', f"The {label} drugs are {pct(share1)}% of {y1} plan-paid pharmacy dollars ({usd(part[y1])} of {usd(totals[y1])}) = {share1:.4f}.", value=pct(share1),
                      numerator=round(part[y1], 2), denominator=round(totals[y1], 2), unit='%'),
                 fact('class_change', f"Spend on the {label} drugs {'rose' if d_part > 0 else 'fell'} by {usd(abs(d_part))} from {usd(part[y0])} in {y0} to {usd(part[y1])} in {y1}"
                      + (f" ({pct(share0)}% of {y0} spend)." if share0 is not None else '.'), value=round(d_part, 2), unit='USD')]
        verdict = ('No: total pharmacy spend fell, so these drugs are a large share of it but are not pushing it up.' if d_total < 0 and d_part <= 0 else
                   'Yes: they account for most of the rise.' if d_total > 0 and d_part > 0 and d_part >= d_total * 0.5 else
                   'Partly: their spend rose but accounts for less than half of the change.' if d_total > 0 and d_part > 0 else
                   'No: their spend fell or did not rise while total spend moved.' if d_part <= 0 else
                   'These drugs rose while total pharmacy spend fell.')
        facts.insert(0, fact('answer', f"{verdict} Total spend went from {usd(totals[y0])} to {usd(totals[y1])}; the {label} drugs from {usd(part[y0])} to {usd(part[y1])}.", value=verdict))
        return dict(headline=f"{verdict.split(':')[0]}: pharmacy spend {y0} {usd(totals[y0])} to {y1} {usd(totals[y1])}; {label} {pct(share1)}% of {y1}", facts=facts,
                    table=[{'year': y0, 'pharmacy_plan_paid': round(totals[y0], 2), 'class_plan_paid': round(part[y0], 2)}, {'year': y1, 'pharmacy_plan_paid': round(totals[y1], 2), 'class_plan_paid': round(part[y1], 2)}],
                    read_back=f"Plan-paid pharmacy spend for {y0} and {y1} and the share and change of the {label} drugs (fills whose received drug name belongs to those classes).",
                    notes=notes + ['Pharmacy spend is plan-paid dollars on pharmacy claims that count as fills; a drug belongs to a class by the class assigned to its received drug name in the canonical fill table.'])


# ============================================================================================================================
# which payer / place of service / ... has the worst denial (medical) or rejection (pharmacy) rate, and the usual reason
# ============================================================================================================================
OUTCOMES = {
    'denial': dict(table='payer_claim', base="c.counts_in_metrics = 1", numerator="c.final_status IN ('denied', 'denied_upheld')", clock='c.service_from',
                   noun='claims', rate_label='denial rate', reason='c.carc', reason_label='denial reason code (CARC)',
                   dims={'place of service': ('c.place_of_service', 'place of service'), 'payer': ('c.payer_id', 'payer'), 'claim type': ('c.claim_type', 'claim type'),
                         'network status': ('c.network_status', 'network status')}),
    'reject': dict(table='payer_pharmacy_claim', base="c.claim_status IN ('paid', 'rejected')", numerator="c.claim_status = 'rejected'", clock='c.fill_date',
                   noun='pharmacy claims', rate_label='reject rate', reason='c.reject_code', reason_label='reject code',
                   dims={'payer': ('c.payer_id', 'payer'), 'pharmacy type': ('c.pharmacy_type', 'pharmacy type'), 'formulary tier': ('c.formulary_tier', 'formulary tier'),
                         'tier': ('c.formulary_tier', 'formulary tier')}),
}
OUTCOME_WORDS = {'denial': r'denial\s+rate|denials?|denied|deny|denies|denying', 'reject': r'reject(?:ion)?\s+rate|reject(?:s|ed|ion|ions)?|bounce[sd]?|bouncing|bounce[- ]?backs?'}
MIN_OUTCOME_ROWS = 500  # a group's rate is ranked only above this many claims in the year (stated in the answer; the same floor the denial drivers use)
FRAME_FILLER = set('where are is our we claims claim getting being get got do does the'.split())


@family
class OutcomeRates:
    NAME = 'outcome_rates'
    WORDS = set('''which has have the worst highest lowest best rate in and what's whats what usual most reason payer's payers claims by'''.split())

    @classmethod
    def parse(cls, text, catalog):
        t = text.strip().rstrip('?.')
        frame = re.match(r'^(?P<frame>[^—–]{6,90}?)\s*[—–]\s*(?P<ask>(?:which|what)\b.+)$', t, re.I)
        ask = frame['ask'] if frame else t
        outcome = next((k for k, p in OUTCOME_WORDS.items() if re.search(r'\b(?:' + p + r')\b', ask, re.I)), None)
        if not outcome: return None
        spec = OUTCOMES[outcome]
        dims = '|'.join(sorted((d.replace(' ', r'\s+') for d in spec['dims']), key=len, reverse=True))
        years = years_in(t)
        quotes = []
        grouped = re.match(r"^which\s+(?P<dim>" + dims + r")\s+(?:has|have|had)\s+the\s+(?P<sup>worst|highest|lowest|best)\s+(?P<out>" + OUTCOME_WORDS[outcome] + r")(?:\s+rate)?\b(?P<rest>.*)$", ask, re.I)
        possessive = re.match(r"^which\s+payer(?:'s|s'?)\s+(?:(?:pharmacy|medical)\s+)?claims\s+(?P<out>" + OUTCOME_WORDS[outcome] + r")\s+the\s+most\b(?P<rest>.*)$", ask, re.I)
        denies = re.match(r"^which\s+payer\s+(?P<out>denies|rejects)\s+the\s+most\s+(?:(?:pharmacy|medical)\s+)?claims\b(?P<rest>.*)$", ask, re.I)
        if grouped:
            dim = re.sub(r'\s+', ' ', grouped['dim'].lower())
            sup = 'low' if grouped['sup'].lower() in ('lowest', 'best') else 'high'
            quotes += [grouped.group()[:grouped.start('rest')]]
            rest = grouped['rest']
        elif possessive or denies:
            m = possessive or denies
            dim, sup, rest = 'payer', 'high', m['rest']
            quotes += [ask[:m.start('rest')]]
        else:
            return None
        reason = re.search(r"(?:,\s*)?(?:and\s+)?what(?:'s|\s+is)\s+the\s+(?:usual|most\s+common|typical|main|top)\s+(?:(?:denial|reject(?:ion)?)\s+)?(?:reason|code|cause)s?", rest, re.I)
        if reason:
            quotes.append(reason.group())
            rest = rest[:reason.start()] + rest[reason.end():]
        rest = rest.strip(' ,')
        year = re.fullmatch(r'(?:in|for|during)\s+(20\d\d)', rest, re.I) if rest else None
        if rest and not year: return None
        if year: quotes.append(rest)
        if dim not in spec['dims']: return None
        if frame:  # the framing clause only restates the outcome ("Where are claims getting denied"): its words must all be framing or outcome words
            left = [w for w in re.findall(r'[a-z]+', frame['frame'].lower()) if w not in FRAME_FILLER and not re.fullmatch(OUTCOME_WORDS[outcome], w)]
            if left: return None
            quotes.append(frame['frame'])
        return {'params': {'outcome': outcome, 'dim': dim, 'sup': sup, 'reason': bool(reason), 'year': int(year[1]) if year else None}, 'quotes': quotes}

    @classmethod
    def run(cls, ctx, params):
        spec = OUTCOMES[params['outcome']]
        y = params['year'] or last_full_year(ctx)
        notes = [] if params['year'] else [FULL_YEAR_NOTE.format(y=y, a=ctx.as_of[:4])]
        column, dim_label = spec['dims'][params['dim']]
        base = f"{spec['base']} AND substr({spec['clock']}, 1, 4) = {q(str(y))}"
        t = spec['table']
        rows = ctx.rows(f"SELECT {column} AS grp, COUNT(*) AS n, SUM(CASE WHEN {spec['numerator']} THEN 1 ELSE 0 END) AS k, 1.0 * SUM(CASE WHEN {spec['numerator']} THEN 1 ELSE 0 END) / COUNT(*) AS rate "
                        f"FROM {t} c WHERE {base} AND {column} IS NOT NULL GROUP BY grp HAVING COUNT(*) >= {MIN_OUTCOME_ROWS} ORDER BY rate {'ASC' if params['sup'] == 'low' else 'DESC'}, grp")
        overall = ctx.rows(f"SELECT COUNT(*) AS n, SUM(CASE WHEN {spec['numerator']} THEN 1 ELSE 0 END) AS k FROM {t} c WHERE {base}")[0]
        if not rows: return dict(headline=f'No {dim_label} has {MIN_OUTCOME_ROWS} {spec["noun"]} in {y}', facts=[fact('none', f'No {dim_label} has at least {MIN_OUTCOME_ROWS} {spec["noun"]} in {y}.')], read_back='', notes=notes)
        word = 'lowest' if params['sup'] == 'low' else 'highest'
        top = rows[0]
        facts = [fact('top', f"{dim_label.capitalize()} {top['grp']} has the {word} {spec['rate_label']} in {y}: {pct(top['rate'])}% ({top['k']:,} of {top['n']:,} {spec['noun']}) = {top['rate']:.4f}.",
                      value=top['grp'], numerator=top['k'], denominator=top['n']),
                 fact('rate', f"The {spec['rate_label']} at {dim_label} {top['grp']} is {pct(top['rate'])}%.", value=pct(top['rate']), numerator=top['k'], denominator=top['n'], unit='%')]
        for i, r in enumerate(rows[1:4], 1):
            facts.append(fact(f'next_{i}', f"{dim_label.capitalize()} {r['grp']}: {pct(r['rate'])}% ({r['k']:,} of {r['n']:,}) = {r['rate']:.4f}.", value=pct(r['rate']), numerator=r['k'], denominator=r['n'], unit='%'))
        facts.append(fact('overall', f"Across all {spec['noun']} in {y}: {pct(overall['k'] / overall['n'])}% ({overall['k']:,} of {overall['n']:,}).", value=pct(overall['k'] / overall['n']),
                          numerator=overall['k'], denominator=overall['n'], unit='%'))
        if params['reason']:
            reasons = ctx.rows(f"SELECT {spec['reason']} AS code, COUNT(*) AS n FROM {t} c WHERE {base} AND {spec['numerator']} AND {spec['reason']} IS NOT NULL GROUP BY code ORDER BY n DESC, code LIMIT 5")
            if reasons:
                facts.append(fact('reason', f"The most common {spec['reason_label']} in {y} is {reasons[0]['code']} ({reasons[0]['n']:,} {spec['noun']}); next: "
                                  + ', '.join(f"{r['code']} ({r['n']:,})" for r in reasons[1:4]) + '.', value=reasons[0]['code'], count=reasons[0]['n']))
                inside = ctx.rows(f"SELECT {spec['reason']} AS code, COUNT(*) AS n FROM {t} c WHERE {base} AND {spec['numerator']} AND {column} = {q(top['grp'])} AND {spec['reason']} IS NOT NULL GROUP BY code ORDER BY n DESC, code LIMIT 1")
                if inside: facts.append(fact('reason_top', f"At {dim_label} {top['grp']} the most common {spec['reason_label']} is {inside[0]['code']} ({inside[0]['n']:,}).", value=inside[0]['code']))
        notes.append(f'Groups are ranked only above {MIN_OUTCOME_ROWS} {spec["noun"]} in the year.' + (' The rate is rejected over paid and rejected claims (reversals and recoupments are not claims the pharmacy decided).' if params['outcome'] == 'reject'
                                                                                                           else ' Claims are the counted payer-adjudicated claims by service date; denied includes upheld denials.'))
        return dict(headline=f"{dim_label.capitalize()} {top['grp']} has the {word} {spec['rate_label']} in {y} ({pct(top['rate'])}%)", facts=facts,
                    table=[{dim_label: r['grp'], 'rows': r['n'], 'outcome_rows': r['k'], 'rate_percent': pct(r['rate'])} for r in rows],
                    read_back=f"{cap(spec['rate_label'])} by {dim_label} for {y} on {spec['noun']} (groups with at least {MIN_OUTCOME_ROWS}); the {word} group is named" + (f" with the usual {spec['reason_label']}." if params['reason'] else '.'),
                    notes=notes)


# ============================================================================================================================
# do patients with a social need miss more appointments than everyone else
# ============================================================================================================================
@family
class NeedGap:
    NAME = 'need_gap'
    WORDS = set('''do does patients with a an actually miss more visits appointments than everyone else others'''.split())
    NEEDS = [(r'transportation\s+(?:barriers?|needs?|insecurity|problems?)|transport\s+(?:barriers?|needs?)', 'transport'), (r'food\s+(?:insecurity|needs?)', 'food'),
             (r'housing\s+(?:instability|needs?|insecurity)', 'housing'), (r'utilit(?:y|ies)\s+needs?', 'utilities'), (r'safety\s+(?:needs?|concerns?)', 'safety')]

    @classmethod
    def parse(cls, text, catalog):
        t = text.strip().rstrip('?.')
        m = re.match(r"^(?:do|does)\s+patients\s+with\s+(?:an?\s+)?(?P<need>.+?)\s+(?:actually\s+)?(?P<verb>miss|no[- ]show)\s+(?:more\s+)?(?P<noun>visits|appointments|visits\s+or\s+appointments)?\s*(?:more\s+)?than\s+(?:everyone|everybody|all\s+(?:the\s+)?others?|the\s+rest|others|the\s+other\s+patients)(?:\s+else)?$", t, re.I)
        if not m: return None
        need = next(((p, k) for p, k in cls.NEEDS if re.fullmatch(p, m['need'].strip(), re.I)), None)
        if not need: return None
        return {'params': {'need': need[1]}, 'quotes': [m.group()]}

    @classmethod
    def run(cls, ctx, params):
        from .insight_families import noshow_rate_sql
        need = params['need']
        window = ctx.in_window('a.scheduled_date')
        pos = ctx.rows(noshow_rate_sql(f"{window} AND {ctx.living} AND {ctx.social_need(need)}"))[0]
        neg = ctx.rows(noshow_rate_sql(f"{window} AND {ctx.living} AND NOT {ctx.social_need(need)}"))[0]
        if not pos['resolved'] or not neg['resolved']: return dict(headline='No appointments to compare', facts=[fact('none', 'One of the two groups has no appointments with a known outcome.')], read_back='', notes=[])
        a, b = pos['missed'] / pos['resolved'], neg['missed'] / neg['resolved']
        facts = [fact('answer', f"{'Yes' if a > b else 'No'}: patients who screened positive for {need} no-show more often." if a > b else f"No: patients who screened positive for {need} do not no-show more often.", value=a > b),
                 fact('positive', f"No-show rate for patients who screened positive for {need}: {pct(a)}% ({pos['missed']} of {pos['resolved']} appointments) = {a:.4f}.", value=pct(a), numerator=pos['missed'], denominator=pos['resolved'], unit='%'),
                 fact('others', f"No-show rate for everyone else: {pct(b)}% ({neg['missed']} of {neg['resolved']} appointments) = {b:.4f}.", value=pct(b), numerator=neg['missed'], denominator=neg['resolved'], unit='%')]
        return dict(headline=f"{'Yes' if a > b else 'No'}: no-show rate {pct(a)}% for patients with a {need} need vs {pct(b)}% for everyone else", facts=facts,
                    table=[{'group': f'screened positive for {need}', 'appointments': pos['resolved'], 'no_shows': pos['missed'], 'no_show_rate_percent': pct(a)},
                           {'group': 'everyone else', 'appointments': neg['resolved'], 'no_shows': neg['missed'], 'no_show_rate_percent': pct(b)}],
                    read_back=f"No-show rate on appointments of the last 12 months with a known outcome, for living patients who ever screened positive for {need} versus all other living patients.",
                    notes=[f'Appointments scheduled in the 12 months to {ctx.as_of} whose outcome is known (no-show, fulfilled, attended or arrived); the rate is no-shows over those.'])


# ============================================================================================================================
# which practice / payer type is worst (and best) on a governed quality measure
# ============================================================================================================================
@family
class MeasureByGroup:
    NAME = 'measure_by_group'
    WORDS = set('''which has have the worst lowest best highest rate and is practice practices payer in for'''.split())
    MIN_DENOMINATOR = 15  # a group's rate is ranked only above this many eligible patients (the same floor the practice-gap family uses)
    DIMS = {'practice': 'practice', 'practices': 'practice', 'payer type': 'payer', 'payer': 'payer', 'coverage type': 'payer'}
    SUP_LOW = ('worst', 'lowest', 'weakest', 'poorest', 'least')

    @classmethod
    def parse(cls, text, catalog):
        t = text.strip().rstrip('?.')
        m = re.match(r"^which\s+(?P<dim>practices?|payer\s+types?|payers?|coverage\s+types?)\s+(?:has|have|had)\s+the\s+(?P<sup>worst|lowest|weakest|poorest|best|highest|strongest)\s+(?P<rest>.+)$", t, re.I)
        if not m: return None
        from . import request_vocabulary as V
        aliases = {k: v for k, v in V.aliases().items() if v.startswith('measure.')}
        rest = m['rest']
        hit = None
        for phrase in sorted(aliases, key=len, reverse=True):
            mm = re.match(re.escape(phrase) + r'(?![\w-])', rest, re.I)
            if mm: hit = (mm.group(), aliases[phrase], rest[mm.end():]); break
        if not hit: return None
        definition = V.definitions().get(hit[1])
        if not definition or definition.get('unit') != 'percent' or not definition.get('measure_definition'): return None
        tail = hit[2]
        kind = re.match(r'^\s*' + V.MEASURE_KIND_WORDS + r'\b', tail, re.I)
        extra = [kind.group().strip()] if kind else []
        if kind: tail = tail[kind.end():]
        both = re.search(r',?\s*and\s+which\s+(?:one\s+)?is\s+(?:the\s+)?(best|worst|highest|lowest)\s*$', tail, re.I)
        if both: extra.append(both.group().strip(' ,')); tail = tail[:both.start()]
        gloss = re.match(r'^\s*(?:for|among|in)\s+(?P<g>.+?)\s*(?:in\s+(?P<year>20\d\d))?\s*$', tail, re.I)
        year = None
        if tail.strip():
            if not gloss: return None
            unaccounted = V.gloss_unaccounted(re.sub(r'\byear\s+olds?\b|\bolds?\b|\byears?\b|\badults?\b|\bpatients?\b', ' ', gloss['g'], flags=re.I), definition)
            if unaccounted: return None
            extra.append(gloss['g'].strip())
            year = gloss['year']
            if year: extra.append(f'in {year}')
        return {'params': {'dim': cls.DIMS[re.sub(r'\s+', ' ', m['dim'].lower())], 'ident': hit[1], 'sup': 'low' if m['sup'].lower() in cls.SUP_LOW else 'high',
                           'both': bool(both), 'year': int(year) if year else None, 'restated': [x for x in extra if re.search(r'\d', x)]},  # numbers the measure itself states (its age band)
                'quotes': [m.group()[:m.start('rest')], hit[0], *extra]}

    @classmethod
    def run(cls, ctx, params):
        import sqlite3
        from . import request_vocabulary as V, measures
        y = params['year'] or last_full_year(ctx)
        notes = [] if params['year'] else [FULL_YEAR_NOTE.format(y=y, a=ctx.as_of[:4])]
        definition = V.definitions()[params['ident']]
        mid = definition['engine']['measures']
        with sqlite3.connect(f'file:{ctx.target}?mode=ro', uri=True) as db:
            db.row_factory = sqlite3.Row
            report = measures.evaluate_db(db, measures.library()[mid], f'{y}-01-01', f'{y}-12-31', ctx.as_of, group_by=params['dim'])
        if report.get('declined'): raise Declined('; '.join(report.get('missing_inputs') or ['measure inputs absent']))
        rows = [r for r in report.get('rows', []) if (r.get('denominator') or 0) >= cls.MIN_DENOMINATOR and r.get('rate') is not None]
        if not rows: return dict(headline='No group is large enough', facts=[fact('none', f'No group has at least {cls.MIN_DENOMINATOR} eligible patients.')], read_back='', notes=notes)
        label = {'practice': 'practice', 'payer': 'payer type'}[params['dim']]
        better_high = definition['measure_definition'].get('direction', 'higher_is_better') == 'higher_is_better'
        ordered = sorted(rows, key=lambda r: (r['rate'], str(r['group'])))  # lowest rate first
        worst, best = (ordered[0], ordered[-1]) if better_high else (ordered[-1], ordered[0])
        name = definition.get('name')
        facts = [fact('worst', f"{worst['group']} has the {'lowest' if better_high else 'highest'} {name} rate in {y}: {pct(worst['rate'])}% ({worst['numerator']} of {worst['denominator']} eligible patients) = {worst['rate']:.4f}.",
                      value=worst['group'], numerator=worst['numerator'], denominator=worst['denominator']),
                 fact('best', f"{best['group']} has the {'highest' if better_high else 'lowest'} rate: {pct(best['rate'])}% ({best['numerator']} of {best['denominator']}) = {best['rate']:.4f}.",
                      value=best['group'], numerator=best['numerator'], denominator=best['denominator'])]
        ov = report.get('overall') or {}
        if ov.get('rate') is not None:
            facts.append(fact('overall', f"Across all {label}s: {pct(ov['rate'])}% ({ov['numerator']} of {ov['denominator']}).", value=pct(ov['rate']), numerator=ov['numerator'], denominator=ov['denominator'], unit='%'))
        first = worst if params['sup'] == 'low' else best
        return dict(headline=f"{first['group']} is {'lowest' if (params['sup'] == 'low') == better_high else 'highest'} on {name} in {y} ({pct(first['rate'])}%); worst {worst['group']} ({pct(worst['rate'])}%), best {best['group']} ({pct(best['rate'])}%)",
                    facts=facts, table=[{label: r['group'], 'eligible': r['denominator'], 'numerator': r['numerator'], 'rate_percent': pct(r['rate'])} for r in ordered],
                    read_back=f"{name} for {y} by {label} (groups with at least {cls.MIN_DENOMINATOR} eligible patients), from the governed measure definition; the worst and best groups are named.",
                    notes=notes + [str(definition['measure_definition'].get('certification') or '')[:300]])


# ============================================================================================================================
# how much of the spend is one category of claims (inpatient, ED, out of network ...), and what the average such claim costs
# ============================================================================================================================
@family
class SpendMix:
    NAME = 'spend_mix'
    WORDS = set('''how much of our medical spend is and what does an average claim run us the share percent'''.split())
    CATEGORIES = [  # phrase -> (label, SQL over alias c)
        (r'(?:hospital\s+)?inpatient(?:\s+(?:stays?|admissions?))?', ('inpatient', 'c.admission_date IS NOT NULL')),
        (r'(?:the\s+)?(?:ed|er|emergency(?:\s+(?:department|room))?)', ('emergency room', "c.place_of_service = '23'")),
        (r'out[- ]of[- ]network', ('out of network', "c.network_status = 'out_of_network'")),
        (r'professional(?:\s+claims?)?', ('professional', "c.claim_type = 'professional'")),
        (r'institutional(?:\s+claims?)?|hospital(?:\s+\(institutional\))?', ('institutional', "c.claim_type = 'institutional'")),
        (r'urgent\s+care', ('urgent care', "c.place_of_service = '20'")),
    ]

    @classmethod
    def parse(cls, text, catalog):
        t = text.strip().rstrip('?.')
        m = re.match(r"^(?:how\s+much|what\s+(?:share|percent(?:age)?|proportion|fraction))\s+of\s+(?:our|the)\s+(?:(?P<y1>20\d\d)\s+)?medical\s+(?:spend|spending|dollars|costs?)\s+"
                     r"(?:is|are|was|were|goes?\s+to|went\s+to|comes?\s+from)\s+(?:for\s+)?(?P<cat>.+?)(?:\s+in\s+(?P<y2>20\d\d))?(?P<avg>\s*,?\s+and\s+what\s+(?:does|is)\s+(?:an?|the)\s+average\s+(?P<cat2>.+?)\s+(?:claim\s+)?(?:run|cost|come\s+to)(?:\s+us)?)?$", t, re.I)
        if not m: return None
        found = next(((p, v) for p, v in cls.CATEGORIES if re.fullmatch(p, m['cat'].strip(), re.I)), None)
        if not found: return None
        if m['avg']:
            again = next((v for p, v in cls.CATEGORIES if re.fullmatch(p, m['cat2'].strip(), re.I)), None)
            if again != found[1]: return None  # the average is asked about the same category
        year = m['y1'] or m['y2']
        return {'params': {'label': found[1][0], 'condition': found[1][1], 'year': int(year) if year else None, 'average': bool(m['avg'])}, 'quotes': [m.group()]}

    @classmethod
    def run(cls, ctx, params):
        y = params['year'] or last_full_year(ctx)
        notes = [] if params['year'] else [FULL_YEAR_NOTE.format(y=y, a=ctx.as_of[:4])]
        base = f"c.counts_in_metrics = 1 AND substr(c.service_from, 1, 4) = {q(str(y))}"
        r = ctx.rows(f"SELECT COALESCE(SUM(c.paid_cents), 0) AS total, COALESCE(SUM(CASE WHEN {params['condition']} THEN c.paid_cents END), 0) AS part, "
                     f"SUM(CASE WHEN {params['condition']} THEN 1 ELSE 0 END) AS n, AVG(CASE WHEN {params['condition']} AND c.final_status = 'paid' THEN c.paid_cents END) AS avg_paid, "
                     f"SUM(CASE WHEN {params['condition']} AND c.final_status = 'paid' THEN 1 ELSE 0 END) AS paid_n FROM payer_claim c WHERE {base}")[0]
        if not r['total']: return dict(headline=f'No counted claims in {y}', facts=[fact('none', f'There are no counted medical claims in {y}.')], read_back='', notes=notes)
        share = r['part'] / r['total']
        label = params['label']
        facts = [fact('share', f"{cap(label)} claims are {pct(share)}% of {y} paid dollars ({usd(r['part'] / 100)} of {usd(r['total'] / 100)}) = {share:.4f}.", value=pct(share),
                      numerator=r['part'] / 100, denominator=r['total'] / 100, unit='%')]
        head = f"{cap(label)} claims are {pct(share)}% of {y} medical paid spend"
        if params['average'] and r['avg_paid'] is not None:
            facts.append(fact('average', f"The average paid {label} claim is {usd(r['avg_paid'] / 100)} ({r['paid_n']:,} paid {label} claims).", value=round(r['avg_paid'] / 100, 2), unit='USD', claims=r['paid_n']))
            head += f"; the average paid {label} claim is {usd(r['avg_paid'] / 100)}"
        notes.append('Medical spend is paid dollars on counted payer-adjudicated claims by service date; a claim is inpatient when it carries an admission date; the average is over claims whose final status is paid.')
        return dict(headline=head, facts=facts, table=[{'category': label, 'claims': r['n'], 'paid_dollars': round(r['part'] / 100, 2), 'total_paid_dollars': round(r['total'] / 100, 2), 'share_percent': pct(share)}],
                    read_back=f"Share of {y} counted medical paid dollars on {label} claims" + (f" and the average paid {label} claim." if params['average'] else '.'), notes=notes)


# ============================================================================================================================
# what the N most expensive members cost in total
# ============================================================================================================================
NUMBER_WORDS_LONG = {w: i for i, w in enumerate('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty'.split())}


@family
class TopSpenders:
    NAME = 'top_spenders'
    WORDS = set('''what did do does our the most expensive members patients cost us in total for how much top'''.split())

    @classmethod
    def parse(cls, text, catalog):
        t = text.strip().rstrip('?.')
        num = r'(?P<n>\d+|' + '|'.join(NUMBER_WORDS_LONG) + r')'
        m = re.match(r"^what\s+(?:did|do|does|have|has)\s+(?:our|the)\s+" + num + r"\s+most\s+(?:expensive|costly)\s+(?:members|patients)\s+cost(?:\s+(?:us|the\s+plan))?(?:\s+in\s+total)?"
                     r"(?:\s+(?:in|for)\s+(?P<year>20\d\d))?$", t, re.I) or \
            re.match(r"^how\s+much\s+(?:did|do|does|have|has)\s+(?:our|the)\s+(?:top\s+)?" + num + r"\s+(?:most\s+(?:expensive|costly)\s+)?(?:members|patients)\s+cost(?:\s+(?:us|the\s+plan))?(?:\s+in\s+total)?"
                     r"(?:\s+(?:in|for)\s+(?P<year>20\d\d))?$", t, re.I)
        if not m: return None
        n = int(m['n']) if m['n'].isdigit() else NUMBER_WORDS_LONG[m['n'].lower()]
        if not 1 <= n <= 1000: return None
        return {'params': {'n': n, 'year': int(m['year']) if m['year'] else None}, 'quotes': [m.group()]}

    @classmethod
    def run(cls, ctx, params):
        y = params['year'] or last_full_year(ctx)
        notes = [] if params['year'] else [FULL_YEAR_NOTE.format(y=y, a=ctx.as_of[:4])]
        n = params['n']
        rows = ctx.rows(f"SELECT c.patient_id AS patient_id, SUM(c.paid_cents) AS cents FROM payer_claim c WHERE c.counts_in_metrics = 1 AND substr(c.service_from, 1, 4) = {q(str(y))} "
                        f"GROUP BY c.patient_id ORDER BY cents DESC, c.patient_id")
        if not rows: return dict(headline=f'No counted claims in {y}', facts=[fact('none', f'There are no counted claims in {y}.')], read_back='', notes=notes)
        total = sum(r['cents'] for r in rows)
        top = rows[:n]
        part = sum(r['cents'] for r in top)
        facts = [fact('total', f"The {len(top)} most expensive members cost {usd(part / 100)} in total in {y} (counted medical paid dollars), {pct(part / total)}% of all {y} paid spend ({usd(total / 100)}) = {part / total:.4f}.",
                      value=round(part / 100, 2), numerator=round(part / 100, 2), denominator=round(total / 100, 2), unit='USD'),
                 fact('range', f"Their individual totals run from {usd(top[-1]['cents'] / 100)} to {usd(top[0]['cents'] / 100)}.", value=[round(top[-1]['cents'] / 100, 2), round(top[0]['cents'] / 100, 2)])]
        return dict(headline=f"{usd(part / 100)} paid in {y} by the {len(top)} most expensive members ({pct(part / total)}% of paid spend)", facts=facts, value=round(part / 100, 2), answer_type='value',
                    table=[{'rank': i, 'patient_id': r['patient_id'], 'paid_dollars': round(r['cents'] / 100, 2)} for i, r in enumerate(top, 1)],
                    read_back=f"Counted medical paid dollars in {y} of the {len(top)} members with the highest total, and their share of all paid spend.",
                    notes=notes + ['Members are ranked by their summed paid dollars on counted payer-adjudicated claims by service date in the year (the deceased are not excluded from a spend ranking of a closed year).'])


# ============================================================================================================================
# do <a group of patients> use the ED / miss visits / get admitted / cost more than everyone else
# ============================================================================================================================
GROUP_MEASURES = [  # phrase -> key
    (r'miss\s+(?:more\s+)?(?:visits|appointments)(?:\s+more)?|no[- ]show\s+more(?:\s+often)?', 'no_show'),
    (r'(?:use|visit|go\s+to|come\s+(?:in)?to|show\s+up\s+(?:in|at)|end\s+up\s+in)\s+the\s+(?:ed|er|emergency\s+(?:department|room))\s+more(?:\s+often)?|have\s+more\s+(?:ed|er|emergency(?:\s+department)?)\s+visits', 'ed'),
    (r'get\s+admitted\s+more(?:\s+often)?|have\s+more\s+(?:hospital\s+)?admissions|get\s+hospitali[sz]ed\s+more(?:\s+often)?', 'admissions'),
    (r'cost\s+(?:us\s+)?more', 'cost'),
]
REST_WORDS = r'(?:everyone|everybody|all\s+(?:the\s+)?others?|the\s+rest|others|the\s+other\s+patients|other\s+patients)(?:\s+else)?'


def cohort_ids(ctx, parser, where, quote):
    """The living patients a population predicate selects, read by the same compiler and decision context as a typed request."""
    from . import sql_query
    from .request_plan import Catalog, compile_request
    catalog = Catalog.load(ctx.stack_path)
    noun = 'patients' if re.search(r'\bpatients\b', quote) else re.match(r'\S+', quote).group()  # a literal word of the group's own wording
    plan = {'source': {'table': 'patient', 'quote': noun},
            'select': [{'kind': 'field', 'field': 'patient.patient_id', 'alias': 'patient_id', 'quote': noun}],
            'where': where, 'group_by': [], 'order_by': [], 'limit': None, 'unresolved': []}
    parser.bind_parent_quotes(where)
    contract = compile_request(catalog, quote, plan)
    return sql_query.execute(ctx.stack_path, {'query': contract['query'], 'returns': 'patient_set'}, target=catalog.target)['patient_ids']


@family
class GroupVsRest:
    NAME = 'group_vs_rest'
    WORDS = set('''do does patients who with a an actually than everyone else others the rest more often'''.split())

    @classmethod
    def parse(cls, text, catalog):
        t = text.strip().rstrip('?.')
        measures = '|'.join(p for p, _ in GROUP_MEASURES)
        m = re.match(r"^(?:do|does|are|is)\s+(?P<group>.+?)\s+(?:actually\s+|really\s+)?(?P<verb>" + measures + r")\s*(?:visits|appointments)?\s*than\s+(?P<rest>" + REST_WORDS + r")$", t, re.I)
        if not m: return None
        key = next((k for p, k in GROUP_MEASURES if re.fullmatch(p, m['verb'].strip(), re.I)), None)
        if not key: return None
        from .request_language import Parser, PlanError
        parser = Parser(catalog, t)
        group = m['group'].strip()
        try:
            where = parser.population_phrase(group)
        except PlanError:
            return None
        if not where: return None
        return {'params': {'measure': key, 'group': group}, 'quotes': [m.group()]}

    @classmethod
    def run(cls, ctx, params):
        from .insight_families import noshow_rate_sql
        parser = Parser_for(ctx, params['group'])
        where = parser.population_phrase(params['group'])
        ids = set(cohort_ids(ctx, parser, where, params['group']))
        if not ids: raise Declined('the group is empty')
        marks = ','.join(q(i) for i in sorted(ids))
        inside, outside = f"p.patient_id IN ({marks})", f"p.patient_id NOT IN ({marks})"
        key, group = params['measure'], params['group']
        facts, table = [], []
        if key == 'no_show':
            window = ctx.in_window('a.scheduled_date')
            a = ctx.rows(noshow_rate_sql(f"{window} AND {ctx.living} AND {inside}"))[0]
            b = ctx.rows(noshow_rate_sql(f"{window} AND {ctx.living} AND {outside}"))[0]
            va, vb = (a['missed'] / a['resolved'] if a['resolved'] else None), (b['missed'] / b['resolved'] if b['resolved'] else None)
            if va is None or vb is None: raise Declined('no appointments with a known outcome')
            label, unit = 'no-show rate', '%'
            texts = (f"{pct(va)}% ({a['missed']} of {a['resolved']} appointments)", f"{pct(vb)}% ({b['missed']} of {b['resolved']} appointments)")
            va_, vb_ = va, vb
            note = f'Appointments scheduled in the 12 months to {ctx.as_of} with a known outcome (no-show, fulfilled, attended or arrived); the rate is no-shows over those.'
        elif key in ('ed', 'admissions'):
            kind, noun = ('ed_visit', 'ED visits') if key == 'ed' else ('inpatient', 'admissions')
            def rate(cond):
                r = ctx.rows(f"SELECT COUNT(*) AS n, COALESCE(SUM((SELECT COUNT(*) FROM utilization_event u WHERE u.patient_id = p.patient_id AND u.kind = {q(kind)} AND {ctx.in_window('u.event_date')})), 0) AS k "
                             f"FROM patient p WHERE {ctx.living} AND {cond}")[0]
                return r
            a, b = rate(inside), rate(outside)
            if not a['n'] or not b['n']: raise Declined('a group is empty')
            va_, vb_ = 1000.0 * a['k'] / a['n'], 1000.0 * b['k'] / b['n']
            label, unit = f'{noun} per 1,000 patients', ''
            texts = (f"{va_:,.0f} ({a['k']:,} {noun} among {a['n']:,} patients)", f"{vb_:,.0f} ({b['k']:,} {noun} among {b['n']:,} patients)")
            note = f'{cap(noun)} are the all-source utilization_event rows in the 12 months ending {ctx.as_of}, per living patient.'
        else:
            def avg(cond):
                return ctx.rows(f"SELECT COUNT(*) AS n, COALESCE(SUM((SELECT SUM(c.paid_cents) FROM payer_claim c WHERE c.patient_id = p.patient_id AND c.counts_in_metrics = 1 AND {ctx.in_window('c.service_from')})), 0) / 100.0 AS dollars "
                                f"FROM patient p WHERE {ctx.living} AND {cond}")[0]
            a, b = avg(inside), avg(outside)
            va_, vb_ = a['dollars'] / a['n'], b['dollars'] / b['n']
            label, unit = 'medical paid per patient', ''
            texts = (f"{usd(va_)} ({usd(a['dollars'])} over {a['n']:,} patients)", f"{usd(vb_)} ({usd(b['dollars'])} over {b['n']:,} patients)")
            note = f'Counted medical paid dollars in the 12 months ending {ctx.as_of}, per living patient (patients without claims count as $0).'
        more = va_ > vb_
        facts = [fact('answer', f"{'Yes' if more else 'No'}: {group} have {'a higher' if more else 'no higher'} {label} than everyone else.", value=more),
                 fact('group', f"{cap(group)}: {label} {texts[0]}.", value=round(va_ * (100 if unit == '%' else 1), 2)),
                 fact('rest', f"Everyone else: {label} {texts[1]}.", value=round(vb_ * (100 if unit == '%' else 1), 2))]
        return dict(headline=f"{'Yes' if more else 'No'}: {label} {texts[0].split(' (')[0]} for {group} vs {texts[1].split(' (')[0]} for everyone else", facts=facts,
                    table=[{'group': group, label: texts[0]}, {'group': 'everyone else', label: texts[1]}],
                    read_back=f"{cap(label)} for living patients in the group ({group}; {len(ids):,} patients) versus all other living patients, last 12 months.", notes=[note])


# ============================================================================================================================
# which chronic conditions account for the most of our ED visits / admissions / spend
# ============================================================================================================================
@family
class ConditionDrivers:
    NAME = 'condition_drivers'
    WORDS = set('''which chronic conditions account for the most of our in the last year'''.split())  # every canonical chronic condition is a candidate
    WHAT = [(r'ed\s+visits|emergency(?:\s+department)?\s+visits', 'ed_visit', 'ED visits'), (r'(?:hospital\s+)?admissions|inpatient\s+stays', 'inpatient', 'admissions'),
            (r'medical\s+spend(?:ing)?|medical\s+costs?|paid\s+spend', 'spend', 'medical paid dollars')]

    @classmethod
    def parse(cls, text, catalog):
        t = text.strip().rstrip('?.')
        m = re.match(r"^which\s+(?:(?:chronic|major)\s+)?(?:conditions|diagnoses|diseases)\s+(?:account\s+for|drive|are\s+driving|generate|are\s+behind)\s+(?:the\s+)?(?:most|largest\s+share)"
                     r"(?:\s+of)?\s+(?:our\s+)?(?P<what>.+?)(?:\s+(?:in|over)\s+the\s+(?P<rolling>(?:last|past)\s+(?:year|12\s+months))|\s+in\s+(?P<year>20\d\d))?$", t, re.I)
        if not m: return None
        what = next(((k, label) for p, k, label in cls.WHAT if re.fullmatch(p, m['what'].strip(), re.I)), None)
        if not what: return None
        return {'params': {'kind': what[0], 'label': what[1], 'year': int(m['year']) if m['year'] else None, 'window': 'rolling_12_months' if (m['rolling'] or not m['year']) else None},
                'quotes': [m.group()]}

    @classmethod
    def run(cls, ctx, params):
        kind, label, year = params['kind'], params['label'], params['year']
        from . import concepts
        # every canonical chronic condition, once: the subtype concepts (type 1 / type 2 diabetes, reduced-EF heart failure, prediabetes) are inside their parents, sepsis is acute
        names = [c for c in concepts.CONDITIONS if c not in ('type_2_diabetes', 'type_1_diabetes', 'prediabetes', 'heart_failure_reduced_ef', 'sepsis', 'myocardial_infarction')]
        label_of = lambda n: CONDITION_LABELS.get(n, n.replace('_', ' '))
        if kind == 'spend':
            window = f"substr(c.service_from, 1, 4) = {q(str(year))}" if year else ctx.in_window('c.service_from')
            base = f"FROM payer_claim c JOIN patient p ON p.patient_id = c.patient_id WHERE c.counts_in_metrics = 1 AND {window} AND {ctx.living}"
            total = (ctx.one(f"SELECT COALESCE(SUM(c.paid_cents), 0) {base}", 0) or 0) / 100.0
            counts = {n: (ctx.one(f"SELECT COALESCE(SUM(c.paid_cents), 0) {base} AND {ctx.cond(n)}", 0) or 0) / 100.0 for n in names}
            fmt = usd
        else:
            window = f"substr(u.event_date, 1, 4) = {q(str(year))}" if year else ctx.in_window('u.event_date')
            base = f"FROM utilization_event u JOIN patient p ON p.patient_id = u.patient_id WHERE u.kind = {q(kind)} AND {window} AND {ctx.living}"
            total = ctx.one(f"SELECT COUNT(*) {base}", 0)
            counts = {n: ctx.one(f"SELECT COUNT(*) {base} AND {ctx.cond(n)}", 0) for n in names}
            fmt = lambda v: f'{v:,.0f}'
        if not total: return dict(headline=f'No {label}', facts=[fact('none', f'There are no {label} in the period.')], read_back='', notes=[])
        period = f'in {year}' if year else 'in the last 12 months'
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        facts = [fact('top', f"{label_of(ranked[0][0])} patients account for the most {label} {period}: {pct(ranked[0][1] / total)}% ({fmt(ranked[0][1])} of {fmt(total)}).", value=label_of(ranked[0][0])),
                 fact('rank', f"{cap(label)} {period} by condition cohort: " + ', '.join(f"{label_of(n)} ({fmt(v)})" for n, v in ranked[:3]) + '.', value=[label_of(n) for n, _ in ranked[:3]])]
        for i, (n, v) in enumerate(ranked[:10]):
            facts.append(fact(f'c_{n}', f"{label_of(n)} patients: {pct(v / total)}% of {label} {period} ({fmt(v)} of {fmt(total)}) = {v / total:.4f}.", value=pct(v / total), numerator=v, denominator=total, unit='%'))
        return dict(headline=f"{label_of(ranked[0][0])} patients account for the most {label} {period} ({pct(ranked[0][1] / total)}%)", facts=facts,
                    table=[{'condition': label_of(n), label.replace(' ', '_'): round(v, 2), 'share_percent': pct(v / total)} for n, v in ranked],
                    read_back=f"Share of {label} {period} that belongs to living patients with each chronic condition (a patient with several conditions counts in each).",
                    notes=['The conditions use the canonical condition evidence; the shares overlap because a patient can have several conditions.'])


# ============================================================================================================================
# is readmission a bigger problem for one payer segment than another
# ============================================================================================================================
@family
class SegmentReadmission:
    NAME = 'segment_readmission'
    WORDS = set('''is a bigger problem for our patients than'''.split())

    @classmethod
    def parse(cls, text, catalog):
        t = text.strip().rstrip('?.')
        m = re.match(r"^is\s+(?:the\s+)?(?:(?P<day>30)[- ]day\s+)?(?P<m>readmissions?(?:\s+rate)?)\s+a\s+bigger\s+problem\s+for\s+(?:our\s+)?(?P<a>.+?)\s+(?:patients\s+)?than\s+(?:for\s+)?(?:our\s+)?(?P<b>.+?)(?:\s+patients)?(?:\s+in\s+(?P<year>20\d\d))?$", t, re.I)
        if not m: return None
        from .insight_families import segment_in
        sa, sb = segment_in(m['a']), segment_in(m['b'])
        if not sa or not sb or sa[1] == sb[1]: return None
        return {'params': {'a': [sa[1], sa[2], sa[3]], 'b': [sb[1], sb[2], sb[3]], 'year': int(m['year']) if m['year'] else None, 'window_days': 30},
                'quotes': [m.group(), sa[0], sb[0]]}

    @classmethod
    def run(cls, ctx, params):
        y = params['year'] or last_full_year(ctx)
        notes = [] if params['year'] else [FULL_YEAR_NOTE.format(y=y, a=ctx.as_of[:4])]
        out = []
        for label, column, values in (params['a'], params['b']):
            cond = f"p.{ {'line_of_business': 'line_of_business', 'product': 'line_of_business'}[column] } IN ({','.join(q(v) for v in sorted({ {'medicare_advantage': 'medicare', 'dual_ma': 'dual'}.get(x, x) for x in values }))})"
            r = ctx.rows(f"SELECT COUNT(*) AS n, COALESCE(SUM(u.readmitted_30d), 0) AS k FROM utilization_event u JOIN patient p ON p.patient_id = u.patient_id WHERE u.kind = 'inpatient' "
                         f"AND substr(u.event_date, 1, 4) = {q(str(y))} AND {ctx.living} AND {cond}")[0]
            out.append((label, r))
        if not all(r['n'] for _, r in out): raise Declined('a segment has no admissions')
        (la, ra), (lb, rb) = out
        va, vb = ra['k'] / ra['n'], rb['k'] / rb['n']
        bigger = va > vb
        facts = [fact('answer', f"{'Yes' if bigger else 'No'}: 30-day readmission is {'a bigger' if bigger else 'not a bigger'} problem for {la} than for {lb} in {y} ({pct(va)}% versus {pct(vb)}%).", value=bigger),
                 fact('a', f"{la}: {pct(va)}% of {y} admissions were readmitted within 30 days ({ra['k']} of {ra['n']}) = {va:.4f}.", value=pct(va), numerator=ra['k'], denominator=ra['n'], unit='%'),
                 fact('b', f"{lb}: {pct(vb)}% ({rb['k']} of {rb['n']}) = {vb:.4f}.", value=pct(vb), numerator=rb['k'], denominator=rb['n'], unit='%')]
        return dict(headline=f"{'Yes' if bigger else 'No'}: {la} {pct(va)}% vs {lb} {pct(vb)}% 30-day readmission in {y}", facts=facts,
                    table=[{'segment': la, 'admissions': ra['n'], 'readmitted': ra['k'], 'rate_percent': pct(va)}, {'segment': lb, 'admissions': rb['n'], 'readmitted': rb['k'], 'rate_percent': pct(vb)}],
                    read_back=f"30-day readmission rate on {y} admissions of living patients, {la} versus {lb}.", notes=notes + ['A segment is the patient\'s line of business; a readmission is a new admission within 30 days of discharge.'])
