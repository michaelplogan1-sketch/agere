"""Insight families for the engine forms (population_engines.py). Each family is the SHAPE of a kind of question, not a particular question:

  practice_gap       which <practices / payers / lines of business> are worst on a measure (control rate, count above a threshold, ED use per 1,000)
  prioritize         who should we call first for <a condition / recent discharge>: the gap populations and how they overlap
  care_gaps          where are the biggest gaps in <a condition's care / preventive care>: each gap's size, the biggest named
  spend_drivers      what is driving PMPM / spend for <a segment>, or why did it change between two years
  spend_concentration  how concentrated is medical spend in the top few percent of patients
  yoy_trend          did <admissions / ED visits / pharmacy spend> go up or down from one year to the next, and what moved it
  denial_drivers     why is the denial rate high with the worst payer: its rate, its mix of denial reasons
  association        is <a social need / booking lead time> tied to missed appointments
  segment_compare    how does <one coverage segment> compare with <another> on <measures>
  frequent_users     who are the frequent ED users and how much of ED volume do they drive

A family parses the wording with vocabularies (conditions, measures, segments, years) and declines anything it cannot account for. It computes
with population_sql.Ctx (decision-context definitions) and states each fact with its numerator and denominator.
"""
import re

from .population_engines import Declined, family, fact
from .population_sql import Ctx, pct, q, usd

NUMBER_WORDS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5}
MIN_PRACTICE_PATIENTS = 15  # a rate over fewer patients than this is not ranked as a practice result (stated in the answer)
FULL_YEAR_NOTE = ('No period was stated: the latest full calendar year ({y}) is used (the as-of year {a} is partial and claims are still running out).')


def last_full_year(ctx):
    y = int(ctx.as_of[:4])
    return y - 1


def years_in(text):
    return [int(y) for y in re.findall(r'\b(20\d\d)\b', text)]


def cap(text):
    return text[:1].upper() + text[1:]


def span(text, pattern):
    m = re.search(pattern, text, re.I)
    return m.group() if m else None


# ============================================================================================================================
# measures ranked across groups
# ============================================================================================================================
def measures(ctx):
    """name -> definition. population / numerator are SQL over alias p (population_sql); worse is the direction of a bad result."""
    return {
        'diabetes_control': dict(
            label='diabetes control (share of adult diabetics with a latest A1c under 8)', noun='adult diabetics with a latest A1c', worse='low',
            population=f"{ctx.cond('diabetes')} AND {ctx.age(18)} AND {ctx.has_lab('hba1c')}", numerator=ctx.lab('hba1c', '<', 8), minimum=MIN_PRACTICE_PATIENTS),
        'poor_control': dict(
            label='adult diabetics with a latest A1c above 9', noun='adult diabetics with a latest A1c', worse='high',
            population=f"{ctx.cond('diabetes')} AND {ctx.age(18)} AND {ctx.has_lab('hba1c')}", numerator=ctx.lab('hba1c', '>', 9), minimum=1, count_ranked=True),
        'bp_control': dict(
            label='blood pressure control (share of hypertensives 18-85 with latest readings under 140/90)', noun='hypertensives 18-85 with a latest blood pressure',
            worse='low', population=f"{ctx.cond('hypertension')} AND {ctx.age(18, 85)} AND {ctx.has_lab('systolic_bp')} AND {ctx.has_lab('diastolic_bp')}",
            numerator=f"{ctx.lab('systolic_bp', '<', 140)} AND {ctx.lab('diastolic_bp', '<', 90)}", minimum=MIN_PRACTICE_PATIENTS),
        'ed_rate': dict(
            label='ED visits per 1,000 patients in the last 12 months', noun='living patients', worse='high', minimum=50, per1000=True),
    }


MEASURE_WORDS = [
    (r'diabetes\s+control(?:\s+gap)?|glycemic\s+control|a1c\s+control', 'diabetes_control'),
    (r'(?:blood\s+pressure|bp|hypertension)\s+control', 'bp_control'),
    (r'(?:diabetic\s+)?patients?\s+(?:above|over)\s+9\b|diabetics?\s+(?:with\s+an?\s+a1c\s+)?(?:above|over)\s+9\b|a1c\s+(?:above|over)\s+9\b|uncontrolled\s+diabetes|poor(?:ly)?\s+controlled', 'poor_control'),
    (r'(?:patients?\s+)?to\s+the\s+(?:ed|er|emergency\s+(?:department|room))(?:\s+relative\s+to\s+(?:their\s+)?size)?|ed\s+(?:use|visits|utilization)(?:\s+relative\s+to\s+size)?', 'ed_rate'),
]
GROUP_WORDS = [(r'practices?|clinics?|sites?', 'practice'), (r'payers?', 'payer'), (r'lines?\s+of\s+business', 'lob')]


@family
class PracticeGap:
    NAME = 'practice_gap'
    WORDS = set('''which what where what's where's is are have has had the a an biggest largest greatest worst weakest lowest highest most across our relative
        size gap control send patients patient with and to'''.split())

    @classmethod
    def parse(cls, text, catalog):
        t = text.strip().rstrip('?.')
        t = re.sub(r'^and\s+', '', t, flags=re.I)
        if not re.match(r'^(?:which|what|where)\b', t, re.I): return None
        group = next(((span(t, p), g) for p, g in GROUP_WORDS if span(t, p)), None)
        measure = next(((span(t, p), m) for p, m in MEASURE_WORDS if span(t, p)), None)
        if not group or not measure: return None
        rank = span(t, r'biggest|largest|greatest|worst|weakest|lowest|highest|most|fewest')
        if not rank: return None
        # worst / biggest / "gap" rank the worse end first; an explicit highest / most or lowest / fewest ranks by the value itself
        direction = 'worst' if re.search(r'gap', measure[0], re.I) or rank.lower() in ('biggest', 'largest', 'greatest', 'worst', 'weakest') else \
            'high' if rank.lower() in ('highest', 'most') else 'low'
        return {'params': {'group': group[1], 'measure': measure[1], 'direction': direction}, 'quotes': [group[0], measure[0], rank]}

    @classmethod
    def run(cls, ctx, params):
        m = measures(ctx)[params['measure']]
        gx = {'practice': ctx.practice, 'lob': ctx.lob, 'payer': 'p.payer_id'}[params['group']]
        gname = {'practice': 'practice', 'lob': 'line of business', 'payer': 'payer'}[params['group']]
        facts, tables = [], {}
        direction = params.get('direction', 'worst')
        order = ('desc' if m['worse'] == 'high' else 'asc') if direction == 'worst' else 'desc' if direction == 'high' else 'asc'
        if m.get('per1000'):
            rows = ctx.rows(
                f"WITH g AS (SELECT p.patient_id, {gx} AS grp FROM patient p WHERE {ctx.living}), "
                f"e AS (SELECT u.patient_id, COUNT(*) AS visits FROM utilization_event u WHERE u.kind = 'ed_visit' AND {ctx.in_window('u.event_date')} GROUP BY u.patient_id) "
                f"SELECT grp, COUNT(*) AS n, COALESCE(SUM(e.visits), 0) AS k, 1000.0 * COALESCE(SUM(e.visits), 0) / COUNT(*) AS rate "
                f"FROM g LEFT JOIN e USING (patient_id) WHERE grp IS NOT NULL GROUP BY grp HAVING COUNT(*) >= {m['minimum']} ORDER BY rate {order.upper()}, grp")
            overall = ctx.rows(
                f"WITH g AS (SELECT p.patient_id FROM patient p WHERE {ctx.living}), "
                f"e AS (SELECT u.patient_id, COUNT(*) AS visits FROM utilization_event u WHERE u.kind = 'ed_visit' AND {ctx.in_window('u.event_date')} GROUP BY u.patient_id) "
                f"SELECT COUNT(*) AS n, COALESCE(SUM(e.visits), 0) AS k FROM g LEFT JOIN e USING (patient_id)")[0]
            top = rows[:3]
            facts.append(fact('rank', f"{gname.capitalize()}s with the {'most' if order == 'desc' else 'fewest'} ED visits per 1,000 patients (last 12 months, {gname}s with at least {m['minimum']} patients), {'highest' if order == 'desc' else 'lowest'} first: "
                              + ', '.join(f"{r['grp']} ({r['rate']:.1f})" for r in top) + '.', value=[r['grp'] for r in top]))
            if top:
                facts.append(fact('top', f"{top[0]['grp']}: {top[0]['rate']:.1f} ED visits per 1,000 patients ({top[0]['k']:,} visits among {top[0]['n']:,} patients).",
                                  value=round(top[0]['rate'], 1), numerator=top[0]['k'], denominator=top[0]['n'], unit='per 1,000'))
            facts.append(fact('overall', f"Across all {gname}s: {1000.0 * overall['k'] / overall['n']:.1f} ED visits per 1,000 patients ({overall['k']:,} visits, {overall['n']:,} patients).",
                              value=round(1000.0 * overall['k'] / overall['n'], 1), numerator=overall['k'], denominator=overall['n'], unit='per 1,000'))
            tables['ranking'] = [{gname: r['grp'], 'patients': r['n'], 'ed_visits': r['k'], 'ed_visits_per_1000': round(r['rate'], 1)} for r in rows]
            headline = (f"{top[0]['grp']} has the {'most' if order == 'desc' else 'fewest'} ED visits per 1,000 patients ({top[0]['rate']:.1f})" if top else 'No group meets the minimum size')
            return dict(headline=headline, facts=facts, table=tables['ranking'], tables=tables,
                        read_back=f"Ranking {gname}s by ED visits per 1,000 living patients, last 12 months to {ctx.as_of}, groups of at least {m['minimum']} patients; "
                                  "living patients; all-source ED visits.", notes=['ED visits are the all-source utilization_event rows (EHR, ADT and claims, de-duplicated).'])
        rows = ctx.rates(gx, m['population'], m['numerator'], minimum=m['minimum'], order=order)
        overall = ctx.overall(m['population'], m['numerator'])
        if m.get('count_ranked'):
            rows = sorted(rows, key=lambda r: (-r['k'] if order == 'desc' else r['k'], str(r['grp'])))
        top = rows[:3]
        word = 'patients' if m.get('count_ranked') else 'share'
        end_word = 'worst' if direction == 'worst' else 'highest' if direction == 'high' else 'lowest'  # which end of the ranking comes first
        if m.get('count_ranked'):
            facts.append(fact('rank', f"{gname.capitalize()}s with the {'most' if order == 'desc' else 'fewest'} {m['label']}, {'most' if order == 'desc' else 'fewest'} first: " + ', '.join(f"{r['grp']} ({r['k']})" for r in top) + '.',
                              value=[r['grp'] for r in top]))
            if top:
                facts.append(fact('top', f"{top[0]['grp']} has the {'most' if order == 'desc' else 'fewest'} {m['label']}: {top[0]['k']} patients ({top[0]['k']} of {top[0]['n']} {m['noun']}).",
                                  value=top[0]['k'], numerator=top[0]['k'], denominator=top[0]['n'], unit='patients'))
            facts.append(fact('overall', f"Across all {gname}s: {overall['k']:,} of {overall['n']:,} {m['noun']} ({pct(overall['rate'])}%).",
                              value=pct(overall['rate']), numerator=overall['k'], denominator=overall['n'], unit='%'))
        else:
            facts.append(fact('rank', f"{gname.capitalize()}s ranked {end_word} first on {m['label']} ({gname}s with at least {m['minimum']} patients): "
                              + ', '.join(f"{r['grp']} ({pct(r['rate'], 0):.0f}%)" for r in top) + '.', value=[r['grp'] for r in top]))
            for i, r in enumerate(top[:3]):
                facts.append(fact(f'w{i + 1}', f"{r['grp']}: {pct(r['rate'])}% ({r['k']} of {r['n']} {m['noun']}) = {r['rate']:.4f}.", value=pct(r['rate']),
                                  numerator=r['k'], denominator=r['n'], unit='%'))
            facts.append(fact('overall', f"Across all {gname}s: {pct(overall['rate'])}% ({overall['k']:,} of {overall['n']:,} {m['noun']}) = {overall['rate']:.4f}.",
                              value=pct(overall['rate']), numerator=overall['k'], denominator=overall['n'], unit='%'))
        tables['ranking'] = [{gname: r['grp'], 'patients': r['n'], 'meeting': r['k'], 'share': round(r['rate'], 4), 'percent': pct(r['rate'])} for r in rows]
        headline = (f"{top[0]['grp']} is {end_word} on {m['label']}: " + (f"{top[0]['k']} patients" if m.get('count_ranked') else f"{pct(top[0]['rate'])}%")) if top else 'No group meets the minimum size'
        return dict(headline=headline, facts=facts, table=tables['ranking'], tables=tables,
                    read_back=f"Ranking {gname}s on {m['label']}; population: {m['noun']} (living, latest results); {gname}s of at least {m['minimum']} patients"
                              + (' (a patient\'s practice is the PCP practice, else the practice attributed on the latest enrollment).' if gname == 'practice' else '.'),
                    notes=['Labs are each patient\'s latest result; conditions use the canonical evidence; adults are 18 and over.'])


