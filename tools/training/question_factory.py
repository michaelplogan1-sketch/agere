"""Combinatorial question factory over the ACTIVE synthetic warehouse (config/agere/active_warehouse.json).

Each item is one canonical question with its canonical structured request and 3-10 natural paraphrases:
  * grammar families: the canonical wording is precise English the shared planner's free parser reads completely; the
    canonical request is that parser's typed request / request_program (resolved here with --resolve, else by label.py);
  * typed families: the typed request is built directly here, every quote bound to a literal span of the canonical
    wording, so the shared compiler accepts it (lab thresholds, rankings and received-table aggregates the grammar
    does not read yet: these teach the local typed interpreter what only the hosted one answers today);
  * slot families: cohort-family slots (bottled.canonical_question / spec_from_slots) for the narrow typed rung.
Entities, measures (governed metrics, the measure vocabulary, VBC library seeds), filters (concepts, value thresholds,
periods, demographics, payer), groupings, rankings, comparisons, trends, follow-up chains and engine questions.
Paraphrases are template + lexical variation and must keep numbers, negation, qualifiers and concepts
(local_normalizer.guard). An optional LLM paraphrase hook runs only with --llm-paraphrase AND an API key.
Generation is a deterministic stream: item i depends only on (seed, i); duplicates are skipped. Synthetic data only.

Usage: python tools/training/question_factory.py --n 1000000 --seed 1 --out artifacts/training/questions [--shard-size 50000]
"""
import argparse
import hashlib
import itertools
import json
import os
import random
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools' / 'bottling'))

VERSION = 'question-factory/1'
SCHEMA = 'training-question/1'
DEFAULT_OUT = ROOT / 'artifacts' / 'training'


def stable(*parts):
    return hashlib.sha256('\x1f'.join(map(str, parts)).encode()).hexdigest()


def active_stack():
    from clinical_core.agere.active import stack_path
    return stack_path()


def assert_public(path):
    """Never read sealed truth: no `private` directory below the filesystem root, no sealed-eval tree."""
    parts = Path(path).resolve().parts
    if 'private' in parts[2:] or any('agere-sealed-eval' in p for p in parts):
        raise ValueError(f'refusing to read a private/sealed path: {path}')
    return Path(path)


# ---------------------------------------------------------------------------------------------------------- vocabulary
SUBJECTS = ['patients', 'living patients', 'women', 'men', 'Medicaid patients', 'Medicare patients', 'living Medicaid patients',
            'commercial patients', 'living women']
AGES = ['', ' over 65', ' 65 and older', ' 65+', ' aged 18 to 75', ' aged 50 to 74', ' under 18', ' aged 40 to 64']
CONDITIONS = ['diabetes', 'heart failure', 'CKD', 'chronic kidney disease', 'COPD', 'hypertension', 'asthma', 'depression',
              'atrial fibrillation', 'coronary disease', 'obesity', 'dementia', 'stroke', 'cancer', 'hyperlipidemia',
              'anxiety', 'osteoporosis', 'sleep apnea', 'hypothyroidism', 'anemia', 'prediabetes']
GROUPS = ['', ' by sex', ' by payer', ' by coverage', ' by language']
DEMO_GROUPS = [' by sex', ' by payer', ' by coverage', ' by language', ' by state', ' by race', ' by ethnicity']
DRUGS = ['a statin', 'metformin', 'insulin', 'an SGLT2 inhibitor', 'a beta-blocker', 'an ACE inhibitor or ARB']
FILL_DRUGS = ['a statin', 'metformin', 'insulin', 'statins']
GAPS = [  # (wording, allows a calendar year)
    ('have had no HbA1c test', True), ('had no HbA1c test', True), ('had no eGFR', True), ('had a uACR', True),
    ('had neither an eGFR nor a uACR', True), ('had a PHQ-9 score recorded', True), ('had no LDL result', True),
    ('had no statin fill', True), ('had no beta-blocker fill', True), ('had no metformin fill', True),
    ('did not get a flu shot', False), ('have not had a mammogram', True), ('did not have an annual wellness visit', True),
    ('have ever received a pneumococcal vaccine on record', None), ('have never had a flu shot on record', None),
    ('have screened positive for food insecurity', None), ('have screened positive for transportation needs', None),
    ('have screened for housing', None)]
WINDOWS = [' in the last 12 months', ' in the last 24 months', ' in the last 6 months', ' in the past year']
SOURCES = ['ED visits', 'emergency department visits', 'inpatient admissions', 'hospital admissions', 'claims',
           'medical claims', 'appointments', 'kept appointments', 'pharmacy claims', 'hospital stays', 'prescriptions']
STATUS = [('appointments', 'cancelled'), ('appointments', 'kept'), ('pharmacy claims', 'rejected'), ('pharmacy claims', 'reversed'),
          ('claims', 'denied')]
SPECIALISTS = ['cardiologists', 'nephrologists', 'endocrinologists', 'oncologists', 'neurologists', 'psychiatrists',
               'pediatricians', 'pulmonologists', 'gastroenterologists', 'dermatologists', 'urologists', 'radiologists',
               'hospitalists', 'ophthalmologists']
MONEY = ['allowed dollars', 'paid dollars', 'patient responsibility', 'charge dollars']
UTIL = ['ED visits', 'inpatient admissions', 'hospital admissions']
METRIC_EXCLUDE = ('_', 'measure.', '(', '/')
LABS = {'HbA1c': ('hba1c', [6.5, 7, 8, 9, 10]), 'eGFR': ('egfr', [15, 30, 45, 60]), 'LDL': ('ldl', [70, 100, 130, 160, 190]),
        'uACR': ('uacr', [30, 300]), 'systolic blood pressure': ('systolic_bp', [130, 140, 160, 180]),
        'diastolic blood pressure': ('diastolic_bp', [80, 90, 100]), 'potassium': ('potassium', [3.5, 5.5]),
        'glucose': ('glucose', [126, 200, 300]), 'creatinine': ('creatinine', [1.5, 2, 3]), 'NT-proBNP': ('nt_probnp', [300, 900, 2000]),
        'BMI': None, 'hemoglobin': ('hemoglobin', [8, 10, 12]), 'triglyceride level': ('triglycerides', [150, 200, 500])}