# ============================================================================================================================
# who to call first / where are the gaps
# ============================================================================================================================
GDMT = ['hf_evidence_based_beta_blocker', 'ace_inhibitor', 'arb', 'arni', 'mra', 'sglt2_inhibitor']
RAAS = ['ace_inhibitor', 'arb', 'arni']


def conditions_gaps(ctx):
    """condition -> {'label', 'population', 'gaps': [(id, label, SQL numerator)], 'escalators': [(id, label, SQL)]}. A gap is something the
    population lacks; an escalator is recent acute use that makes a gap more urgent. (Data: add a condition here, no new code.)"""
    return {
        'diabetes': dict(label='adult diabetics', population=f"{ctx.cond('diabetes')} AND {ctx.age(18)}",
                         gaps=[('poor', 'a latest A1c above 9', ctx.lab('hba1c', '>', 9)),
                               ('nolab', 'no A1c result in the last 12 months', f"NOT {ctx.lab_in_window('hba1c')}")],
                         escalators=[('ed', 'had an ED visit in the last 12 months', ctx.events('ed_visit'))],
                         tiers=[('poor_ed', ['poor', 'ed'], 'highest priority'), ('poor', ['poor'], 'next'), ('nolab', ['nolab'], 'then')]),
        'heart_failure': dict(label='heart failure patients', population=ctx.cond('heart_failure'),
                              gaps=[('nogdmt', 'no GDMT medication fill (none of the four pillars: evidence-based beta-blocker, ACEI/ARB/ARNI, MRA, SGLT2 inhibitor) in the last 12 months',
                                     f"NOT {ctx.fill(GDMT)}")],
                              escalators=[('adm', 'were admitted to the hospital in the last 12 months', ctx.events('inpatient'))],
                              tiers=[('nogdmt_adm', ['nogdmt', 'adm'], 'call first'), ('nogdmt', ['nogdmt'], 'next')]),
        'hypertension': dict(label='adult hypertensives (18-85)', population=f"{ctx.cond('hypertension')} AND {ctx.age(18, 85)}",
                             gaps=[('uncontrolled', 'a latest blood pressure of 140/90 or higher',
                                    f"({ctx.lab('systolic_bp', '>=', 140)} OR {ctx.lab('diastolic_bp', '>=', 90)})")],
                             escalators=[('ed', 'had an ED visit in the last 12 months', ctx.events('ed_visit'))],
                             tiers=[('uncontrolled_ed', ['uncontrolled', 'ed'], 'highest priority'), ('uncontrolled', ['uncontrolled'], 'next')]),
        'chronic_kidney_disease': dict(label='CKD patients', population=ctx.cond('chronic_kidney_disease'),
                                       gaps=[('nouacr', 'no UACR result in the last 12 months', f"NOT {ctx.lab_in_window('uacr')}"),
                                             ('noraas', 'no ACE inhibitor / ARB / ARNI fill in the last 12 months', f"NOT {ctx.fill(RAAS)}"),
                                             ('nosglt2', 'no SGLT2 inhibitor fill in the last 12 months', f"NOT {ctx.fill(['sglt2_inhibitor'])}")],
                                       escalators=[], tiers=[]),
    }


CONDITION_WORDS = [(r'diabet\w*', 'diabetes'), (r'heart\s+failure|\bhf\b|\bchf\b', 'heart_failure'), (r'hypertensi\w*|blood\s+pressure', 'hypertension'),
                   (r'\bckd\b|chronic\s+kidney\s+disease|kidney', 'chronic_kidney_disease')]


@family
class Prioritize:
    NAME = 'prioritize'
    WORDS = set('''who which what should we the our care team patients patient first right now need a call calling prioritize prioritise outreach
        for to recently'''.split())

    @classmethod
    def parse(cls, text, catalog):
        t = text.strip().rstrip('?.')
        if not re.search(r'\b(?:should|need|needs)\b', t, re.I) or not re.match(r'^(?:who|which)\b', t, re.I): return None
        action = span(t, r'prioriti[sz]e|call(?:\s+first)?|a\s+call|outreach|reach|contact')
        if not action: return None
        discharged = span(t, r'recently\s+discharged')
        if discharged:
            return {'params': {'kind': 'discharged'}, 'quotes': [discharged, action, 'right now', 'now']}
        cond = next(((span(t, p), c) for p, c in CONDITION_WORDS if span(t, p)), None)
        if not cond: return None
        extra = [w for w in (span(t, r'outreach'), span(t, r'care\s+team')) if w]
        return {'params': {'kind': 'condition', 'condition': cond[1], 'ranking': 'tiers: highest priority first'}, 'quotes': [cond[0], action, *extra]}

    @classmethod
    def run(cls, ctx, params):
        if params['kind'] == 'discharged':
            return cls.discharged(ctx)
        spec = conditions_gaps(ctx)[params['condition']]
        pop = spec['population']
        n = ctx.count(pop)
        facts, tables = [fact('population', f"{n:,} {spec['label']}.", value=n, unit='patients')], {}
        masks = {gid: ctx.ids(pop, expr) for gid, _, expr in spec['gaps']}
        esc = {eid: set(ctx.ids(pop, expr)) for eid, _, expr in spec['escalators']}
        for gid, label, _ in spec['gaps']:
            k = len(masks[gid])
            facts.append(fact(gid, f"{k:,} of {n:,} {spec['label']} ({pct(k / n)}%) have {label}.", value=k, numerator=k, denominator=n, unit='patients'))
        listed, rows = [], []
        for tid, parts, why in spec['tiers']:
            members = None
            for part in parts:
                ids = set(masks[part]) if part in masks else esc[part]
                members = ids if members is None else members & ids
            members = sorted(members - set(listed))
            gap_label = next(l for g, l, _ in spec['gaps'] if g in parts)
            esc_label = next((l for g, l, _ in spec['escalators'] if g in parts), None)
            label = gap_label + (f' and also {esc_label}' if esc_label else '')
            rows.append({'tier': len(rows) + 1, 'priority': why, 'criteria': label, 'patients': len(members)})
            if len(parts) > 1:
                facts.append(fact(tid, f"{len(members):,} of the {len(masks[parts[0]]):,} with {gap_label} also {esc_label} ({why}).",
                                  value=len(members), numerator=len(members), denominator=len(masks[parts[0]]), unit='patients'))
            listed += members
        if not spec['tiers']:
            worst = max(spec['gaps'], key=lambda g: len(masks[g[0]]))
            facts.append(fact('biggest', f"The largest gap is {worst[1]}: {len(masks[worst[0]]):,} of {n:,} ({pct(len(masks[worst[0]]) / n)}%).", value=worst[0]))
        tables['tiers'] = rows or [{'gap': l, 'patients': len(masks[g]), 'share': round(len(masks[g]) / n, 4)} for g, l, _ in spec['gaps']]
        head = (f"{rows[0]['patients']:,} {spec['label']} with {rows[0]['criteria']} are the {rows[0]['priority']}" if rows else f"Largest gap: {worst[1]}")
        return dict(headline=head, facts=facts, table=tables['tiers'], tables=tables, patient_ids=listed or sorted({i for v in masks.values() for i in v}),
                    cohort_role='prioritized',
                    read_back=f"Priority for {spec['label']} (living; canonical condition evidence; latest labs; fills and visits in the 12 months to {ctx.as_of}): "
                              + '; '.join(f"{l}" for _, l, _ in spec['gaps']) + (' - ordered by whether they ' + ' / '.join(l for _, l, _ in spec['escalators']) if spec['escalators'] else '') + '.',
                    notes=['Priority order is by the gaps and recent acute use named above; it is a work list, not a risk score.'])

    @classmethod
    def discharged(cls, ctx):
        days = 90
        start = f"date({q(ctx.as_of)}, '-{days} days')"
        pop = f"EXISTS (SELECT 1 FROM stay s WHERE s.patient_id = p.patient_id AND s.type = 'inpatient' AND s.discharge_known = 1 AND s.discharge_date > {start} AND s.discharge_date <= {q(ctx.as_of)})"
        n = ctx.count(pop)
        # no completed outpatient follow-up (office, specialist or telehealth contact) after the patient's latest discharge in the window
        latest = f"(SELECT MAX(s.discharge_date) FROM stay s WHERE s.patient_id = p.patient_id AND s.type = 'inpatient' AND s.discharge_known = 1 AND s.discharge_date > {start} AND s.discharge_date <= {q(ctx.as_of)})"
        nofu = f"NOT {ctx.visit_after(latest)}"
        ids = ctx.ids(pop, nofu)
        facts = [fact('n', f"{n:,} patients were discharged from an inpatient stay in the last {days} days.", value=n, unit='patients'),
                 fact('no_fu', f"{len(ids):,} of those {n:,} ({pct(len(ids) / n)}%) have had no outpatient follow-up visit since discharge.", value=len(ids),
                      numerator=len(ids), denominator=n, unit='patients')]
        return dict(headline=f"{len(ids):,} of {n:,} recently discharged patients have had no outpatient follow-up visit since discharge",
                    facts=facts, table=[{'group': 'discharged in the last 90 days', 'patients': n}, {'group': 'no follow-up visit since discharge', 'patients': len(ids)}],
                    patient_ids=ids, cohort_role='prioritized',
                    read_back=f'Living patients with an inpatient discharge in the {days} days to {ctx.as_of}; follow-up = a completed office, specialist or telehealth visit after the discharge.',
                    notes=['A follow-up is an in-person primary-care or specialist visit, or a telehealth contact, dated after the patient\'s latest discharge in the window.'])


@family
class CareGaps:
    NAME = 'care_gaps'
    WORDS = set('''where what what's are is the our biggest largest worst gap gaps care in'''.split())

    @classmethod
    def parse(cls, text, catalog):
        t = text.strip().rstrip('?.')
        if not re.match(r"^(?:where|what)(?:'s|\s+is|\s+are)?\b", t, re.I) or not span(t, r'\bgaps?\b'): return None
        if span(t, r'preventive'):
            return {'params': {'topic': 'preventive', 'ranking': 'largest gap first (lowest rate first)'}, 'quotes': [span(t, r'preventive'), span(t, r'\bgaps?\b'), span(t, r'biggest|largest|worst') or '']}
        cond = next(((span(t, p), c) for p, c in CONDITION_WORDS if span(t, p)), None)
        if not cond: return None
        return {'params': {'topic': 'condition', 'condition': cond[1], 'ranking': 'largest gap first'}, 'quotes': [cond[0], span(t, r'\bgaps?\b'), span(t, r'biggest|largest|worst') or '']}

    @classmethod
    def run(cls, ctx, params):
        if params['topic'] == 'preventive':
            return cls.preventive(ctx)
        spec = conditions_gaps(ctx)[params['condition']]
        pop = spec['population']
        n = ctx.count(pop)
        rows = []
        for gid, label, expr in spec['gaps']:
            k = ctx.count(pop, expr)
            rows.append({'gap': label, 'patients': k, 'of': n, 'share': round(k / n, 4) if n else None})
        facts = [fact('cohort', f"{n:,} {spec['label']}.", value=n, unit='patients')]
        for i, r in enumerate(rows):
            facts.append(fact(f'gap{i + 1}', f"{r['patients']:,} of {n:,} ({pct(r['share'])}%) have {r['gap']}.", value=r['patients'], numerator=r['patients'], denominator=n, unit='patients'))
        worst = max(rows, key=lambda r: r['patients'])
        facts.append(fact('biggest', f"The biggest gap is {worst['gap']}: {worst['patients']:,} of {n:,} ({pct(worst['share'])}%).", value=worst['gap']))
        return dict(headline=f"Biggest gap in {spec['label']}: {worst['gap']} ({pct(worst['share'])}%)", facts=facts, table=sorted(rows, key=lambda r: -r['patients']),
                    read_back=f"Gaps for {spec['label']} (living, canonical condition evidence, 12 months to {ctx.as_of}): " + '; '.join(r['gap'] for r in rows) + '.')

    @classmethod
    def preventive(cls, ctx):
        from . import measures as M
        import sqlite3
        out = []
        with sqlite3.connect(f'file:{ctx.target}?mode=ro', uri=True) as db:
            lib = M.library()
            for mid, name in (('bcs', 'Breast cancer screening'), ('col', 'Colorectal cancer screening')):
                if mid not in lib: continue
                label = measure_label(lib[mid], name)
                rep = M.evaluate_db(db, lib[mid], *ctx_period(ctx), ctx.as_of)
                overall = rep.get('overall') or {}
                if rep.get('declined') or overall.get('rate') is None: continue
                out.append({'measure': label, 'rate': overall['rate'], 'numerator': overall.get('numerator'), 'denominator': overall.get('denominator')})
        flu_pop = f"{ctx.age(65)}"
        season_year = int(ctx.as_of[:4]) - (0 if ctx.as_of[5:7] >= '07' else 1)
        flu_num = (f"EXISTS (SELECT 1 FROM immunization i WHERE i.patient_id = p.patient_id AND i.vaccine LIKE 'influenza%' AND i.status = 'completed' "
                   f"AND i.event_date >= {q(f'{season_year}-07-01')} AND i.event_date <= {q(ctx.as_of)})")
        flu = ctx.overall(flu_pop, flu_num)
        out.append({'measure': 'Flu vaccination (65 and older, most recent season)', 'rate': flu['rate'], 'numerator': flu['k'], 'denominator': flu['n']})
        out.sort(key=lambda r: r['rate'])
        facts = [fact(f'm{i + 1}', f"{r['measure']}: {pct(r['rate'])}% ({r['numerator']} of {r['denominator']}).", value=pct(r['rate']), numerator=r['numerator'],
                      denominator=r['denominator'], unit='%') for i, r in enumerate(out)]
        facts.append(fact('largest', f"The largest preventive-care gap is {out[0]['measure']} at {pct(out[0]['rate'])}%.", value=out[0]['measure']))
        return dict(headline=f"Largest preventive-care gap: {out[0]['measure']} ({pct(out[0]['rate'])}%)", facts=facts,
                    table=[{'measure': r['measure'], 'rate_percent': pct(r['rate']), 'numerator': r['numerator'], 'denominator': r['denominator']} for r in out],
                    read_back='Preventive screening and vaccination rates (governed HEDIS-style measures for breast and colorectal screening; flu = a completed influenza vaccine in the most recent season), lowest first.',
                    notes=['Breast and colorectal screening come from the governed measure library (approximations of the HEDIS measures); flu vaccination counts a completed influenza dose since July 1 of the latest season.'])


def measure_label(m, name):
    """A governed measure's name with the population and look-back it is DEFINED with (age band at period end, sex, a single-criterion window),
    so the label never claims a band the measure does not read (the HEDIS-style library reads women 42-74, not 50-74)."""
    den = m.get('denominator') or {}
    age = den.get('age') or {}
    who = 'women' if (den.get('sex') or '').upper().startswith('F') else 'adults'
    parts = [f"{who} {age['min']}-{age['max']} at period end" if 'min' in age and 'max' in age else who]
    criteria = (m.get('numerator') or {}).get('criteria') or []
    if len(criteria) == 1 and criteria[0].get('label'):
        parts.append(str(criteria[0]['label'])[:1].lower() + str(criteria[0]['label'])[1:])
    return f"{name} ({', '.join(parts)})"


def ctx_period(ctx):
    return decision_period(ctx)


def decision_period(ctx):
    from . import decision_context
    return decision_context.rolling_year(ctx.as_of)


# ============================================================================================================================
# spend
# ============================================================================================================================
SEGMENTS = [  # phrase -> (label, column on the claim / member-month row, values)
    (r'medicare\s+advantage|\bma\b(?:\s+members)?', 'Medicare Advantage', 'product', ['medicare_advantage']),  # the product label as received; dual-eligible MA plans carry their own dual_ma product
    (r'dual[- ]eligibles?(?:\s+members)?|duals?', 'dual-eligible', 'line_of_business', ['dual']),
    (r'medicaid(?:\s+members)?', 'Medicaid', 'line_of_business', ['medicaid']),
    (r'commercial(?:\s+members)?', 'commercial', 'line_of_business', ['commercial']),
    (r'medigap', 'Medigap', 'line_of_business', ['medigap']),
    (r'medicare(?:\s+members)?', 'Medicare (including dual-eligible and Medigap)', 'line_of_business', ['medicare', 'dual', 'medigap']),
]
HIGH_COST_USD = 50_000  # a "high-cost claimant": $50,000 or more paid in the year (the cutoff used for high-cost claimants throughout)


def segment_in(text):
    for pattern, label, column, values in SEGMENTS:
        m = re.search(r'(?<![\w-])(?:' + pattern + r')(?![\w-])', text, re.I)
        if m: return m.group(), label, column, values
    return None


def seg_sql(alias, column, values):
    return f"{alias}.{column} IN ({','.join(q(v) for v in values)})"


def year_totals(ctx, y, seg=None):
    """Counted payer-adjudicated claims of the calendar year in an enrolled member month of the segment (the governed PMPM rule), by claim type."""
    cond = f" AND {seg_sql('m', seg[1], seg[2])}" if seg else ''
    claims = ctx.rows(
        f"SELECT c.claim_type AS claim_type, COUNT(*) AS claims, SUM(c.paid_cents) AS paid_cents FROM payer_claim c WHERE c.counts_in_metrics = 1 "
        f"AND substr(c.service_from, 1, 4) = {q(str(y))} AND EXISTS (SELECT 1 FROM member_month m WHERE m.patient_id = c.patient_id "
        f"AND m.month = substr(c.service_from, 1, 7){cond}) GROUP BY c.claim_type")
    mm = ctx.one(f"SELECT COUNT(*) FROM member_month m WHERE substr(m.month, 1, 4) = {q(str(y))}{cond}", 0)
    big = ctx.rows(
        f"SELECT COUNT(*) AS members, COALESCE(SUM(t), 0) AS paid_cents FROM (SELECT c.patient_id, SUM(c.paid_cents) AS t FROM payer_claim c WHERE c.counts_in_metrics = 1 "
        f"AND substr(c.service_from, 1, 4) = {q(str(y))} AND EXISTS (SELECT 1 FROM member_month m WHERE m.patient_id = c.patient_id AND m.month = substr(c.service_from, 1, 7){cond}) "
        f"GROUP BY c.patient_id HAVING SUM(c.paid_cents) >= {HIGH_COST_USD * 100})")[0]
    paid = sum(r['paid_cents'] or 0 for r in claims)
    n_claims = sum(r['claims'] for r in claims)
    return {'year': y, 'by_type': {r['claim_type'] or 'unknown': r for r in claims}, 'paid_cents': paid, 'claims': n_claims, 'member_months': mm,
            'pmpm': (paid / 100.0 / mm) if mm else None, 'big': big}