AN = {'HbA1c', 'eGFR', 'LDL', 'NT-proBNP'}
LABS = {k: v for k, v in LABS.items() if v}
LAB_OPS = {'>': ['above', 'over', 'greater than'], '<': ['below', 'under', 'less than'], '>=': ['at least'], '<=': ['at most']}
TYPED_SUBJECTS = {'patients': [], 'women': [('patient.sex_reported', '=', 'F', 'women')], 'men': [('patient.sex_reported', '=', 'M', 'men')],
                  'Medicaid patients': [('patient.coverage_type', '=', 'medicaid', 'Medicaid')],
                  'Medicare patients': [('patient.coverage_type', '=', 'medicare', 'Medicare')]}
TYPED_AGES = {'': [], ' over 65': [('patient.age', '>', 65, 'over 65')], ' 65 and older': [('patient.age', '>=', 65, '65 and older')],
              ' under 18': [('patient.age', '<', 18, 'under 18')],
              ' aged 18 to 75': [('patient.age', '>=', 18, 'aged 18 to 75'), ('patient.age', '<=', 75, 'aged 18 to 75')]}
TYPED_CONDITIONS = {'diabetes': 'diabetes', 'heart failure': 'heart_failure', 'CKD': 'chronic_kidney_disease',
                    'hypertension': 'hypertension', 'COPD': 'copd', 'asthma': 'asthma', 'atrial fibrillation': 'atrial_fibrillation',
                    'coronary disease': 'coronary_artery_disease'}
# Received tables for the directly typed aggregate family, with literal nouns. Tables whose business convention differs
# from "all received rows" (payer claims, measurements) are named as records so no governed convention is taught wrongly.
TYPED_TABLES = {'appointment': 'appointment records', 'clinician': 'clinician records', 'organization': 'organization records',
                'immunization': 'immunization records', 'prescription': 'prescription records', 'visit': 'visit records',
                'stay': 'hospital stay records', 'utilization_event': 'utilization event records',
                'social_need': 'social need screening records', 'enrollment_span': 'enrollment span records',
                'member_month': 'member month records', 'adt_visit': 'ADT visit records', 'clinical_note': 'clinical note records',
                'allergy_entry': 'allergy entry records', 'problem_list_entry': 'problem list entry records',
                'medication_list_entry': 'medication list entry records', 'cancer_screening': 'cancer screening records',
                'fill': 'medication fill records', 'death': 'death records', 'wellness_visit': 'wellness visit records',
                'hie_document': 'HIE document records', 'scanned_document': 'scanned document records',
                'payer_claim': 'payer claim records', 'payer_claim_line': 'payer claim line records',
                'payer_pharmacy_claim': 'payer pharmacy claim records', 'claim': 'provider claim records',
                'procedure_line': 'procedure line records', 'pharmacy_claim_event': 'pharmacy claim event records',
                'smoking_status': 'smoking status records', 'incident_diagnosis': 'incident diagnosis records',
                'payer_adjudication': 'payer adjudication records', 'sdoh_vendor': 'SDOH vendor records',
                'note_therapy_change': 'note therapy change records', 'therapy_barrier': 'therapy barrier records'}
CLOCKS = ('event_date', 'service_date', 'service_from', 'fill_date', 'scheduled_date', 'admit_date', 'ordered_date',
          'coverage_start', 'signed_date', 'death_date', 'diagnosis_date', 'delivery_date', 'adjudication_date', 'status_date',
          'first_recorded', 'first_listed', 'month')


class Vocab:
    """Everything the families draw from, read once from the active warehouse (read-only)."""

    def __init__(self, stack=None, out=None):
        from clinical_core.agere.clinical import request_plan as R, request_vocabulary as V
        self.stack = assert_public(stack or active_stack())
        self.catalog = R.Catalog.load(self.stack)
        self.as_of = self.catalog.identity.get('as_of') or '2030-05-27'
        last = int(self.as_of[:4]) - 1
        self.years = [str(y) for y in range(last - 2, last + 1)]
        self.metrics = sorted({a for a in V.aliases() if not any(x in a for x in METRIC_EXCLUDE)})
        self.concept_versions = {k: v['version'] for k, v in V.concept_definitions()['condition'].items()}
        vbc = json.loads((ROOT / 'config' / 'agere' / 'vbc_questions.json').read_text())
        self.vbc = [q for q in vbc['questions'] if q.get('question')]
        self.out = Path(out or DEFAULT_OUT)
        self._profile = None

    @property
    def profile(self):
        if self._profile is None: self._profile = table_profile(self.catalog, self.out)
        return self._profile