@family
class SpendDrivers:
    NAME = 'spend_drivers'
    WORDS = set('''what is are driving why did the for in jump jumped rise rose increase increased go went up spike spiked climb climbed change changed
        grow grew drop dropped fall fell pmpm medical spend cost costs members member'''.split())

    @classmethod
    def parse(cls, text, catalog):
        t = text.strip().rstrip('?.')
        driving = re.match(r"^what(?:'s|\s+is|\s+are)\s+driving\b", t, re.I)
        change = re.match(r'^why\s+(?:did|has|have|is|was|were)\b', t, re.I)
        if not (driving or change): return None
        metric = span(t, r'(?:medical\s+)?pmpm|per\s+member\s+per\s+month|(?:medical\s+)?(?:spend|spending|cost|costs)')
        if not metric: return None
        seg = segment_in(t)
        years = years_in(t)
        verb = span(t, r'jump\w*|ris\w+|rose|increas\w+|go\s+up|went\s+up|spik\w+|climb\w*|chang\w+|grow\w*|grew|drop\w*|fall\w*|fell')
        if change and not verb: return None
        quotes = [driving.group() if driving else change.group(), metric, verb or '', *(str(y) for y in years)]
        if seg: quotes.append(seg[0])
        return {'params': {'segment': list(seg[1:]) if seg else None, 'years': years, 'change': bool(change)}, 'quotes': quotes}

    @classmethod
    def run(cls, ctx, params):
        seg = tuple(params['segment']) if params['segment'] else None
        label = seg[0] if seg else 'all members'
        years = params['years']
        notes = ['PMPM = paid dollars on counted payer-adjudicated claims (current non-duplicate versions) whose service month is an enrolled member month of the segment, '
                 'divided by the segment\'s member months (the governed paid PMPM rule).']
        if params['change']:
            y1 = years[-1] if years else last_full_year(ctx)
            y0 = years[-2] if len(years) > 1 else y1 - 1
        else:
            y1, y0 = (years[-1] if years else last_full_year(ctx)), None
            if not years: notes.append(FULL_YEAR_NOTE.format(y=y1, a=ctx.as_of[:4]))
        a = year_totals(ctx, y1, seg)
        b = year_totals(ctx, y0, seg) if y0 else None
        overall = year_totals(ctx, y1, None)
        facts, tables = [], {}

        def mix(t, tot):
            return {k: (v['paid_cents'] / tot if tot else None) for k, v in t['by_type'].items()}
        if not a['member_months']:
            return dict(headline=f'No {label} member months in {y1}', facts=[fact('none', f'There are no {label} member months in {y1}.')], read_back='', notes=notes)
        facts.append(fact('pmpm', f"{label} medical paid PMPM in {y1} is ${a['pmpm']:,.0f} (${a['paid_cents'] / 100:,.0f} paid over {a['member_months']:,} member months).",
                          value=round(a['pmpm'], 2), numerator=round(a['paid_cents'] / 100, 2), denominator=a['member_months'], unit='USD per member month'))
        inst = mix(a, a['paid_cents']).get('institutional')
        if inst is not None:
            facts.append(fact('inst', f"Hospital (institutional) claims are {pct(inst, 0):.0f}% of {label} paid spend ({usd(a['by_type']['institutional']['paid_cents'] / 100)} of {usd(a['paid_cents'] / 100)}) = {inst:.4f}.",
                              value=pct(inst), numerator=a['by_type']['institutional']['paid_cents'] / 100, denominator=a['paid_cents'] / 100, unit='%'))
        prof = mix(a, a['paid_cents']).get('professional')
        if prof is not None:
            facts.append(fact('prof', f"Professional claims are {pct(prof, 0):.0f}% of {label} paid spend.", value=pct(prof), unit='%'))
        util = 1000.0 * a['claims'] / a['member_months']
        price = a['paid_cents'] / 100.0 / a['claims'] if a['claims'] else None
        facts.append(fact('util_price', f"{label} members generate {util:,.0f} claims per 1,000 member months at an average of ${price:,.0f} paid per claim.",
                          value=round(util, 1), unit='claims per 1,000 member months', price=round(price, 2)))
        big = a['big']
        if big['members']:
            share = big['paid_cents'] / a['paid_cents']
            facts.append(fact('highcost', f"{big['members']} {label} members with ${HIGH_COST_USD:,} or more paid account for {pct(share, 0):.0f}% of {label} spend "
                                          f"({usd(big['paid_cents'] / 100)} of {usd(a['paid_cents'] / 100)}) = {share:.4f}.",
                              value=pct(share), numerator=big['paid_cents'] / 100, denominator=a['paid_cents'] / 100, unit='%', members=big['members']))
        if seg and overall['pmpm']:
            facts.append(fact('vs_all', f"For comparison, PMPM across all members in {y1} is ${overall['pmpm']:,.0f}; {label} is {a['pmpm'] / overall['pmpm']:.2f} times that.",
                              value=round(overall['pmpm'], 2), unit='USD per member month'))
        tables['by_claim_type'] = [{'claim_type': k, 'claims': v['claims'], 'paid_dollars': round(v['paid_cents'] / 100, 2),
                                    'share_of_paid': round(v['paid_cents'] / a['paid_cents'], 4)} for k, v in sorted(a['by_type'].items(), key=lambda kv: -kv[1]['paid_cents'])]
        head = f"{label} PMPM in {y1}: ${a['pmpm']:,.0f}"
        if b and b['pmpm']:
            change = (a['pmpm'] - b['pmpm']) / b['pmpm']
            facts.insert(0, fact('pmpm_prior', f"{label} PMPM was ${b['pmpm']:,.0f} in {y0}.", value=round(b['pmpm'], 2), unit='USD per member month'))
            facts.insert(1, fact('change', f"{label} PMPM {'rose' if change > 0 else 'fell'} {abs(change) * 100:.0f}% from ${b['pmpm']:,.0f} in {y0} to ${a['pmpm']:,.0f} in {y1} "
                                           f"({'+' if change > 0 else ''}{change * 100:.1f}%).", value=round(change * 100, 1), unit='%'))
            deltas = []
            for k in sorted(set(a['by_type']) | set(b['by_type'])):
                d = (a['by_type'].get(k, {}).get('paid_cents') or 0) - (b['by_type'].get(k, {}).get('paid_cents') or 0)
                deltas.append((k, d / 100.0, (b['by_type'].get(k, {}).get('paid_cents') or 0) / 100.0, (a['by_type'].get(k, {}).get('paid_cents') or 0) / 100.0))
            deltas.sort(key=lambda x: -abs(x[1]))
            total_delta = (a['paid_cents'] - b['paid_cents']) / 100.0
            for k, d, v0, v1 in deltas:
                facts.append(fact(f'delta_{k}', f"{k.capitalize()} paid {'rose' if d > 0 else 'fell'} from {usd(v0)} to {usd(v1)} ({'+' if d > 0 else '-'}{usd(abs(d))}) between {y0} and {y1}"
                                                + (f", {abs(d) / abs(total_delta) * 100:.0f}% of the total change of {'+' if total_delta > 0 else '-'}{usd(abs(total_delta))}." if total_delta else '.'),
                                  value=round(d, 2), unit='USD'))
            if deltas:
                facts.append(fact('main_driver', f"Most of the change is {deltas[0][0]} spend ({'+' if deltas[0][1] > 0 else '-'}{usd(abs(deltas[0][1]))}).", value=deltas[0][0]))
            mm_change = (a['member_months'] - b['member_months'])
            util0 = 1000.0 * b['claims'] / b['member_months'] if b['member_months'] else None
            price0 = b['paid_cents'] / 100.0 / b['claims'] if b['claims'] else None
            if util0 and price0:
                facts.append(fact('util_price_change', f"Claims per 1,000 member months went from {util0:,.0f} to {util:,.0f} and the average paid per claim from ${price0:,.0f} to ${price:,.0f}; "
                                                       f"member months went from {b['member_months']:,} to {a['member_months']:,}.", value=[round(util0, 1), round(util, 1)]))
            tables['change_by_claim_type'] = [{'claim_type': k, f'paid_{y0}': round(v0, 2), f'paid_{y1}': round(v1, 2), 'change': round(d, 2)} for k, d, v0, v1 in deltas]
            head = f"{label} PMPM {'rose' if change > 0 else 'fell'} {abs(change) * 100:.0f}% to ${a['pmpm']:,.0f} in {y1}; mostly {deltas[0][0]} spend"
        return dict(headline=head, facts=facts, table=tables.get('change_by_claim_type') or tables['by_claim_type'], tables=tables,
                    read_back=f"Paid PMPM and what drives it for {label}, {y1}" + (f" versus {y0}" if b else '') + '; counted payer-adjudicated claims in enrolled member months; spend split by claim type, utilization and price, and high-cost members.',
                    notes=notes)


@family
class SpendConcentration:
    NAME = 'spend_concentration'
    WORDS = set('''how concentrated is our medical spend much comes from the top few percent of patients'''.split())

    @classmethod
    def parse(cls, text, catalog):
        t = text.strip().rstrip('?.')
        if not span(t, r'concentrat\w+') or not span(t, r'spend|cost|paid'): return None
        return {'params': {'years': years_in(t)}, 'quotes': [span(t, r'concentrat\w+'), span(t, r'spend|cost|paid'), span(t, r'top\s+\w+\s+percent|top\s+\d+\s*%') or '']}

    @classmethod
    def run(cls, ctx, params):
        y = params['years'][-1] if params['years'] else last_full_year(ctx)
        notes = [] if params['years'] else [FULL_YEAR_NOTE.format(y=y, a=ctx.as_of[:4])]
        rows = ctx.rows(
            f"SELECT c.patient_id AS patient_id, SUM(c.paid_cents) AS paid_cents FROM payer_claim c JOIN patient p ON p.patient_id = c.patient_id "
            f"WHERE c.counts_in_metrics = 1 AND substr(c.service_from, 1, 4) = {q(str(y))} AND {ctx.living} GROUP BY c.patient_id ORDER BY paid_cents DESC, c.patient_id")
        n = len(rows)
        total = sum(r['paid_cents'] for r in rows)
        facts, table = [fact('base', f"{n:,} living patients had counted medical claims in {y}, with {usd(total / 100)} paid in total.", value=n, unit='patients')], []
        for share in (0.01, 0.05, 0.10):
            k = max(1, int(n * share + 0.5))
            paid = sum(r['paid_cents'] for r in rows[:k])
            facts.append(fact(f'top{int(share * 100)}', f"The top {int(share * 100)}% of patients with claims ({k:,} patients) account for {pct(paid / total, 0):.0f}% of {y} paid spend "
                                                         f"({usd(paid / 100)} of {usd(total / 100)}) = {paid / total:.4f}.", value=pct(paid / total), numerator=paid / 100, denominator=total / 100, unit='%', patients=k))
            table.append({'top_percent': int(share * 100), 'patients': k, 'paid_dollars': round(paid / 100, 2), 'share_of_paid': round(paid / total, 4)})
        big = [r for r in rows if r['paid_cents'] >= HIGH_COST_USD * 100]
        bp = sum(r['paid_cents'] for r in big)
        facts.append(fact('highcost', f"{len(big)} patients with ${HIGH_COST_USD:,} or more paid account for {pct(bp / total, 0):.0f}% of {y} spend ({usd(bp / 100)}).", value=len(big),
                          numerator=bp / 100, denominator=total / 100, unit='patients'))
        return dict(headline=f"The top 5% of patients with claims account for {pct(table[1]['share_of_paid'], 0):.0f}% of {y} paid spend; the top 1% for {pct(table[0]['share_of_paid'], 0):.0f}%",
                    facts=facts, table=table, tables={'concentration': table},
                    read_back=f"Concentration of {y} paid spend on counted medical claims across living patients with claims; counts rounded to the nearest patient.", notes=notes)


@family
class YoyTrend:
    NAME = 'yoy_trend'
    WORDS = set('''did do does are is were was go up or down from to and which drugs moved it most trending hospital pharmacy visits'''.split())
    METRICS = [(r'hospital\s+admissions|admissions|inpatient\s+stays', 'admissions'), (r'ed\s+visits|emergency\s+(?:department\s+)?visits', 'ed'),
               (r'pharmacy\s+spend|drug\s+spend|rx\s+spend', 'pharmacy'), (r'medical\s+spend|claims\s+spend|medical\s+claims\s+spend', 'medical')]

    @classmethod
    def parse(cls, text, catalog):
        t = text.strip().rstrip('?.')
        metric = next(((span(t, p), m) for p, m in cls.METRICS if span(t, p)), None)
        yrs = years_in(t)
        direction = span(t, r'up\s+or\s+down|trending|going\s+up|go\s+up|increas\w+|decreas\w+')
        if not metric or len(yrs) < 2 or not direction: return None
        movers = span(t, r'which\s+drugs?\s+moved\s+it\s+most')
        return {'params': {'metric': metric[1], 'y0': yrs[0], 'y1': yrs[1], 'movers': bool(movers), 'ranking': 'largest change first'},
                'quotes': [metric[0], direction, str(yrs[0]), str(yrs[1]), movers or '', span(t, r'trending') or '']}

    @classmethod
    def run(cls, ctx, params):
        y0, y1, metric = params['y0'], params['y1'], params['metric']
        facts, tables, notes = [], {}, []
        if metric in ('admissions', 'ed'):
            kind = 'inpatient' if metric == 'admissions' else 'ed_visit'
            noun = 'hospital admissions' if metric == 'admissions' else 'ED visits'
            vals = {}
            for y in (y0, y1):
                vals[y] = ctx.one(f"SELECT COUNT(*) FROM utilization_event WHERE kind = {q(kind)} AND substr(event_date, 1, 4) = {q(str(y))}", 0)
            unit = 'events'
            notes.append(f"{cap(noun)} are the all-source utilization_event rows (EHR, ADT and claims, de-duplicated), counted by event year.")
        elif metric == 'pharmacy':
            noun, unit = 'pharmacy spend', 'USD'
            vals = {y: (ctx.one(f"SELECT COALESCE(SUM(plan_paid_cents), 0) FROM payer_pharmacy_claim WHERE counts_as_fill = 1 AND substr(fill_date, 1, 4) = {q(str(y))}", 0) or 0) / 100.0
                    for y in (y0, y1)}
            notes.append('Pharmacy spend is plan-paid dollars on pharmacy claims that count as fills (paid, not reversed or rejected), by fill year.')
        else:
            noun, unit = 'medical spend', 'USD'
            vals = {y: (ctx.one(f"SELECT COALESCE(SUM(paid_cents), 0) FROM payer_claim WHERE counts_in_metrics = 1 AND substr(service_from, 1, 4) = {q(str(y))}", 0) or 0) / 100.0
                    for y in (y0, y1)}
            notes.append('Medical spend is paid dollars on counted payer-adjudicated claims by service year.')
        d = vals[y1] - vals[y0]
        pc = d / vals[y0] if vals[y0] else None
        word = 'down' if d < 0 else 'up' if d > 0 else 'flat'
        money = unit == 'USD'
        f = (lambda v: usd(v)) if money else (lambda v: f'{v:,.0f}')
        facts.append(fact('y0', f"{cap(noun)} in {y0}: {f(vals[y0])}.", value=vals[y0], unit=unit))
        facts.append(fact('y1', f"{cap(noun)} in {y1}: {f(vals[y1])}.", value=vals[y1], unit=unit))
        facts.append(fact('direction', f"{cap(noun)} went {word} from {y0} to {y1}: {'+' if d > 0 else ''}{f(d) if not money else ('-' if d < 0 else '') + usd(abs(d))}"
                                       + (f" ({'+' if d > 0 else ''}{pc * 100:.1f}%)." if pc is not None else '.'), value=word, change=d, change_percent=round(pc * 100, 1) if pc is not None else None))
        table = [{'year': y0, noun: vals[y0]}, {'year': y1, noun: vals[y1]}]
        if metric == 'pharmacy' and params['movers']:
            rows = ctx.rows(
                f"SELECT drug_name, SUM(CASE WHEN substr(fill_date, 1, 4) = {q(str(y1))} THEN COALESCE(plan_paid_cents, 0) ELSE 0 END) / 100.0 AS spend_{y1}, "
                f"SUM(CASE WHEN substr(fill_date, 1, 4) = {q(str(y0))} THEN COALESCE(plan_paid_cents, 0) ELSE 0 END) / 100.0 AS spend_{y0} FROM payer_pharmacy_claim "
                f"WHERE counts_as_fill = 1 AND substr(fill_date, 1, 4) IN ({q(str(y0))}, {q(str(y1))}) GROUP BY drug_name")
            for r in rows: r['change'] = round(r[f'spend_{y1}'] - r[f'spend_{y0}'], 2)
            ups = sorted(rows, key=lambda r: -r['change'])[:3]
            downs = sorted(rows, key=lambda r: r['change'])[:3]
            facts.append(fact('movers_up', 'Largest increases: ' + '; '.join(f"{r['drug_name']} (+{usd(r['change'])})" for r in ups if r['change'] > 0) + '.', value=[r['drug_name'] for r in ups]))
            facts.append(fact('movers_down', 'Largest decreases: ' + '; '.join(f"{r['drug_name']} (-{usd(abs(r['change']))})" for r in downs if r['change'] < 0) + '.', value=[r['drug_name'] for r in downs]))
            tables['drug_movers'] = [{'drug': r['drug_name'], f'spend_{y0}': round(r[f'spend_{y0}'], 2), f'spend_{y1}': round(r[f'spend_{y1}'], 2), 'change': r['change']} for r in ups + downs]
            table = tables['drug_movers']
        return dict(headline=f"{cap(noun)} went {word} from {y0} to {y1} ({f(vals[y0])} to {f(vals[y1])})", facts=facts, table=table, tables=tables,
                    read_back=f"{cap(noun)} {y0} versus {y1}" + (' and the drugs whose spend moved most' if params['movers'] else '') + '.', notes=notes)


@family
class DenialDrivers:
    NAME = 'denial_drivers'
    MIN_CLAIMS = 500  # a payer is ranked on its denial rate only above this many counted claims in the year
    WORDS = set('''why is our denial rate so high with worst payer'''.split())

    @classmethod
    def parse(cls, text, catalog):
        t = text.strip().rstrip('?.')
        if not re.match(r'^why\b', t, re.I) or not span(t, r'denial'): return None
        worst = span(t, r'worst\s+payer')
        if not worst: return None
        return {'params': {'years': years_in(t)}, 'quotes': [span(t, r'denial\s+rate|denials?'), worst, span(t, r'so\s+high|high') or '', *[str(y) for y in years_in(t)]]}

    @classmethod
    def run(cls, ctx, params):
        y = params['years'][-1] if params['years'] else last_full_year(ctx)
        notes = [] if params['years'] else [FULL_YEAR_NOTE.format(y=y, a=ctx.as_of[:4])]
        denied = "c.final_status IN ('denied','denied_upheld')"
        base = f"c.counts_in_metrics = 1 AND substr(c.service_from, 1, 4) = {q(str(y))}"
        payers = ctx.rows(f"SELECT c.payer_id AS payer, COUNT(*) AS claims, SUM(CASE WHEN {denied} THEN 1 ELSE 0 END) AS denials, 1.0 * SUM(CASE WHEN {denied} THEN 1 ELSE 0 END) / COUNT(*) AS rate "
                          f"FROM payer_claim c WHERE {base} GROUP BY c.payer_id HAVING COUNT(*) >= {cls.MIN_CLAIMS} ORDER BY rate DESC, payer LIMIT 5")
        overall = ctx.rows(f"SELECT COUNT(*) AS claims, SUM(CASE WHEN {denied} THEN 1 ELSE 0 END) AS denials FROM payer_claim c WHERE {base}")[0]
        if not payers:
            return dict(headline=f'No payer has {cls.MIN_CLAIMS} counted claims in {y}', facts=[fact('none', f'No payer has at least {cls.MIN_CLAIMS} counted claims in {y}, so no payer is ranked on denial rate.')],
                        read_back='', notes=notes)
        worst = payers[0]
        reasons = ctx.rows(f"SELECT c.carc AS reason, COUNT(*) AS denials FROM payer_claim c WHERE {base} AND c.payer_id = {q(worst['payer'])} AND {denied} GROUP BY c.carc ORDER BY denials DESC, reason")
        orate = overall['denials'] / overall['claims']
        facts = [fact('payer', f"The payer with the highest denial rate in {y} (payers with at least {cls.MIN_CLAIMS} claims) is {worst['payer']}.", value=worst['payer']),
                 fact('rate', f"{worst['payer']} denies {pct(worst['rate'])}% of claims ({worst['denials']:,} of {worst['claims']:,}) = {worst['rate']:.4f} versus {pct(orate)}% overall ({overall['denials']:,} of {overall['claims']:,}).",
                      value=pct(worst['rate']), numerator=worst['denials'], denominator=worst['claims'], unit='%')]
        total = sum(r['denials'] for r in reasons)
        for i, r in enumerate(reasons[:3]):
            facts.append(fact(f'reason{i + 1}', f"Denial reason {r['reason']} accounts for {r['denials']} of {total} {worst['payer']} denials ({r['denials'] / total * 100:.0f}%) = {r['denials'] / total:.4f}.",
                              value=round(r['denials'] / total * 100, 1), numerator=r['denials'], denominator=total, unit='%'))
        if reasons:
            facts.append(fact('mix', f"Denial reason codes at {worst['payer']} by count: " + ', '.join(f"{r['reason']} ({r['denials']})" for r in reasons[:5]) + '.', value=[r['reason'] for r in reasons[:5]]))
        return dict(headline=f"{worst['payer']} has the highest denial rate ({pct(worst['rate'])}% vs {pct(orate)}% overall); top reason {reasons[0]['reason'] if reasons else 'n/a'}",
                    facts=facts, table=[{'reason_code': r['reason'], 'denials': r['denials'], 'share_of_payer_denials': round(r['denials'] / total, 4)} for r in reasons], tables={'payers': payers},
                    read_back=f"Denial rate by payer for {y} (payers with at least {cls.MIN_CLAIMS} counted claims), the worst payer, and the mix of its denial reason codes.", notes=notes)


# ============================================================================================================================
# association, comparison, users, adherence
# ============================================================================================================================
RESOLVED = "('no_show','fulfilled','attended','arrived')"  # appointments whose outcome is known: missed, or kept


def noshow_rate_sql(where, group=None):
    g = f'{group} AS grp, ' if group else ''
    return (f"SELECT {g}SUM(CASE WHEN a.final_status = 'no_show' THEN 1 ELSE 0 END) AS missed, COUNT(*) AS resolved FROM appointment a JOIN patient p ON p.patient_id = a.patient_id "
            f"WHERE a.final_status IN {RESOLVED} AND {where}" + (f' GROUP BY {group}' if group else ''))