def table_profile(catalog, out):
    """Low-cardinality categorical values, numeric quantiles and event clocks per typed table, from a bounded sample.
    Cached under <out>/profile-<source digest>.json."""
    digest = stable(catalog.identity.get('source_digest'), VERSION)[:16]
    cache = Path(out) / f'profile-{digest}.json'
    if cache.exists(): return json.loads(cache.read_text())
    from clinical_core.agere.clinical.request_plan import identifier
    result = {}
    with sqlite3.connect(f'file:{catalog.target}?mode=ro', uri=True) as db:
        for table in TYPED_TABLES:
            cols = [c for c in catalog.tables.get(table, []) if c != 'age']
            if not cols: continue
            rows = db.execute(f'SELECT {",".join(map(identifier, cols))} FROM {identifier(table)} LIMIT 20000').fetchall()
            if len(rows) < 20: continue
            cats, nums, dates = {}, {}, []
            for i, col in enumerate(cols):
                values = [r[i] for r in rows if r[i] is not None]
                if len(values) < 20 or col.endswith('_id') or col in ('record_id', 'npi', 'ndc', 'payload', 'record_ids') or 'npi' in col:
                    continue
                if all(isinstance(v, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}.*', v) for v in values[:200]):
                    dates.append(col); continue
                if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
                    distinct = sorted(set(values))
                    if len(distinct) > 12:
                        q = [distinct[int(len(distinct) * p)] for p in (0.25, 0.5, 0.75, 0.9)]
                        nums[col] = sorted({round(v) if abs(v) >= 10 else round(v, 1) for v in q})
                    continue
                if all(isinstance(v, str) for v in values):
                    counts = {}
                    for v in values: counts[v] = counts.get(v, 0) + 1
                    if 2 <= len(counts) <= 25 and all(len(v) <= 40 and "'" not in v and '\n' not in v for v in counts):
                        cats[col] = [v for v, n in sorted(counts.items(), key=lambda x: -x[1]) if n >= max(3, len(values) // 400)][:12]
            clock = next((c for c in CLOCKS if c in dates), None)
            result[table] = {'noun': TYPED_TABLES[table], 'categorical': {k: v for k, v in cats.items() if v},
                             'numeric': nums, 'clock': clock, 'patient': 'patient_id' in cols}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result, indent=1, sort_keys=True))
    return result


# --------------------------------------------------------------------------------------------- typed request helpers
def P(field, op, value, quote): return {'op': op, 'field': field, 'value': value, 'quote': quote}
def AND(args, quote): return args[0] if len(args) == 1 else {'op': 'and', 'args': args, 'quote': quote}
def EXISTS(table, where, quote): return {'op': 'exists', 'table': table, 'where': where, 'quote': quote}
def YEAR(field, year, quote): return [P(field, '>=', f'{year}-01-01', quote), P(field, '<', f'{int(year) + 1}-01-01', quote)]


def REQ(table, quote, select, where=None, group_by=(), order_by=(), limit=None):
    return {'source': {'table': table, 'quote': quote}, 'select': select, 'where': where, 'group_by': list(group_by),
            'order_by': list(order_by), 'limit': limit, 'unresolved': []}


def words(col): return col.replace('_', ' ')


def num_text(v): return str(int(v)) if float(v).is_integer() else str(v)


# ------------------------------------------------------------------------------------------------------ the families
def cond_clause(rng, conds=CONDITIONS):
    shape = rng.random()
    a, b = rng.sample(conds, 2)
    if shape < 0.7: return a, 'one'
    if shape < 0.85: return f'{a} and {b}', 'both'
    return f'{a} or {b}', 'either'


def f_condition_count(rng, v):
    s, a, g = rng.choice(SUBJECTS), rng.choice(AGES), rng.choice(GROUPS)
    c, shape = cond_clause(rng)
    form = rng.choice(['have', 'with_are_there'])
    q = {'have': f'How many {s}{a} have {c}{g}?', 'with_are_there': f'How many {s}{a} with {c} are there{g}?'}[form]
    return {'form': f'{form}/{shape}{"/grouped" if g else ""}', 'canonical': q, 'slots': {'subject': s, 'age': a, 'condition': c, 'group': g}}


def f_condition_list(rng, v):
    s, a = rng.choice(SUBJECTS), rng.choice(AGES)
    c, shape = cond_clause(rng)
    form = rng.choice(['which', 'list'])
    q = f'Which {s}{a} have {c}?' if form == 'which' else f'List {s}{a} with {c}'
    return {'form': f'{form}/{shape}', 'canonical': q, 'slots': {'subject': s, 'age': a, 'condition': c}}


def f_condition_combo(rng, v):
    a, b = rng.sample(CONDITIONS[:12], 2)
    form = rng.choice(['both', 'plus', 'not', 'or', 'but_not'])
    q = {'both': f'How many patients have both {a} and {b}?', 'plus': f'How many patients have {a} plus {b}?',
         'not': f'How many patients with {a} do not have {b}?', 'or': f'How many {rng.choice(["patients", "living patients"])} with {a} or {b} are there?',
         'but_not': f'How many patients have {a} but not {b}?'}[form]
    return {'form': form, 'canonical': q, 'slots': {'a': a, 'b': b}}


def f_care_gap(rng, v):
    s = rng.choice(['patients', 'living patients', 'women', 'Medicaid patients'])
    a = rng.choice(['', ' 65 and older', ' aged 50 to 74', ' aged 18 to 75'])
    c = rng.choice(CONDITIONS[:10] + [None])
    gap, yearly = rng.choice(GAPS)
    tail = [''] if yearly is None else ([f' in the {y}-{int(y) + 1} season' for y in v.years[-2:]] if gap.endswith('flu shot') and yearly is False
                                       else WINDOWS + ([f' in {y}' for y in v.years[-2:]] if yearly else []))
    w = rng.choice(tail)
    lead = rng.choice(['How many', 'Which'])
    q = f'{lead} {s}{a}{" with " + c if c else ""} {gap}{w}?'
    return {'form': f'{lead.split()[0].lower()}/{re.sub(r"[^a-z]+", "_", gap)[:30]}/{"window" if "last" in w or "past" in w else "year" if w else "none"}',
            'canonical': q, 'slots': {'subject': s, 'age': a, 'condition': c, 'gap': gap, 'window': w}}


def f_utilization(rng, v):
    s, a = rng.choice(['patients', 'women', 'men', 'Medicaid patients', 'Medicare patients']), rng.choice(['', ' over 65', ' 65 and older'])
    c = rng.choice([None, *CONDITIONS[:8]])
    period = rng.choice([f' in {y}' for y in v.years] + [' in the last 6 months', ' in the last 12 months'])
    form = rng.choice(['any', 'threshold'])
    cond = f' with {c}' if c else ''
    if form == 'any':
        q = f'How many {s}{a}{cond} had an ED visit{period}?'
    else:
        q = f'How many {s}{a}{cond} had {rng.choice([2, 3, 4, 5])} or more {rng.choice(UTIL)}{period}?'
    return {'form': form, 'canonical': q, 'slots': {'subject': s, 'age': a, 'condition': c, 'period': period}}


def f_medication(rng, v):
    s = rng.choice(['patients', 'women', 'men', 'Medicaid patients'])
    c = rng.choice([None, 'diabetes', 'heart failure', 'CKD', 'hypertension', 'coronary disease'])
    form = rng.choice(['filled', 'on'])
    cond = f' with {c}' if c else ''
    if form == 'filled':
        q = f'How many {s}{cond} filled {rng.choice(FILL_DRUGS)} in {rng.choice(v.years)}?'
    else:
        q = f'How many {s} with {c or "diabetes"} are on {rng.choice(["metformin", "insulin", "a statin", "statins"])}?'
    return {'form': form, 'canonical': q, 'slots': {'subject': s, 'condition': c}}


def f_demographics(rng, v):
    s, a = rng.choice(['patients', 'women', 'men', 'Medicaid patients', 'Medicare patients', 'living patients']), rng.choice(AGES)
    form = rng.choice(['count', 'grouped', 'avg_age'])
    if form == 'count': q = f'How many {s}{a} are there?'
    elif form == 'grouped': q = f'How many {s}{a} are there{rng.choice(DEMO_GROUPS)}?'
    else: q = f'What is the average age of {rng.choice(["patients", "women", "men"])} with {rng.choice(CONDITIONS[:10])}?'
    return {'form': form, 'canonical': q, 'slots': {'subject': s, 'age': a}}


def f_governed_metric(rng, v):
    m, y = rng.choice(v.metrics), rng.choice(v.years)
    form = rng.choice(['year', 'payer_type', 'grouped', 'year_first'])
    q = {'year': f'What was the {m} in {y}?', 'payer_type': f'What was the {m} in {y} for {rng.choice(["Medicaid", "Medicare", "commercial"])} patients?',
         'grouped': f'What was the {m} in {y} by {rng.choice(["payer", "sex", "coverage"])}?', 'year_first': f'What was the {y} {m}?'}[form]
    return {'form': form, 'canonical': q, 'slots': {'metric': m, 'year': y}}


def f_comparison(rng, v):
    m = rng.choice(v.metrics)
    a, b = rng.choice(list(zip(v.years, v.years[1:])))
    form = rng.choice(['change', 'up_down', 'vs'])
    q = {'change': f'How did {m} change from {a} to {b}?', 'up_down': f'Compared with {a}, did the {b} {m} go up or down?',
         'vs': f'{m} in {b} vs {a}'}[form]
    return {'form': form, 'canonical': q, 'slots': {'metric': m, 'years': [a, b]}}


def f_source_count(rng, v):
    y = rng.choice(v.years)
    form = rng.choice(['year', 'status', 'specialists', 'trend', 'by_payer', 'fixed'])
    if form == 'year': q = f'How many {rng.choice(SOURCES)} were there in {y}?'
    elif form == 'status':
        src, st = rng.choice(STATUS); q = f'How many {src} were {st} in {y}?'
    elif form == 'specialists': q = f'How many {rng.choice(SPECIALISTS)} are there?'
    elif form == 'trend': q = f'Show {rng.choice(["ED visits", "inpatient admissions", "appointments"])} by month for {y}'
    elif form == 'by_payer': q = f'How many {rng.choice(["ED visits", "claims", "pharmacy claims"])} were there in {y} by payer?'
    else: q = rng.choice(['How many prescriptions were written but never filled?', 'How many patients have an assigned PCP?'])
    return {'form': form, 'canonical': q, 'slots': {'year': y}}


def f_money(rng, v):
    m, y = rng.choice(MONEY), rng.choice(v.years)
    form = rng.choice(['total', 'by_payer', 'by_month', 'payer_type'])
    q = {'total': f'What were total {m} on {y} medical claims?', 'by_payer': f'What were total {m} on {y} medical claims by payer?',
         'by_month': f'What were total {m} on {y} medical claims by month?',
         'payer_type': f'What were total {m} on {y} medical claims for {rng.choice(["Medicaid", "Medicare"])} patients?'}[form]
    return {'form': form, 'canonical': q, 'slots': {'money': m, 'year': y}}


def f_sequence(rng, v):
    form = rng.choice(['start_after_discharge', 'pcp_followup', 'followup_week', 'ed_after_discharge', 'admit_before_death'])
    y, n = rng.choice(v.years), rng.choice([7, 14, 30, 60, 90])
    if form == 'start_after_discharge':
        q = f'How many {rng.choice(["heart failure", "diabetes", "CKD", "COPD"])} patients started {rng.choice(DRUGS)} within {rng.choice([30, 60, 90])} days after a hospital discharge?'
    elif form == 'pcp_followup': q = f'Of inpatient discharges in {y}, how many had a primary care visit within {rng.choice([7, 14, 30])} days after discharge?'
    elif form == 'followup_week': q = f'How many {y} hospital discharges got a primary care follow-up within a week?'
    elif form == 'ed_after_discharge': q = f'How many patients{rng.choice(["", " with diabetes", " with heart failure"])} had an ED visit within {rng.choice([7, 30, 90])} days after a hospital discharge?'
    else: q = f'How many patients who died had an inpatient admission in the {rng.choice([7, 30, 90])} days before death?'
    return {'form': form, 'canonical': q, 'slots': {'year': y, 'days': n}}


def f_ranking(rng, v):
    y, n = rng.choice(v.years), rng.choice(['three', 'five', '10'])
    form = rng.choice(['denials', 'denials_year', 'symptoms', 'rejects'])
    q = {'denials': f'What are the top {n} denial reason codes across all medical claims?',
         'denials_year': f'What are the top {n} denial reason codes across all medical claims in {y}?',
         'symptoms': f'What are the top {rng.choice(["three", "five"])} symptoms in notes in {y}?',
         'rejects': rng.choice(['What is the most common reject code on pharmacy claims?', 'What are the top five reject codes on pharmacy claims?',
                                'What is the most frequently documented symptom in notes (affirmed, not negated)?'])}[form]
    return {'form': form, 'canonical': q, 'slots': {'year': y, 'n': n}}


def f_followup_chain(rng, v):
    """A base question, then a conversational follow-up; the canonical is the complete standalone question."""
    a, g0 = rng.choice(['', ' over 65', ' 65 and older']), ''
    c, _ = cond_clause(rng, CONDITIONS[:12])
    base = f'How many patients{a} have {c}?'
    kind = rng.choice(['sex', 'group', 'payer_type'])
    if kind == 'sex':
        who = rng.choice(['women', 'men'])
        follow = rng.choice([f'What about just {who}?', f'only {who}', f'Now only the {who}', f'and for {who}?'])
        standalone = f'How many {who}{a} have {c}?'
    elif kind == 'group':
        grp = rng.choice(['sex', 'payer', 'coverage', 'language'])
        follow = rng.choice([f'break that down by {grp}', f'by {grp}', f'Can you split that by {grp}?', f'now group it by {grp}'])
        standalone = f'How many patients{a} have {c} by {grp}?'
    else:
        pay = rng.choice(['Medicaid', 'Medicare'])
        follow = rng.choice([f'What about {pay} patients?', f'only {pay}', f'just the {pay} patients', f'and {pay} patients?'])
        standalone = f'How many {pay} patients{a} have {c}?'
    return {'form': kind, 'canonical': standalone, 'chain': {'previous': base, 'followup': follow},
            'slots': {'condition': c, 'age': a, 'kind': kind}}


def f_vbc_seed(rng, v):
    item = rng.choice(v.vbc)
    return {'form': item.get('domain') or 'vbc', 'canonical': item['question'],
            'slots': {'vbc_id': item['id']}, 'expected_engine': item.get('expected_engine')}


# ---- typed families (request built here) ----
def subject_preds(subject, age):
    return [P(*x) for x in TYPED_SUBJECTS[subject] + TYPED_AGES[age]]


def concept_pred(v, phrase, quote):
    name = TYPED_CONDITIONS[phrase]
    return {'op': 'concept', 'kind': 'condition', 'names': [name], 'version': v.concept_versions.get(name, 1), 'period': None, 'quote': quote}


def f_lab_threshold(rng, v):
    lab = rng.choice(sorted(LABS))
    concept, values = LABS[lab]
    op = rng.choice(sorted(LAB_OPS)); word = rng.choice(LAB_OPS[op]); val = rng.choice(values)
    s, a = rng.choice(sorted(TYPED_SUBJECTS)), rng.choice(sorted(TYPED_AGES))
    c = rng.choice([None, None, *sorted(TYPED_CONDITIONS)])
    y = rng.choice([None, *v.years]) if rng.random() < 0.5 else None
    form = rng.choice(['count_ever', 'list_latest', 'count_latest', 'avg_latest'])
    cond_text = f' with {c}' if c else ''
    threshold = f'{lab} {word} {num_text(val)}'
    base = subject_preds(s, a) + ([concept_pred(v, c, f'with {c}')] if c else [])
    if form == 'count_ever':
        period = f' in {y}' if y else ''
        q = f'How many {s}{a}{cond_text} had {"an" if lab in AN else "a"} {threshold}{period}?'
        inner = [P('measurement.concept', '=', concept, lab), P('measurement.value_canonical', op, val, f'{word} {num_text(val)}')]
        if y: inner += YEAR('measurement.event_date', y, f'in {y}')
        where = AND(base + [EXISTS('measurement', AND(inner, threshold + period), threshold + period)], q)
        req = REQ('patient', s.split()[-1] if s != 'patients' else 'patients', [{'kind': 'count_distinct', 'alias': 'patients', 'quote': 'How many', 'field': 'patient.patient_id'}], where)
    elif form in ('list_latest', 'count_latest'):
        lead = 'Which' if form == 'list_latest' else 'How many'
        q = f'{lead} {s}{a}{cond_text} have a latest {threshold}?'
        inner = AND([P('measurement_latest.concept', '=', concept, lab), P('measurement_latest.value_canonical', op, val, f'{word} {num_text(val)}')], threshold)
        where = AND(base + [EXISTS('measurement_latest', inner, f'latest {threshold}')], q)
        sel = ([{'kind': 'field', 'field': 'patient.patient_id', 'alias': 'patient_id', 'quote': 'Which'}] if lead == 'Which' else
               [{'kind': 'count_distinct', 'alias': 'patients', 'quote': 'How many', 'field': 'patient.patient_id'}])
        req = REQ('patient', s.split()[-1] if s != 'patients' else 'patients', sel, where)
    else:
        q = f'What is the average latest {lab} of {s}{a}{cond_text}?'
        preds = [P('measurement_latest.concept', '=', concept, lab)] + base
        req = REQ('measurement_latest', f'latest {lab}', [{'kind': 'avg', 'alias': 'average_' + concept, 'quote': f'average latest {lab}',
                                                           'field': 'measurement_latest.value_canonical'}], AND(preds, q))
        threshold = lab
    return {'form': form + ('/year' if y and form == 'count_ever' else ''), 'canonical': q, 'request': {'kind': 'request', 'value': req},
            'slots': {'lab': lab, 'op': op, 'value': val, 'subject': s, 'age': a, 'condition': c, 'year': y}}


def f_top_conditions(rng, v):
    n = rng.choice([3, 5, 10])
    y = rng.choice([None, *v.years])
    period = f' recorded in {y}' if y else ''
    q = f'What are the top {n} condition concepts by patient count{period}?'
    sel = [{'kind': 'field', 'field': 'condition.concept', 'alias': 'concept', 'quote': 'condition concepts'},
           {'kind': 'count_distinct', 'field': 'condition.patient_id', 'alias': 'patients', 'quote': 'patient count'}]
    where = AND(YEAR('condition.event_date', y, f'recorded in {y}'), f'recorded in {y}') if y else None
    req = REQ('condition', 'condition concepts', sel, where, ['concept'],
              [{'alias': 'patients', 'direction': 'desc', 'quote': f'top {n}'}], {'value': n, 'quote': f'top {n}'})
    return {'form': 'year' if y else 'all', 'canonical': q, 'request': {'kind': 'request', 'value': req}, 'slots': {'n': n, 'year': y}}


def f_table_aggregate(rng, v):
    """Count / distinct patients / sum / average / extreme / top-N over a received table with value, threshold and period
    filters and an optional grouping. Every quote is a literal span of the canonical wording."""
    prof = v.profile
    table = rng.choice(sorted(prof))
    t = prof[table]
    noun, cats, nums, clock = t['noun'], t['categorical'], t['numeric'], t['clock']
    forms = ['count'] + (['patients'] if t['patient'] else []) + (['sum', 'avg', 'max'] if nums else []) + (['top'] if cats else [])
    form = rng.choice(forms)
    filters, preds, used = [], [], set()
    for _ in range(rng.choice([0, 1, 1, 2])):
        kind = rng.choice(['cat', 'num', 'year'])
        if kind == 'cat' and cats:
            col = rng.choice(sorted(set(cats) - used) or [None])
            if not col: continue
            val = rng.choice(cats[col]); used.add(col)
            text = f'{words(col)} {val}'
            filters.append(f' with {text}'); preds.append(P(f'{table}.{col}', '=', val, text))
        elif kind == 'num' and nums:
            col = rng.choice(sorted(set(nums) - used) or [None])
            if not col: continue
            val = rng.choice(nums[col]); used.add(col)
            val = int(val) if float(val).is_integer() else val
            op = rng.choice(['>', '<']); word = rng.choice(LAB_OPS[op])
            text = f'{words(col)} is {word} {num_text(val)}'
            filters.append(f' where {text}'); preds.append(P(f'{table}.{col}', op, val, text))
        elif kind == 'year' and clock and 'year' not in used:
            y = rng.choice(v.years); used.add('year')
            filters.append(f' in {y}'); preds.extend(YEAR(f'{table}.{clock}', y, f'in {y}'))
    tail = ''.join(filters)
    group = None
    if form in ('count', 'patients', 'sum', 'avg') and cats and rng.random() < 0.35:
        group = rng.choice(sorted(set(cats) - used) or [None])
    gtext = f' by {words(group)}' if group else ''
    select, order, limit, groups = [], [], None, []
    if form == 'count':
        q = f'How many {noun}{tail}{gtext}?'
        select.append({'kind': 'count', 'alias': 'count', 'quote': 'How many'})
    elif form == 'patients':
        q = f'How many distinct patients appear in {noun}{tail}{gtext}?'
        select.append({'kind': 'count_distinct', 'alias': 'patients', 'quote': 'How many distinct patients appear in', 'field': f'{table}.patient_id'})
    elif form in ('sum', 'avg', 'max'):
        col = rng.choice(sorted(nums))
        word = {'sum': 'total', 'avg': 'average', 'max': rng.choice(['highest', 'maximum'])}[form]
        rel = 'among' if form == 'max' else 'across'
        q = f'What is the {word} {words(col)} {rel} {noun}{tail}{gtext}?'
        select.append({'kind': form, 'alias': f'{form}_{col}', 'quote': f'{word} {words(col)}' + (' across' if rel == 'across' else ''),
                       'field': f'{table}.{col}'})
        if form == 'max': group, gtext = None, ''
    else:
        col = rng.choice(sorted(set(cats) - used) or sorted(cats))
        n = rng.choice([3, 5, 10])
        q = f'What are the top {n} {words(col)} values by number of {noun}{tail}?'
        select = [{'kind': 'field', 'field': f'{table}.{col}', 'alias': col, 'quote': f'{words(col)} values'},
                  {'kind': 'count', 'alias': 'count', 'quote': f'number of {noun}'}]
        groups, order, limit = [col], [{'alias': 'count', 'direction': 'desc', 'quote': f'top {n}'}], {'value': n, 'quote': f'top {n}'}
    if group:
        select.insert(0, {'kind': 'field', 'field': f'{table}.{group}', 'alias': group, 'quote': words(group)})
        groups = [group]
    where = AND(preds, tail.strip()) if preds else None
    req = REQ(table, noun, select, where, groups, order, limit)
    return {'form': f'{form}/{len(filters)}f{"/g" if group else ""}', 'canonical': q, 'request': {'kind': 'request', 'value': req},
            'slots': {'table': table, 'form': form, 'group': group}, 'template_key': f'{table}/{form}'}


def f_narrow_cohort(rng, v):
    import narrow_data
    from clinical_core.agere.clinical import bottled
    family = rng.choice(['event_followup_gap', 'therapy_class_gap', 'utilization_threshold', 'screening_overdue', 'adherence_pdc'])
    slots = narrow_data.sample_slots(rng, family)
    shape = '/'.join([family, slots.get('ask', ''), *(k for k in ('condition', 'age', 'sex', 'lab', 'anchor', 'event', 'screening', 'mode')
                                                       if k in slots)])
    if 'anchor' in slots: shape += '/' + slots['anchor']
    return {'form': shape, 'canonical': bottled.canonical_question(slots), 'request': {'kind': 'cohort_family_slots', 'value': slots},
            'slots': {'family': family}}


FAMILIES = {  # name -> (weight, generator, request source)
    'condition_count': (14, f_condition_count, 'grammar'), 'condition_list': (5, f_condition_list, 'grammar'),
    'condition_combo': (5, f_condition_combo, 'grammar'), 'care_gap': (12, f_care_gap, 'grammar'),
    'utilization': (6, f_utilization, 'grammar'), 'medication': (5, f_medication, 'grammar'),
    'demographics': (5, f_demographics, 'grammar'), 'governed_metric': (6, f_governed_metric, 'grammar'),
    'comparison': (3, f_comparison, 'grammar'), 'source_count': (5, f_source_count, 'grammar'),
    'money_total': (3, f_money, 'grammar'), 'sequence': (4, f_sequence, 'grammar'), 'ranking': (2, f_ranking, 'grammar'),
    'followup_chain': (4, f_followup_chain, 'grammar'), 'vbc_seed': (1, f_vbc_seed, 'grammar'),
    'lab_threshold': (7, f_lab_threshold, 'typed'), 'top_conditions': (1, f_top_conditions, 'typed'),
    'table_aggregate': (10, f_table_aggregate, 'typed'), 'narrow_cohort': (3, f_narrow_cohort, 'slots'),
}


# ------------------------------------------------------------------------------------------------------ paraphrases
# (style, pattern, alternatives). Each alternative keeps every number, negation, qualifier and concept of the original.
RULES = [
    ('lead', r'^How many ', ['Tell me how many ', 'Can you tell me how many ', 'I need to know how many ', 'Quick question: how many ',
                             'Do you know how many ', 'how many ', 'Roughly how many ', 'Please count how many ']),
    ('lead', r'^Which (.*?)\b(have|had|did|do|were|are|got)\b', [r'Show me the \1who \2', r'Find the \1who \2',
                                                                   r'Who are the \1that \2', r'Pull the \1who \2', r'which \1\2']),
    ('lead', r'^List ', ['Show me ', 'Give me a list of ', 'Pull ', 'Find all ', 'list ']),
    ('lead', r'^What (is|was|were) the ', ['Give me the ', 'Show the ', 'Tell me the ', "What's the ", 'Can I get the ']),
    ('lead', r'^What (is|was|were) ', ['Give me ', 'Show ', 'Tell me ']),
    ('lead', r'^What are the ', ['Show me the ', 'Give me the ', 'List the ']),
    ('lead', r'^Show ', ['Display ', 'Give me ', 'Chart ', 'Break out ']),
    ('subject', r'\bMedicaid patients\b', ['patients on Medicaid', 'Medicaid members', 'Medicaid enrollees']),
    ('subject', r'\bMedicare patients\b', ['patients on Medicare', 'Medicare members', 'Medicare beneficiaries']),
    ('subject', r'\bcommercial patients\b', ['commercially insured patients', 'commercial members']),
    ('subject', r'\bliving patients\b', ['patients who are alive', 'living members', 'patients still living']),
    ('subject', r'(?<!\w)women\b', ['female patients', 'females', 'ladies']),
    ('subject', r'(?<![\w-])men\b', ['male patients', 'males']),
    ('subject', r'(?<!distinct )\bpatients\b(?! appear)', ['members', 'people', 'pts', 'individuals']),
    ('age', r'\bover 65\b', ['older than 65', 'above age 65', 'over age 65']),
    ('age', r'\b65 and older\b', ['65 or older', 'aged 65+', 'age 65 and up', '65 and over']),
    ('age', r'\b65\+', ['65 or older', '65 and up']),
    ('age', r'\baged (\d+) to (\d+)\b', [r'between \1 and \2', r'ages \1-\2', r'\1 to \2 years old', r'aged \1-\2']),
    ('age', r'\bunder 18\b', ['younger than 18', 'below age 18', 'under age 18']),
    ('condition', r'\bdiabetes\b', ['DM', 'T2DM', 'diabetes mellitus']),
    ('condition', r'\bheart failure\b', ['HF', 'CHF', 'congestive heart failure']),
    ('condition', r'\bhypertension\b', ['HTN', 'high blood pressure']),
    ('condition', r'\bCKD\b', ['chronic kidney disease', 'kidney disease']),
    ('condition', r'\bchronic kidney disease\b', ['CKD']),
    ('condition', r'\batrial fibrillation\b', ['afib', 'AF', 'a-fib']),
    ('condition', r'\bcoronary disease\b', ['CAD', 'coronary artery disease']),
    ('condition', r'\bhyperlipidemia\b', ['high cholesterol']),
    ('condition', r'\bdepression\b', ['major depression', 'MDD']),
    ('drug', r'\ba statin\b', ['statin therapy', 'any statin']),
    ('drug', r'\ba beta-blocker\b', ['a beta blocker', 'beta-blocker therapy']),
    ('lab', r'\bHbA1c\b', ['A1c', 'hemoglobin A1c', 'HgbA1c']),
    ('lab', r'\bsystolic blood pressure\b', ['systolic BP', 'SBP']),
    ('lab', r'\bdiastolic blood pressure\b', ['diastolic BP', 'DBP']),
    ('period', r'\bin (20\d\d)\b(?![-\d])', [r'during \1', r'for \1', r'in calendar year \1', r'in CY\1', r'throughout \1']),
    ('period', r'\bin the last (\d+) months\b', [r'over the past \1 months', r'in the past \1 months', r'within the last \1 months',
                                                  r'during the last \1 months']),
    ('period', r'\bin the past year\b', ['over the past year', 'in the last year', 'within the past year']),
    ('group', r'\bby (sex|payer|coverage|language|state|race|ethnicity|month)\b', [r'broken down by \1', r'per \1', r'split by \1',
                                                                                     r'grouped by \1', r'for each \1']),
    ('group', r'\bby sex\b', ['by gender']),
    ('connector', r'\bhave both (.+?) and ', [r'have \1 as well as ', r'have \1 along with ']),
    ('connector', r'\bdo not have\b', ["don't have", 'lack']),
    ('connector', r'\bdid not\b', ["didn't"]),
    ('connector', r'\bhave not\b', ["haven't"]),
    ('source', r'\bED visits\b', ['emergency department visits', 'ER visits', 'emergency visits']),
    ('source', r'\bemergency department visits\b', ['ED visits', 'ER visits']),
    ('source', r'\ban ED visit\b', ['an emergency department visit', 'an ER visit']),
    ('source', r'\bmedical claims\b', ['medical claim lines' if False else 'med claims']),
    ('money', r'\bpaid dollars\b', ['paid amounts', 'dollars paid']),
    ('money', r'\ballowed dollars\b', ['allowed amounts', 'dollars allowed']),
    ('rank', r'\btop (\d+|three|five) ', [r'\1 most common ', r'leading \1 ']),
]
COMPILED = [(s, re.compile(p), alts) for s, p, alts in RULES]
FILLERS = [('filler', lambda q: q.rstrip('?') + ', please?'), ('filler', lambda q: 'Hey, ' + q[0].lower() + q[1:]),
           ('filler', lambda q: q.rstrip('?') + ' for our population?'), ('casing', lambda q: q.lower()),
           ('punct', lambda q: q.rstrip('?.')), ('punct', lambda q: q.rstrip('?.') + '??')]
PROTECT = re.compile(r'\d|^(?:no|not|never|without|neither|nor|none|hba1c|egfr|ldl|uacr|copd|ckd|medicaid|medicare)$', re.I)


def typo(q, rng):
    tokens = [m for m in re.finditer(r'[A-Za-z]{6,}', q) if not PROTECT.search(m.group())]
    if not tokens: return None
    m = rng.choice(tokens)
    w = m.group(); i = rng.randrange(1, len(w) - 2)
    return q[:m.start()] + w[:i] + w[i + 1] + w[i] + w[i + 2:] + q[m.end():]


def paraphrases(canonical, rng, k=None, guard=True):
    """3-10 distinct rewordings, each passing local_normalizer.guard against the canonical. -> [{'text', 'styles'}]"""
    from clinical_core.agere.clinical import local_normalizer as N
    k = k or rng.randint(3, 10)
    found, seen = [], {canonical.strip().lower()}
    applicable = [(s, rx, alts) for s, rx, alts in COMPILED if rx.search(canonical)]
    for _ in range(k * 8):
        if len(found) >= k: break
        text, styles = canonical, []
        chosen = rng.sample(applicable, min(len(applicable), rng.randint(1, 3))) if applicable else []
        for style, rx, alts in chosen:
            if not rx.search(text): continue
            text = rx.sub(rng.choice(alts), text, count=1); styles.append(style)
        if rng.random() < 0.3:
            style, fn = rng.choice(FILLERS); text = fn(text); styles.append(style)
        if rng.random() < 0.08:
            t = typo(text, rng)
            if t: text, styles = t, styles + ['typo']
        text = re.sub(r'\s+', ' ', text).strip()
        if not styles or text.lower() in seen: continue
        if guard and N.guard(text, canonical): continue
        seen.add(text.lower()); found.append({'text': text, 'styles': styles})
    return found


def chain_paraphrases(chain, rng):
    """Follow-up utterances re-voiced; the previous question stays fixed."""
    base = chain['followup']
    out, seen = [], {base.lower()}
    for variant in [base.lower(), base.rstrip('?') + '?', 'ok, ' + base, base + ' instead', 'and ' + base.lower(), 'same but ' + base.lower()]:
        if variant.lower() not in seen:
            seen.add(variant.lower()); out.append({'text': variant, 'styles': ['followup'], 'previous': chain['previous']})
    return out[:rng.randint(3, 6)]


def llm_paraphrase_hook(enabled=False):
    """Optional paid paraphraser: off unless explicitly enabled AND ANTHROPIC_API_KEY is set. Returns a callable or None."""
    key = os.environ.get('ANTHROPIC_API_KEY')
    if not (enabled and key): return None
    import urllib.request
    model = os.environ.get('AGERE_PARAPHRASE_MODEL', 'claude-haiku-4-5')

    def call(canonical, n):
        body = json.dumps({'model': model, 'max_tokens': 400, 'messages': [{'role': 'user', 'content':
            f'Rewrite this analytics question {n} different ways a busy clinician or analyst might type it. Keep every number, '
            f'year, negation, condition and population. One per line, no numbering.\n\n{canonical}'}]}).encode()
        req = urllib.request.Request('https://api.anthropic.com/v1/messages', data=body, headers={
            'x-api-key': key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'})
        with urllib.request.urlopen(req, timeout=60) as r:
            text = json.loads(r.read())['content'][0]['text']
        return [l.strip() for l in text.splitlines() if l.strip()][:n]
    return call


# --------------------------------------------------------------------------------------------------------- the stream
def template_family(family, form):
    return f'{family}/{form}'


def build(family, gen, source, rng, vocab, seed, index, llm=None):
    raw = gen(rng, vocab)
    canonical = raw['canonical'].strip()
    para = chain_paraphrases(raw['chain'], rng) if raw.get('chain') else paraphrases(canonical, rng)
    if llm:
        from clinical_core.agere.clinical import local_normalizer as N
        try:
            para += [{'text': t, 'styles': ['llm']} for t in llm(canonical, 3) if not N.guard(t, canonical)]
        except Exception:  # noqa: BLE001 - the hook is optional; template paraphrases stand alone
            pass
    if len(para) < 3 and not raw.get('chain'):
        para += [{'text': t, 'styles': ['trivial']} for t in (canonical.lower(), canonical.rstrip('?.'), canonical + ' please')
                 if t != canonical][:3 - len(para)]
    request = raw.get('request')
    return {'schema': SCHEMA, 'factory': VERSION, 'id': stable(family, canonical, json.dumps(raw.get('chain'), sort_keys=True))[:24],
            'seed': seed, 'index': index, 'family': family, 'form': raw['form'],
            'template_family': template_family(family, raw.get('template_key') or raw['form']),
            'canonical': canonical, 'paraphrases': para, 'request_source': source,
            'request': request, 'chain': raw.get('chain'), 'slots': raw.get('slots'),
            'expected_engine': raw.get('expected_engine'), 'synthetic': True}


def generate(n, seed=1, vocab=None, families=None, start=0, llm=None):
    """Deterministic stream of n unique items (item i depends only on seed and i)."""
    vocab = vocab or Vocab()
    names = [f for f in FAMILIES if not families or f in families]
    weights = [FAMILIES[f][0] for f in names]
    seen, emitted, i = set(), 0, start
    misses = 0
    while emitted < n:
        rng = random.Random(stable(seed, i))
        family = rng.choices(names, weights)[0]
        _, gen, source = FAMILIES[family]
        i += 1
        try:
            item = build(family, gen, source, rng, vocab, seed, i - 1, llm)
        except (ValueError, KeyError, IndexError):
            misses += 1
            if misses > 10 * n + 1000: raise RuntimeError('generator cannot produce more unique items')
            continue
        key = item['id'][:16]
        if key in seen:
            misses += 1
            if misses > 10 * n + 1000: break  # the combinatorial space is exhausted for this family filter
            continue
        seen.add(key)
        emitted += 1
        yield item


def write_shards(out, n, seed=1, shard_size=50000, families=None, resolve=False, llm=None, vocab=None):
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    vocab = vocab or Vocab(out=out.parent)
    shard, handle, counts, templates, started = -1, None, {}, set(), time.time()
    for k, item in enumerate(generate(n, seed, vocab, families, llm=llm)):
        if resolve and item['request'] is None:
            item['request'] = resolve_request(vocab.catalog, item['canonical'])
        if k // shard_size != shard:
            if handle: handle.close()
            shard = k // shard_size
            handle = (out / f'questions-{shard:05d}.jsonl').open('w')
        handle.write(json.dumps(item, separators=(',', ':')) + '\n')
        counts[item['family']] = counts.get(item['family'], 0) + 1
        templates.add(item['template_family'])
    if handle: handle.close()
    manifest = {'factory': VERSION, 'seed': seed, 'n': sum(counts.values()), 'shard_size': shard_size, 'shards': shard + 1,
                'families': counts, 'template_families': len(templates), 'source_identity': vocab.catalog.identity.get('source_digest'),
                'stack': str(vocab.stack), 'seconds': round(time.time() - started, 1)}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=1))
    return manifest


def resolve_request(catalog, question):
    """The free parser's typed request or request_program for a canonical question, or None when it does not parse."""
    from clinical_core.agere.clinical import request_language as L, request_plan as R
    try:
        program = L.parse_program(catalog, question)
        if program is not None: return {'kind': 'program', 'value': program}
        return {'kind': 'request', 'value': L.parse(catalog, question)}
    except (R.PlanError, ValueError, KeyError):
        return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=20000); ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--out', default=str(DEFAULT_OUT / 'questions')); ap.add_argument('--shard-size', type=int, default=50000)
    ap.add_argument('--families', default=''); ap.add_argument('--resolve', action='store_true')
    ap.add_argument('--llm-paraphrase', action='store_true', help='paid; needs ANTHROPIC_API_KEY (off by default)')
    a = ap.parse_args(argv)
    fams = [f for f in a.families.split(',') if f] or None
    print(json.dumps(write_shards(a.out, a.n, a.seed, a.shard_size, fams, a.resolve, llm_paraphrase_hook(a.llm_paraphrase)), indent=1))


if __name__ == '__main__':
    main()