@family
class Association:
    NAME = 'association'
    WORDS = set('''which does is are most more likely to tied linked related associated social need needs missed appointments booking further out make
        people lead time'''.split())

    @classmethod
    def parse(cls, text, catalog):
        t = text.strip().rstrip('?.')
        need = re.match(r'^which\s+social\s+need\b', t, re.I) and span(t, r'(?:tied|linked|related|associated)\s+(?:to|with)\s+(?:missed\s+appointments|no[- ]?shows?)')
        if need:
            return {'params': {'kind': 'need'}, 'quotes': [span(t, r'social\s+need'), need, span(t, r'most')]}
        lead = re.match(r'^does\s+(?:booking|scheduling)\s+(?:further|farther)\s+out\b', t, re.I) and span(t, r'more\s+likely\s+to\s+no[- ]?show|(?:cause|increase|raise)\s+no[- ]?shows?')
        if lead:
            return {'params': {'kind': 'lead_time'}, 'quotes': [span(t, r'booking\s+(?:further|farther)\s+out|scheduling\s+(?:further|farther)\s+out'), lead, span(t, r'make\s+people') or '']}
        return None

    @classmethod
    def run(cls, ctx, params):
        window = f"{ctx.in_window('a.scheduled_date')}"
        notes = [f'Appointments scheduled in the 12 months to {ctx.as_of} whose outcome is known (no-show, fulfilled, attended or arrived); the no-show rate is no-shows over those.',
                 'Patients are living patients.']
        if params['kind'] == 'need':
            needs = [r['need'] for r in ctx.rows("SELECT DISTINCT need FROM social_need WHERE positive = 1 ORDER BY need")]
            rows = []
            for need in needs:
                pos = ctx.rows(noshow_rate_sql(f"{window} AND {ctx.living} AND {ctx.social_need(need)}"))[0]
                neg = ctx.rows(noshow_rate_sql(f"{window} AND {ctx.living} AND NOT {ctx.social_need(need)}"))[0]
                if not pos['resolved'] or not neg['resolved']: continue
                rows.append({'need': need, 'positive_rate': pos['missed'] / pos['resolved'], 'positive': pos, 'others_rate': neg['missed'] / neg['resolved'], 'others': neg})
            rows.sort(key=lambda r: -(r['positive_rate'] - r['others_rate']))
            facts = []
            for i, r in enumerate(rows):
                facts.append(fact(f"need_{r['need']}", f"{r['need'].capitalize()} (screened positive): no-show rate {pct(r['positive_rate'])}% ({r['positive']['missed']} of {r['positive']['resolved']} appointments) "
                                                       f"versus {pct(r['others_rate'])}% among the other patients ({r['others']['missed']} of {r['others']['resolved']}).",
                                  value=pct(r['positive_rate']), numerator=r['positive']['missed'], denominator=r['positive']['resolved'], unit='%', others=pct(r['others_rate'])))
            top = rows[0]
            facts.insert(0, fact('top', f"{top['need'].capitalize()} (screened positive) shows the biggest no-show gap: {pct(top['positive_rate'])}% vs {pct(top['others_rate'])}% "
                                        f"(+{(top['positive_rate'] - top['others_rate']) * 100:.1f} points).", value=top['need']))
            return dict(headline=f"{top['need'].capitalize()} is the social need most tied to missed appointments ({pct(top['positive_rate'])}% vs {pct(top['others_rate'])}%)", facts=facts,
                        table=[{'need': r['need'], 'no_show_rate_positive_percent': pct(r['positive_rate']), 'positive_appointments': r['positive']['resolved'],
                                'no_show_rate_others_percent': pct(r['others_rate']), 'other_appointments': r['others']['resolved'],
                                'gap_points': round((r['positive_rate'] - r['others_rate']) * 100, 1)} for r in rows],
                        read_back='No-show rate for patients who screened positive for each social need versus all other patients, appointments of the last 12 months; the biggest gap is named.', notes=notes)
        buckets = [('booked within a week (0-7 days ahead)', 'a.lead_time_days <= 7'), ('booked 8-30 days ahead', 'a.lead_time_days BETWEEN 8 AND 30'), ('booked 31 or more days ahead', 'a.lead_time_days >= 31')]
        rows = []
        for label, cond in buckets:
            r = ctx.rows(noshow_rate_sql(f"{window} AND {ctx.living} AND a.lead_time_days IS NOT NULL AND {cond}"))[0]
            rows.append({'bucket': label, 'missed': r['missed'], 'resolved': r['resolved'], 'rate': r['missed'] / r['resolved'] if r['resolved'] else None})
        facts = [fact(f'b{i}', f"{r['bucket'].capitalize()}: {pct(r['rate'])}% no-show ({r['missed']} of {r['resolved']} appointments).", value=pct(r['rate']),
                      numerator=r['missed'], denominator=r['resolved'], unit='%') for i, r in enumerate(rows)]
        long, short = rows[2], rows[0]
        yes = long['rate'] > short['rate'] and long['rate'] >= max(r['rate'] for r in rows)
        facts.insert(0, fact('answer', f"{'Yes' if yes else 'No'}, " + (f"long lead times have the highest no-show rate ({pct(long['rate'])}% for 31+ days vs {pct(short['rate'])}% within a week)."
                                                                         if yes else f"booking further out does not raise the no-show rate ({pct(long['rate'])}% for 31+ days vs {pct(short['rate'])}% within a week)."), value=yes))
        return dict(headline=('Yes: ' if yes else 'No: ') + f"no-show rate is {pct(long['rate'])}% when booked 31+ days ahead vs {pct(short['rate'])}% within a week", facts=facts,
                    table=[{'bucket': r['bucket'], 'no_show_rate_percent': pct(r['rate']), 'no_shows': r['missed'], 'appointments': r['resolved']} for r in rows],
                    read_back='No-show rate by how far ahead the appointment was booked (lead_time_days), appointments of the last 12 months with a known outcome.', notes=notes)


SEGMENT_MEASURES = [(r'diabetes\s+control|a1c|glycemic', 'diabetes'), (r'\bed\s+(?:use|visits|utilization)|emergency(?:\s+(?:department|room))?(?:\s+(?:use|visits))?', 'ed'),
                    (r'blood\s+pressure|bp\s+control|hypertension\s+control', 'bp'), (r'(?:hospital\s+|inpatient\s+)?admissions?', 'admissions')]


HIGH_SVI, LOW_SVI = 75, 25  # social vulnerability percentile (0-100): "high" is 75 or more, "low" is under 25 unless the question states its own cut


def patient_segment_in(text):
    """The first patient segment named in the text -> (quote, label, SQL over alias p) or None. Segments: social vulnerability bands, lines of business."""
    m = re.search(r'(?<![\w-])high[- ]svi(?:\s*\(\s*(\d+)\s*\+?\s*\))?', text, re.I)
    if m:
        cut = int(m[1]) if m[1] else HIGH_SVI
        return m.group(), f'high-SVI ({cut}+)', f"EXISTS (SELECT 1 FROM patient_svi s WHERE s.patient_id = p.patient_id AND s.svi_overall >= {cut})"
    m = re.search(r'(?<![\w-])low[- ]svi(?:\s*\(\s*(?:under|below|<)\s*(\d+)\s*\))?', text, re.I)
    if m:
        cut = int(m[1]) if m[1] else LOW_SVI
        return m.group(), f'low-SVI (under {cut})', f"EXISTS (SELECT 1 FROM patient_svi s WHERE s.patient_id = p.patient_id AND s.svi_overall < {cut})"
    seg = segment_in(text)
    if seg: return seg[0], seg[1], seg_in_patient(seg[2], seg[3])
    return None


@family
class SegmentCompare:
    NAME = 'segment_compare'
    WORDS = set('''how does do compare with and on use control versus vs between per patients last year in the'''.split())

    @classmethod
    def parse(cls, text, catalog):
        t = text.strip().rstrip('?.')
        m = re.match(r'^how\s+(?:does|do)\s+(?P<a>.+?)\s+compare\s+(?:with|to|against)\s+(?P<b>.+?)\s+on\s+(?P<m>.+)$', t, re.I)
        if not m:
            m = re.match(r'^compare\s+(?P<m>.+?)\s+between\s+(?P<a>.+?)\s+and\s+(?P<b>.+)$', t, re.I)
            if not m: return None
        sa, sb = patient_segment_in(m['a']), patient_segment_in(m['b'])
        if not sa or not sb: return None
        ms = [(span(m['m'], p), k) for p, k in SEGMENT_MEASURES if span(m['m'], p)]
        if not ms: return None
        extra = [w for w in (span(m['m'], r'per\s+1,?000(?:\s+patients)?'), span(m['m'], r'in\s+the\s+last\s+year')) if w]
        return {'params': {'a': [sa[1], sa[2]], 'b': [sb[1], sb[2]], 'measures': [k for _, k in ms]},
                'quotes': [sa[0], sb[0], *[x for x, _ in ms], 'compare', *extra]}

    @classmethod
    def run(cls, ctx, params):
        facts, rows = [], []
        segs = [params['a'], params['b']]
        for label, cond in segs:
            for mk in params['measures']:
                if mk == 'diabetes':
                    pop = f"{ctx.cond('diabetes')} AND {ctx.age(18)} AND {ctx.has_lab('hba1c')} AND {cond}"
                    for tag, num, text_ in (('poor', ctx.lab('hba1c', '>', 9), 'with a latest A1c above 9'), ('controlled', ctx.lab('hba1c', '<', 8), 'with a latest A1c under 8')):
                        r = ctx.overall(pop, num)
                        facts.append(fact(f'{label}_a1c_{tag}', f"{label} diabetics {text_}: {pct(r['rate'])}% ({r['k']} of {r['n']} adult diabetics with a latest A1c) = {r['rate']:.4f}.",
                                          value=pct(r['rate']), numerator=r['k'], denominator=r['n'], unit='%'))
                        rows.append({'segment': label, 'measure': f'adult diabetics {text_}', 'percent': pct(r['rate']), 'numerator': r['k'], 'denominator': r['n']})
                elif mk == 'bp':
                    pop = f"{ctx.cond('hypertension')} AND {ctx.age(18, 85)} AND {ctx.has_lab('systolic_bp')} AND {ctx.has_lab('diastolic_bp')} AND {cond}"
                    r = ctx.overall(pop, f"{ctx.lab('systolic_bp', '<', 140)} AND {ctx.lab('diastolic_bp', '<', 90)}")
                    facts.append(fact(f'{label}_bp', f"{label} hypertensives with blood pressure under control: {pct(r['rate'])}% ({r['k']} of {r['n']}).", value=pct(r['rate']),
                                      numerator=r['k'], denominator=r['n'], unit='%'))
                    rows.append({'segment': label, 'measure': 'hypertensives with blood pressure under 140/90', 'percent': pct(r['rate']), 'numerator': r['k'], 'denominator': r['n']})
                else:
                    kind = 'ed_visit' if mk == 'ed' else 'inpatient'
                    noun = 'ED visits' if mk == 'ed' else 'hospital admissions'
                    r = ctx.rows(f"SELECT COUNT(*) AS n, COALESCE(SUM((SELECT COUNT(*) FROM utilization_event u WHERE u.patient_id = p.patient_id AND u.kind = {q(kind)} AND {ctx.in_window('u.event_date')})), 0) AS k "
                                 f"FROM patient p WHERE {ctx.living} AND {cond}")[0]
                    rate = 1000.0 * r['k'] / r['n'] if r['n'] else None
                    facts.append(fact(f'{label}_{mk}', f"{label} {noun} per 1,000 patients (last 12 months): {rate:.0f} ({r['k']:,} {noun} among {r['n']:,} patients) = {rate:.1f}.",
                                      value=round(rate, 1), numerator=r['k'], denominator=r['n'], unit='per 1,000'))
                    rows.append({'segment': label, 'measure': f'{noun} per 1,000 patients', 'value': round(rate, 1), 'patients': r['n'], 'events': r['k']})
        return dict(headline=f"{segs[0][0]} versus {segs[1][0]} on " + ', '.join(params['measures']), facts=facts, table=rows,
                    read_back=f"{segs[0][0]} compared with {segs[1][0]} (living patients) on {', '.join(params['measures'])}; labs are latest results, utilization is the last 12 months.",
                    notes=["Segments are the patient's line of business or social-vulnerability band; diabetics are adults with a latest A1c; utilization is all-source ED visits / inpatient admissions in the last 12 months."])


def seg_in_patient(column, values):
    col = {'line_of_business': 'p.line_of_business', 'product': 'p.line_of_business'}[column]
    mapped = {'medicare_advantage': 'medicare', 'dual_ma': 'dual'}
    vals = sorted({mapped.get(v, v) for v in values})
    return f"{col} IN ({','.join(q(v) for v in vals)})"


@family
class FrequentUsers:
    NAME = 'frequent_users'
    WORDS = set('''who are our frequent users and how much of volume do they drive'''.split())

    @classmethod
    def parse(cls, text, catalog):
        t = text.strip().rstrip('?.')
        if not re.match(r'^who\s+are\s+our\s+frequent\s+ed\s+users\b', t, re.I): return None
        return {'params': {'minimum': 3}, 'quotes': [span(t, r'frequent\s+ed\s+users'), span(t, r'ed\s+volume') or '']}

    @classmethod
    def run(cls, ctx, params):
        n = params['minimum']
        ids = ctx.ids(ctx.events('ed_visit', n))
        total = ctx.one(f"SELECT COUNT(*) FROM utilization_event u JOIN patient p ON p.patient_id = u.patient_id WHERE u.kind = 'ed_visit' AND {ctx.in_window('u.event_date')} AND {ctx.living}", 0)
        mine = ctx.one(f"SELECT COUNT(*) FROM utilization_event u JOIN patient p ON p.patient_id = u.patient_id WHERE u.kind = 'ed_visit' AND {ctx.in_window('u.event_date')} AND {ctx.living} "
                       f"AND {ctx.events('ed_visit', n)}", 0)
        hf = ctx.count(ctx.events('ed_visit', n), ctx.cond('heart_failure'))
        lobs = ctx.rows(f"SELECT {ctx.lob} AS lob, COUNT(*) AS patients FROM patient p WHERE {ctx.living} AND {ctx.events('ed_visit', n)} GROUP BY lob ORDER BY patients DESC, lob")
        facts = [fact('n', f"{len(ids)} patients had {n} or more ED visits in the last 12 months.", value=len(ids), unit='patients'),
                 fact('visits', f"They account for {mine:,} of {total:,} ED visits ({pct(mine / total, 0):.0f}%) = {mine / total:.4f}.", value=pct(mine / total), numerator=mine, denominator=total, unit='%'),
                 fact('hf', f"{hf} of them have heart failure.", value=hf, unit='patients'),
                 fact('lob', f"Largest line of business among them: {lobs[0]['lob']} ({lobs[0]['patients']} of {len(ids)}).", value=lobs[0]['lob'])]
        return dict(headline=f"{len(ids)} frequent ED users ({n}+ visits in 12 months) drive {pct(mine / total, 0):.0f}% of ED visits", facts=facts, patient_ids=ids, cohort_role='listed',
                    table=[{'line_of_business': r['lob'], 'patients': r['patients']} for r in lobs],
                    read_back=f"Living patients with {n} or more all-source ED visits in the 12 months to {ctx.as_of}, their share of all ED visits, heart-failure overlap and line of business.",
                    notes=[f'"Frequent ED user" is read as {n} or more ED visits in the last 12 months; the governed repeat-user metric uses 2 or more.'])


@family
class PdcShare:
    NAME = 'pdc_share'
    WORDS = set('''how adherent are patients on in share with pdc at least adherence what is the of'''.split())

    @classmethod
    def parse(cls, text, catalog):
        from .drug_classes import CLASSES, GROUPS
        t = text.strip().rstrip('?.')
        if not span(t, r'adheren\w*|\bpdc\b|proportion\s+of\s+days\s+covered'): return None
        names = {**{k.replace('_', ' '): [k] for k in CLASSES}, **{k.replace('_', ' '): list(v) for k, v in GROUPS.items()},
                 'ace inhibitors': ['ace_inhibitor'], 'ace inhibitor': ['ace_inhibitor'], 'statins': ['statin'], 'sglt2 inhibitors': ['sglt2_inhibitor'], 'beta blockers': ['beta_blocker'],
                 'arbs': ['arb'], 'anticoagulants': ['anticoagulant'], 'metformin': ['metformin'], 'insulin': ['insulin']}
        found = next(((k, v) for k, v in sorted(names.items(), key=lambda kv: -len(kv[0])) if re.search(r'(?<![\w-])' + re.escape(k) + r's?(?![\w-])', t, re.I)), None)
        if not found: return None
        threshold = 0.8
        th = re.search(r'pdc\s+(?:of\s+)?(?:at\s+least|>=|of)?\s*(0?\.\d+|1(?:\.0)?)', t, re.I)
        if th: threshold = float(th.group(1))
        yrs = years_in(t)
        quotes = [span(t, r'adheren\w*'), span(t, r'\bpdc\b|proportion\s+of\s+days\s+covered') or '', found[0], span(t, r'share') or '', span(t, r'at\s+least') or '', th.group(1) if th else '', *[str(y) for y in yrs]]
        return {'params': {'classes': found[1], 'threshold': threshold, 'year': yrs[-1] if yrs else None}, 'quotes': quotes}

    @classmethod
    def run(cls, ctx, params):
        import sqlite3
        from . import default_definitions as dd
        y = params['year']
        start, end = (f'{y}-01-01', min(f'{y}-12-31', ctx.as_of)) if y else (ctx.start, ctx.end)
        with sqlite3.connect(f'file:{ctx.target}?mode=ro', uri=True) as db:
            from .age import register
            register(db)
            rep = dd.pdc_adherence(db, params['classes'], start, end, ctx.as_of, None, params['threshold'])
        if rep.get('declined'):
            return dict(headline='Adherence cannot be computed from the received data', facts=[fact('declined', '; '.join(rep.get('missing') or []))], read_back='')
        living = set(ctx.ids('1'))
        den = [x for x in rep['denominator_patient_ids'] if x in living]
        num = [x for x in rep['numerator_patient_ids'] if x in living]
        rate = len(num) / len(den) if den else None
        label = ', '.join(params['classes'])
        facts = [fact('rate', f"{pct(rate)}% of patients on {label} with at least two fills in {start[:4] if y else 'the last 12 months'} have a PDC of {params['threshold']} or higher "
                              f"({len(num):,} of {len(den):,}) = {rate:.4f}.", value=pct(rate), numerator=len(num), denominator=len(den), unit='%')]
        return dict(headline=f"{pct(rate)}% adherent (PDC >= {params['threshold']}): {len(num):,} of {len(den):,} patients on {label}", facts=facts, value=pct(rate), answer_type='value',
                    table=[{'drug_classes': label, 'period_start': start, 'period_end': end, 'patients': len(den), 'adherent': len(num), 'percent_adherent': pct(rate)}],
                    read_back=f"PQA proportion of days covered >= {params['threshold']} for {label}, {start} to {end}: living patients with at least 2 fills on distinct dates and a treatment period of at least 91 days; "
                              'days covered from the first fill to the period end, overlapping supply shifted forward.',
                    notes=[dd.DEFINITIONS['pdc_adherence']['basis']])


# ============================================================================================================================
# high-cost claimants
# ============================================================================================================================
@family
class HighCostClaimants:
    NAME = 'high_cost_claimants'
    WORDS = set('''what share of total spend do the high cost claimants account for how many of have in or more paid medical'''.split())
    AMOUNT = r'\(?\$\s?[\d,]+(?:\.\d+)?\s*(?:or\s+more|or\s+higher|\+)?\s*paid(?:\s+in\s+20\d\d)?\)?'

    @classmethod
    def parse(cls, text, catalog):
        t = text.strip().rstrip('?.')
        amount = re.search(cls.AMOUNT, t, re.I)
        claimants = span(t, r'high[- ]cost\s+claimants')
        if not amount or not claimants: return None
        threshold = float(re.search(r'\$\s?([\d,]+(?:\.\d+)?)', amount.group()).group(1).replace(',', ''))
        yrs = years_in(t)
        share = re.match(r'^what\s+share\s+of\s+total\s+(?:20\d\d\s+)?(?:medical\s+)?(?:paid\s+)?spend\s+do\s+the\s+', t, re.I) and span(t, r'account\s+for')
        if share:
            return {'params': {'kind': 'share', 'threshold': threshold, 'year': yrs[0]}, 'quotes': [share.group() if hasattr(share, 'group') else share, claimants, amount.group(), *[str(y) for y in yrs], span(t, r'account\s+for') or '']}
        has = re.match(r'^how\s+many\s+of\s+the\s+(?:20\d\d\s+)?high[- ]cost\s+claimants\s*(?:' + cls.AMOUNT + r')?\s*have\s+(.+)$', t, re.I)
        if has:
            tail = re.sub(r'\s*' + cls.AMOUNT, '', has.group(1), flags=re.I)
            cond = next(((span(has.group(1), p), c) for p, c in CONDITION_WORDS if span(has.group(1), p)), None)
            if not cond: return None
            return {'params': {'kind': 'with', 'threshold': threshold, 'year': yrs[0], 'condition': cond[1]},
                    'quotes': [span(t, r'how\s+many\s+of\s+the'), claimants, amount.group(), cond[0], 'have', *[str(y) for y in yrs]]}
        return None

    @classmethod
    def run(cls, ctx, params):
        y, cents = params['year'], int(params['threshold'] * 100)
        claim = f"c.counts_in_metrics = 1 AND substr(c.service_from, 1, 4) = {q(str(y))}"
        claimants = (f"SELECT c.patient_id FROM payer_claim c JOIN patient p ON p.patient_id = c.patient_id WHERE {claim} AND {ctx.living} "
                     f"GROUP BY c.patient_id HAVING SUM(c.paid_cents) >= {cents}")
        notes = [f'High-cost claimants: living patients whose summed paid dollars on counted medical claims in {y} reach ${params["threshold"]:,.0f}.']
        if params['kind'] == 'share':
            part = ctx.one(f"SELECT COALESCE(SUM(c.paid_cents), 0) FROM payer_claim c WHERE {claim} AND c.patient_id IN ({claimants})", 0)
            total = ctx.one(f"SELECT COALESCE(SUM(c.paid_cents), 0) FROM payer_claim c WHERE {claim}", 0)
            n = ctx.one(f"SELECT COUNT(*) FROM ({claimants})", 0)
            facts = [fact('share', f"The {n} high-cost claimants account for {pct(part / total)}% of total {y} medical paid spend ({usd(part / 100)} of {usd(total / 100)}) = {part / total:.4f}.",
                          value=pct(part / total), numerator=part / 100, denominator=total / 100, unit='%', claimants=n)]
            return dict(headline=f"{n} high-cost claimants account for {pct(part / total)}% of {y} medical paid spend", facts=facts, value=pct(part / total), answer_type='value',
                        table=[{'high_cost_claimants': n, 'paid_dollars': round(part / 100, 2), 'total_paid_dollars': round(total / 100, 2), 'share_percent': pct(part / total)}],
                        read_back=f"Share of total {y} counted medical paid spend that comes from patients with ${params['threshold']:,.0f} or more paid in {y}.", notes=notes)
        k = ctx.count(f"p.patient_id IN ({claimants})", ctx.cond(params['condition']))
        n = ctx.one(f"SELECT COUNT(*) FROM ({claimants})", 0)
        facts = [fact('count', f"{k} of the {n} high-cost claimants in {y} have {params['condition'].replace('_', ' ')}.", value=k, numerator=k, denominator=n, unit='patients')]
        return dict(headline=f"{k} of {n} high-cost claimants have {params['condition'].replace('_', ' ')}", facts=facts, count=k, answer_type='count',
                    read_back=f"High-cost claimants ({y}, ${params['threshold']:,.0f} or more paid) who have {params['condition'].replace('_', ' ')} (canonical condition evidence).", notes=notes)


# ============================================================================================================================
# PMPM by a member attribute
# ============================================================================================================================
PMPM_GROUPS = [(r'lines?\s+of\s+business|payer\s+types?|\blob\b', 'line_of_business', 'line of business'), (r'products?', 'product', 'product'),
               (r'payers?', 'payer_id', 'payer'), (r'practices?', 'attribution_practice_id', 'attributed practice')]


@family
class PmpmBy:
    NAME = 'pmpm_by'
    WORDS = set('''what was is were the medical claims paid allowed pmpm per member month in by which have has highest lowest'''.split())

    @classmethod
    def parse(cls, text, catalog):
        t = text.strip().rstrip('?.')
        ranked = re.match(r'^which\s+(' + '|'.join(p for p, _, _ in PMPM_GROUPS) + r')\s+(?:have|has)\s+the\s+(highest|lowest|greatest|largest|most|least)\s+', t, re.I)
        if ranked and span(t, r'pmpm|per\s+member\s+per\s+month'):
            group = next((span(ranked.group(1), p), c, label) for p, c, label in PMPM_GROUPS if re.fullmatch(p, ranked.group(1), re.I))
            basis = 'allowed' if span(t, r'allowed') else 'paid'
            direction = 'asc' if ranked.group(2).lower() in ('lowest', 'least') else 'desc'
            return {'params': {'group': group[1], 'label': group[2], 'basis': basis, 'year': (years_in(t) or [None])[-1], 'direction': direction},
                    'quotes': [ranked.group(), span(t, r'pmpm|per\s+member\s+per\s+month'), span(t, r'medical(?:\s+claims)?') or '', span(t, r'paid|allowed') or '', *[str(y) for y in years_in(t)]]}
        if not re.match(r"^(?:what(?:'s|\s+is|\s+was|\s+were)|show(?:\s+me)?|give\s+me)\b", t, re.I): return None
        metric = span(t, r'pmpm|per\s+member\s+per\s+month')
        if not metric: return None
        group = next(((span(t, p), c, label) for p, c, label in PMPM_GROUPS if re.search(r'\bby\s+(?:' + p + r')', t, re.I)), None)
        if not group: return None
        basis = 'allowed' if span(t, r'allowed') else 'paid'
        by = re.search(r'\bby\s+' + group[0], t, re.I)
        return {'params': {'group': group[1], 'label': group[2], 'basis': basis, 'year': (years_in(t) or [None])[-1]},
                'quotes': [metric, by.group() if by else group[0], span(t, r'medical(?:\s+claims)?') or '', span(t, r'paid|allowed') or '', *[str(y) for y in years_in(t)]]}

    @classmethod
    def run(cls, ctx, params):
        y = params['year'] or last_full_year(ctx)
        notes = [] if params['year'] else [FULL_YEAR_NOTE.format(y=y, a=ctx.as_of[:4])]
        col = params['group']
        amount = 'paid_cents' if params['basis'] == 'paid' else 'allowed_cents'
        rows = ctx.rows(
            f"WITH mm AS (SELECT m.{col} AS grp, COUNT(*) AS member_months FROM member_month m WHERE substr(m.month, 1, 4) = {q(str(y))} GROUP BY m.{col}), "
            f"paid AS (SELECT m.{col} AS grp, SUM(c.{amount}) AS cents FROM payer_claim c JOIN member_month m ON m.patient_id = c.patient_id AND m.month = substr(c.service_from, 1, 7) "
            f"WHERE c.counts_in_metrics = 1 AND substr(c.service_from, 1, 4) = {q(str(y))} GROUP BY m.{col}) "
            f"SELECT mm.grp AS grp, mm.member_months AS member_months, COALESCE(paid.cents, 0) AS cents, COALESCE(paid.cents, 0) / 100.0 / mm.member_months AS pmpm "
            f"FROM mm LEFT JOIN paid USING (grp) WHERE mm.grp IS NOT NULL ORDER BY pmpm {'ASC' if params.get('direction') == 'asc' else 'DESC'}, grp")
        total = ctx.rows(f"SELECT COUNT(*) AS member_months FROM member_month m WHERE substr(m.month, 1, 4) = {q(str(y))}")[0]['member_months']
        facts = [fact(f"g_{r['grp']}", f"{r['grp']}: ${r['pmpm']:,.2f} {params['basis']} PMPM in {y} (${r['cents'] / 100:,.0f} over {r['member_months']:,} member months).", value=round(r['pmpm'], 2),
                      numerator=round(r['cents'] / 100, 2), denominator=r['member_months'], unit='USD per member month') for r in rows]
        return dict(headline=f"{params['basis'].capitalize()} medical PMPM in {y} by {params['label']}: {'lowest' if params.get('direction') == 'asc' else 'highest'} {rows[0]['grp']} (${rows[0]['pmpm']:,.0f})", facts=facts,
                    table=[{params['label']: r['grp'], 'member_months': r['member_months'], f"{params['basis']}_dollars": round(r['cents'] / 100, 2), 'pmpm_dollars': round(r['pmpm'], 2)} for r in rows],
                    read_back=f"{params['basis'].capitalize()} medical PMPM for {y} by {params['label']}: counted payer-adjudicated claims in an enrolled member month, grouped by that month's {params['label']}, over the group's member months.",
                    notes=notes + ['A claim counts in the group of the member month it was incurred in; PMPM = dollars / member months of the group (the governed rule).'])


# ============================================================================================================================
# a follow-up about the patients of the previous answer ("what share of total spend do they account for?")
# ============================================================================================================================
@family
class CohortSpend:
    NAME = 'cohort_spend'
    BOUND = True  # only with a saved cohort: "they" / "these" / "those" are the previous answer's patients
    WORDS = set('''what share of total medical paid spend do they account for how much did cost in these those'''.split())

    @classmethod
    def parse(cls, text, catalog):
        original = text.strip().rstrip('?.')
        from . import decision_context as DC
        rolling = DC.ROLLING_YEAR.search(original)
        if rolling and original[rolling.end():].strip(): return None
        t = original[:rolling.start()].rstrip() if rolling else original
        if rolling and years_in(t): return None
        pronoun = r'(?:they|these|those)'
        share = re.match(r'^what\s+share\s+of\s+total\s+(?:(20\d\d)\s+)?(?:medical\s+)?(?:paid\s+)?spend\s+do\s+' + pronoun + r'\s+account\s+for(?:\s+in\s+(20\d\d))?$', t, re.I)
        if share:
            year = int(share[1] or share[2]) if (share[1] or share[2]) else None
            return {'params': {'kind': 'share', 'year': year, **({'rolling_12_months': True} if rolling else {})}, 'quotes': [original]}
        cost = re.match(r'^how\s+much\s+(?:did|do|have)\s+' + pronoun + r'\s+(?:cost|spend|account\s+for)(?:\s+in\s+(20\d\d))?$', t, re.I)
        if cost:
            return {'params': {'kind': 'total', 'year': int(cost[1]) if cost[1] else None, **({'rolling_12_months': True} if rolling else {})}, 'quotes': [original]}
        return None

    @classmethod
    def run(cls, ctx, params):
        ids = list(ctx.population or [])
        y = params['year'] or last_full_year(ctx)
        notes = [] if params['year'] else [FULL_YEAR_NOTE.format(y=y, a=ctx.as_of[:4])]
        marks = ','.join(q(i) for i in ids) or "''"
        if params.get('rolling_12_months'):
            if params.get('year') is not None: raise Declined('State one period for the saved cohort measure')
            from .decision_context import rolling_year
            start,end=rolling_year(ctx.as_of)
            base=f"c.counts_in_metrics = 1 AND c.service_from >= {q(start)} AND c.service_from <= {q(end)}"
            y=f'the 12 months ending {end}'
            notes=[]
        else:
            base = f"c.counts_in_metrics = 1 AND substr(c.service_from, 1, 4) = {q(str(y))}"
        part = ctx.one(f"SELECT COALESCE(SUM(c.paid_cents), 0) FROM payer_claim c WHERE {base} AND c.patient_id IN ({marks})", 0)
        total = ctx.one(f"SELECT COALESCE(SUM(c.paid_cents), 0) FROM payer_claim c WHERE {base}", 0)
        if params['kind'] == 'total':
            facts = [fact('paid', f"The {len(ids):,} patients paid {usd(part / 100)} on counted medical claims in {y}.", value=round(part / 100, 2), unit='USD')]
            return dict(headline=f"{len(ids):,} patients: {usd(part / 100)} paid in {y}", facts=facts, value=round(part / 100, 2), answer_type='value',
                        read_back=f'Counted medical paid dollars in {y} for the saved patients of the previous answer.', notes=notes)
        facts = [fact('share', f"The {len(ids):,} patients account for {pct(part / total)}% of total {y} medical paid spend ({usd(part / 100)} of {usd(total / 100)}) = {part / total:.4f}.",
                      value=pct(part / total), numerator=part / 100, denominator=total / 100, unit='%')]
        return dict(headline=f"{len(ids):,} patients account for {pct(part / total)}% of {y} medical paid spend", facts=facts, value=pct(part / total), answer_type='value',
                    table=[{'patients': len(ids), 'paid_dollars': round(part / 100, 2), 'total_paid_dollars': round(total / 100, 2), 'share_percent': pct(part / total)}],
                    read_back=f'Share of total {y} counted medical paid spend that comes from the saved patients of the previous answer.', notes=notes)
