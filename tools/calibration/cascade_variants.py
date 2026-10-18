"""Question variants with trusted patient lists, for calibrating the specialist routes (tools/calibrate_cascades.py).

The Gold 10 questions (docs/agere/gold10/QUESTIONS.md, bench/gold10_reference.py) are five population-health shapes. This
module derives many more questions of the same shapes by changing parameters (conditions, drug classes, windows,
thresholds, counts, age bands, follow-up windows, screening types, PDC thresholds and periods) and by rephrasing. For each
variant the TRUSTED definition is written here explicitly as a cohort-family spec (never by parsing the question), under
the Gold 10 shared conventions, and its patient list is computed by rendering that spec with cohort_families.render.
A route's answer to the question text is correct when its patient list equals the trusted list (count questions: equal
counts). Where a question adds a constraint a single family spec cannot express (a second condition that must ALSO hold),
the trusted list is the rendered list intersected with that condition's patients (truth 'intersect_conditions').

What this does and does not check: the renderer itself is checked against the locked Gold 10 lists (8 of 8 locked
questions match exactly); the variants then measure whether a route maps the question to the right definition. When a
question leaves a choice to convention (follow-up window closed, alive at as_of, 12-month lookback, active = med list or a
fill covering any of the last 120 days) the trusted definition follows the Gold 10 shared building blocks.

Synthetic data only. Deterministic (seeded). Nothing here calls a model.
"""
from __future__ import annotations

import copy, hashlib, json, random

VERSION = 'cascade-variants/1'
AS_OF = '2030-05-27'
# DEV_SEED's variants found three cohort-family parser bugs (fixed 2026-10-18); certification uses fresh draws (SEED) so the
# certificate is not computed on the questions the fixes were made against.
DEV_SEED = 20261018
SEED = 20261019

ED = {'pos': ['23'], 'revenue_prefixes': ['045'], 'cpt': ['99281-99285'], 'sources': ['claims', 'ehr']}
VISIT = {'types': ['office', 'virtual', 'wellness', 'pediatric_office'], 'cpt': ['99202-99215']}
VISIT_OFFICE = {'types': ['office', 'wellness', 'pediatric_office'], 'cpt': ['99202-99215']}
BH = ['90791', '90792', '90832-90838', '90846', '90847', '90853']
PT = ['97161-97164', '97110', '97112', '97140', '97530']

TERMS = {
    'statin': ['atorvastatin', 'rosuvastatin', 'simvastatin', 'pravastatin', 'lovastatin', 'fluvastatin', 'pitavastatin'],
    'hf_beta_blocker': ['carvedilol', 'metoprolol succinate', 'bisoprolol'],
    'beta_blocker': ['carvedilol', 'metoprolol', 'bisoprolol', 'atenolol', 'propranolol', 'nadolol', 'nebivolol', 'labetalol'],
    'acei': ['lisinopril', 'enalapril', 'ramipril', 'benazepril', 'captopril', 'fosinopril', 'quinapril', 'perindopril',
             'trandolapril', 'moexipril'],
    'arb': ['losartan', 'valsartan', 'irbesartan', 'candesartan', 'olmesartan', 'telmisartan', 'azilsartan', 'eprosartan'],
    'mra': ['spironolactone', 'eplerenone'],
    'sglt2': ['dapagliflozin', 'empagliflozin', 'canagliflozin', 'ertugliflozin', 'sotagliflozin', 'bexagliflozin'],
    'glp1': ['semaglutide', 'liraglutide', 'dulaglutide', 'exenatide', 'lixisenatide', 'tirzepatide'],
    'metformin': ['metformin'],
    'anticoagulant': ['apixaban', 'rivaroxaban', 'dabigatran', 'edoxaban', 'warfarin'],
    'antiplatelet': ['clopidogrel', 'prasugrel', 'ticagrelor', 'aspirin'],
    'ics': ['fluticasone', 'budesonide', 'beclomethasone', 'mometasone', 'ciclesonide'],
}
TERMS['ras'] = TERMS['acei'] + TERMS['arb'] + ['sacubitril']
CLASS_PHRASES = {
    'statin': ['a statin', 'statin therapy', 'any statin'], 'sglt2': ['an SGLT2 inhibitor', 'an SGLT2i'],
    'mra': ['an MRA', 'a mineralocorticoid receptor antagonist'], 'hf_beta_blocker': ['a beta blocker', 'a beta-blocker'],
    'beta_blocker': ['a beta blocker'], 'acei': ['an ACE inhibitor'], 'arb': ['an ARB'], 'glp1': ['a GLP-1 agonist', 'a GLP-1 receptor agonist'],
    'metformin': ['metformin'], 'anticoagulant': ['an anticoagulant', 'a blood thinner'], 'antiplatelet': ['an antiplatelet'],
    'ics': ['an inhaled corticosteroid'],
}
# condition key -> (ICD-10 prefixes, adjective phrases "X patients", noun phrases "patients with X")
CONDITIONS = {
    'hf': (['I50'], ['heart failure', 'HF', 'CHF'], ['heart failure']),
    'dm': (['E10', 'E11'], ['diabetic', 'diabetes'], ['diabetes']),
    'htn': (['I10', 'I11', 'I12', 'I13', 'I15', 'I16'], ['hypertensive', 'hypertension'], ['hypertension', 'high blood pressure']),
    'ckd': (['N18'], ['CKD'], ['chronic kidney disease', 'CKD']),
    'afib': (['I48'], ['atrial fibrillation', 'AFib'], ['atrial fibrillation']),
    'cad': (['I25'], ['CAD', 'coronary artery disease'], ['coronary artery disease']),
    'copd': (['J44'], ['COPD'], ['COPD']),
    'asthma': (['J45'], ['asthma'], ['asthma']),
}
THERAPY_PAIRS = {'hf': ['sglt2', 'mra', 'hf_beta_blocker', 'statin'], 'dm': ['statin', 'metformin', 'sglt2', 'glp1'],
                 'htn': ['acei', 'arb', 'statin'], 'ckd': ['sglt2', 'acei'], 'afib': ['anticoagulant'],
                 'cad': ['statin', 'antiplatelet', 'beta_blocker'], 'copd': ['ics'], 'asthma': ['ics']}
NUM_WORDS = {2: 'two', 3: 'three', 4: 'four', 5: 'five', 6: 'six'}


def _pop(conditions=None, age=None, sex=None, alive=False, coverage=False):
    p = {}
    if conditions: p['condition_prefixes'] = list(conditions)
    if age: p['age'] = list(age)
    if sex: p['sex'] = sex
    if alive: p.update(alive=True, death_source='any')
    if coverage: p['coverage'] = True
    return p


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:12]


class _Gen:
    def __init__(self, seed=SEED):
        self.seed = seed
        self.rng = random.Random(seed)
        self.items = []

    def add(self, family, template, question, spec, returns, *, intersect=None, note=None):
        truth = {'spec': {'family': family, 'parameters': spec, 'returns': 'patient_set'}}
        if intersect: truth['intersect_conditions'] = intersect
        group = digest(truth)  # paraphrases of one trusted definition share a group (they never straddle the split)
        self.items.append({'id': f'{family}:{template}:{len(self.items):04d}', 'family': family, 'template': template,
                           'question': question, 'answer_type': 'count' if returns == 'count' else 'patient_set',
                           'truth': truth, 'group': group, 'note': note,
                           'provenance': {'source': 'gold10-derived variant', 'generator': VERSION, 'seed': self.seed,
                                          'as_of': AS_OF}})

    # -- therapy_class_gap --------------------------------------------------------------------------------------------
    def therapy(self):
        r = self.rng
        for cond, classes in THERAPY_PAIRS.items():
            prefixes, adjs, nouns = CONDITIONS[cond]
            for cls in classes:
                for _ in range(9):
                    age = r.choice([None, None, (40, 75), (18, 64), (65, None), (50, 80)])
                    phrase = r.choice(CLASS_PHRASES[cls])
                    days = 120
                    count = r.random() < 0.2
                    age_txt = ''
                    if age and age[1]:
                        age_txt = r.choice([f' aged {age[0]} to {age[1]}', f' between the ages of {age[0]} and {age[1]}', f' {age[0]}-{age[1]} years old'])
                    elif age:
                        age_txt = r.choice([f' {age[0]} and older', f' aged {age[0]}+', f' {age[0]} or older'])
                    adj, noun = r.choice(adjs), r.choice(nouns)
                    if count:
                        t, q = 'count', f'How many {adj} patients{age_txt} are not on {phrase}?'
                    else:
                        t, q = r.choice([
                            ('which_not_on', f'Which {adj} patients{age_txt} are not on {phrase}?'),
                            ('list_not_taking', f'List {adj} patients{age_txt} who are not taking {phrase}.'),
                            ('with_no_active', f'Which patients{age_txt} with {noun} have no active {phrase.split(" ", 1)[-1] if phrase.startswith(("a ", "an ")) else phrase}?'),
                            ('who_missing', f'Who among our {adj} patients{age_txt} is missing {phrase.split(" ", 1)[-1] if phrase.startswith(("a ", "an ")) else phrase} therapy?'),
                        ])
                    if r.random() < 0.3:
                        days = r.choice([90, 120, 180])
                        q += (f' Definition: {noun} = ICD-10 {"/".join(p + "*" for p in prefixes)} on a condition record, an encounter '
                              f'diagnosis or a payer claim; on the drug = active on the medication list or a fill covering any of the '
                              f'last {days} days; alive at as_of.')
                        t += '+definition'
                    spec = {'classes': [{'name': cls, 'terms': TERMS[cls]}], 'active_days': days, 'mode': 'any_missing',
                            'population': _pop(prefixes, age, alive=True)}
                    self.add('therapy_class_gap', t, q, spec, 'count' if count else 'patient_set')
        # GDMT (four pillars) and "not on any pillar"
        pillars = [{'name': 'hf_beta_blocker', 'terms': TERMS['hf_beta_blocker']}, {'name': 'ras', 'terms': TERMS['ras']},
                   {'name': 'mra', 'terms': TERMS['mra']}, {'name': 'sglt2', 'terms': TERMS['sglt2']}]
        for q in ['Which heart failure patients are not on all four GDMT pillars?', 'List HF patients missing one or more GDMT pillars.',
                  'Which CHF patients are not on full guideline-directed medical therapy (all four pillars)?',
                  'Which heart failure patients are missing at least one of the four GDMT pillars?']:
            self.add('therapy_class_gap', 'gdmt_any', q, {'classes': pillars, 'active_days': 120, 'mode': 'any_missing',
                                                          'population': _pop(['I50'], alive=True)}, 'patient_set')
        for q in ['Which heart failure patients are not on any GDMT pillar?', 'List HF patients on none of the four GDMT pillars.']:
            self.add('therapy_class_gap', 'gdmt_none', q, {'classes': pillars, 'active_days': 120, 'mode': 'all_missing',
                                                           'population': _pop(['I50'], alive=True)}, 'patient_set')
        # dropped-constraint probes: a second condition that must also hold
        for a, b, cls in [('dm', 'ckd', 'sglt2'), ('hf', 'dm', 'sglt2'), ('dm', 'htn', 'acei'), ('cad', 'dm', 'statin'),
                          ('hf', 'afib', 'anticoagulant'), ('dm', 'cad', 'statin'), ('htn', 'ckd', 'acei'), ('hf', 'ckd', 'mra')]:
            pa, adja, _ = CONDITIONS[a]
            pb, _, nounb = CONDITIONS[b]
            for q in [f'Which {adja[0]} patients who also have {nounb[0]} are not on {CLASS_PHRASES[cls][0]}?',
                      f'Which patients with both {CONDITIONS[a][2][0]} and {nounb[0]} are not on {CLASS_PHRASES[cls][0]}?']:
                self.add('therapy_class_gap', 'two_conditions', q,
                         {'classes': [{'name': cls, 'terms': TERMS[cls]}], 'active_days': 120, 'mode': 'any_missing',
                          'population': _pop(pa, alive=True)}, 'patient_set', intersect=pb,
                         note='both conditions must hold')

    # -- utilization_threshold ----------------------------------------------------------------------------------------
    def utilization(self):
        r = self.rng
        ev_phr = {'ed_visit': ['ED visits', 'emergency department visits', 'ER visits', 'ER trips'],
                  'inpatient_admission': ['inpatient admissions', 'hospitalizations', 'hospital admissions']}
        for ev, phrases in ev_phr.items():
            for _ in range(100):
                n = r.choice([2, 3, 4, 5, 6])
                win = r.choice([('m', 3), ('m', 6), ('m', 12), ('m', 18), ('m', 24), ('y', 1), ('y', 2), ('d', 90), ('d', 180)])
                if win[0] == 'm': wtxt, lb = r.choice([f'in the last {win[1]} months', f'in the past {win[1]} months']), {'months': win[1]}
                elif win[0] == 'y':
                    wtxt = 'over the past year' if win[1] == 1 else r.choice(['in the past two years', 'in the last 2 years'])
                    lb = {'months': 12 * win[1]}
                else: wtxt, lb = f'in the last {win[1]} days', {'days': win[1]}
                form = r.choice(['or_more', 'at_least', 'more_than', 'plus', 'word'])
                ntxt = {'or_more': f'{n} or more', 'at_least': f'at least {n}', 'more_than': f'more than {n - 1}',
                        'plus': f'{n}+', 'word': f'{NUM_WORDS[n]} or more'}[form]
                cond = r.choice([None, None, None, 'dm', 'hf', 'copd', 'asthma'])
                who = f'{CONDITIONS[cond][1][0]} patients' if cond else 'patients'
                count = r.random() < 0.15
                q = (f'How many {who} had {ntxt} {r.choice(phrases)} {wtxt}?' if count else
                     r.choice([f'Which {who} had {ntxt} {r.choice(phrases)} {wtxt}?', f'List {who} with {ntxt} {r.choice(phrases)} {wtxt}.']))
                spec = {'event': ev, 'min_count': n, 'window': {'lookback': lb, 'lower_inclusive': False},
                        'population': _pop(CONDITIONS[cond][0] if cond else None)}
                if ev == 'ed_visit': spec['ed'] = dict(ED)
                self.add('utilization_threshold', f'{ev}:{form}', q, spec, 'count' if count else 'patient_set')

    # -- screening_overdue --------------------------------------------------------------------------------------------
    def screening(self):
        r = self.rng
        tests = {'breast': [{'match': ['%mammograph%'], 'codes': ['77065-77067', 'G0202', 'G0204', 'G0206'], 'months': 27}],
                 'cervical': [{'match': ['%cervical%', '%pap%', '%cytology%'], 'codes': ['88141-88175', 'G0123', 'G0124', 'G0141', 'G0143',
                                                                                        'G0144', 'G0145', 'G0147', 'G0148', 'P3000'], 'months': 36}],
                 'colorectal': [{'match': ['%colonoscop%'], 'codes': ['44388-44408', '45378-45398', 'G0105', 'G0121'], 'months': 120},
                                {'match': ['%sigmoidoscop%', '%ct colonograph%'], 'codes': ['45330-45350', '74261-74263', 'G0104'], 'months': 60},
                                {'match': ['%stool dna%', '%fit-dna%', '%cologuard%'], 'codes': ['81528'], 'months': 36},
                                {'match': ['%fobt%', '%fecal immuno%', '%fit%'], 'codes': ['82270', '82274', 'G0328'], 'months': 12}]}
        base = {'breast': ('F', (52, 74)), 'cervical': ('F', (21, 64)), 'colorectal': (None, (45, 75))}
        names = {'breast': ['breast cancer screening', 'a screening mammogram'], 'cervical': ['cervical cancer screening'],
                 'colorectal': ['colorectal cancer screening', 'colon cancer screening']}
        for kind in tests:
            for _ in range(45):
                sex, age = base[kind]
                age_override = r.random() < 0.5
                if age_override:
                    age = r.choice({'breast': [(50, 74), (40, 74), (45, 70)], 'cervical': [(21, 65), (25, 64), (30, 65)],
                                    'colorectal': [(50, 75), (45, 70), (50, 85)]}[kind])
                who = {'F': r.choice(['women', 'female patients']), None: 'patients'}[sex]
                age_txt = r.choice([f' aged {age[0]} to {age[1]}', f' {age[0]}-{age[1]} years old', f' between the ages of {age[0]} and {age[1]}']) if age_override else ''
                t = list(copy.deepcopy(tests[kind]))
                lookback = None
                count = r.random() < 0.15
                if kind == 'breast' and not count and r.random() < 0.35:
                    lookback = r.choice([24, 27, 36])
                    t[0]['months'] = lookback
                    yrs = {24: 'the last 2 years', 27: 'the last 27 months', 36: 'the last 3 years'}[lookback]
                    q = f'Which {who}{age_txt} have not had a mammogram in {yrs}?'
                    tpl = 'breast_no_mammogram'
                else:
                    tpl, q = r.choice([('overdue', f'Which {who}{age_txt} are overdue for {r.choice(names[kind])}?'),
                                       ('due', f'Which {who}{age_txt} are due for {r.choice(names[kind])}?'),
                                       ('list_gap', f'List {who}{age_txt} with a gap in {names[kind][0]}.')])
                if count:
                    q = f'How many {who}{age_txt} are overdue for {names[kind][0]}?'; tpl = 'count'
                spec = {'screening': kind, 'tests': t, 'population': _pop(None, age, sex, alive=True, coverage=True)}
                self.add('screening_overdue', f'{kind}:{tpl}', q, spec, 'count' if count else 'patient_set')

    # -- event_followup_gap -------------------------------------------------------------------------------------------
    def followup(self):
        r = self.rng
        for _ in range(70):  # inpatient discharges
            n = r.choice([7, 14, 30])
            ntxt = {7: r.choice(['within 7 days', 'within a week', 'within seven days']), 14: r.choice(['within 14 days', 'within two weeks', 'within 2 weeks']),
                    30: r.choice(['within 30 days', 'within thirty days'])}[n]
            lbm = r.choice([None, None, 6, 12])
            lb_txt = f' in the last {lbm} months' if lbm else ''
            q = r.choice([f'Which patients discharged from the hospital{lb_txt} had no follow-up visit {ntxt}?',
                          f'List patients with an inpatient discharge{lb_txt} and no follow-up visit {ntxt} of discharge.',
                          f'Which patients left the hospital{lb_txt} and had no follow-up appointment {ntxt}?'])
            spec = {'anchor': {'kind': 'inpatient_discharge', 'claim_discharge_status': ['01', '06'], 'adt_dispositions': ['home', 'home_health']},
                    'window': {'lookback': {'months': lbm or 12}, 'lower_inclusive': False, 'min_days_before': n},
                    'followup': {'kinds': ['visit'], 'days': n, 'visit': dict(VISIT)}, 'population': {}}
            self.add('event_followup_gap', 'discharge', q, spec, 'patient_set')
        for _ in range(60):  # ED visits with a condition
            cond = r.choice(['asthma', 'copd', 'hf', 'dm'])
            prefixes, adjs, nouns = CONDITIONS[cond]
            n = r.choice([7, 14, 30])
            q = r.choice([f'Which patients went to the ED for {nouns[0]} and had no follow-up visit within {n} days?',
                          f'List patients with an ER visit for {nouns[0]} and no follow-up visit within {n} days.'])
            spec = {'anchor': {'kind': 'ed_visit', **ED, 'dx_prefixes': prefixes},
                    'window': {'lookback': {'months': 12}, 'lower_inclusive': False, 'min_days_before': n},
                    'followup': {'kinds': ['visit'], 'days': n, 'visit': dict(VISIT)}, 'population': {}}
            self.add('event_followup_gap', f'ed:{cond}', q, spec, 'patient_set')
        for _ in range(120):  # lab / survey thresholds
            kind = r.choice(['a1c', 'a1c', 'phq', 'phq', 'ldl'])
            n = r.choice([14, 30, 60])
            latest = False
            cond = None
            if kind == 'a1c':
                v = r.choice([8, 9, 10])
                op, opword = r.choice([('gt', 'above'), ('gt', 'over'), ('ge', f'of {v} or higher')])
                lab = f'an A1c {opword} {v}' if op == 'gt' else f'an A1c {opword}'
                if r.random() < 0.4:
                    latest = True
                    lab = lab.replace('an A1c', 'a latest A1c')
                cond = r.choice([None, 'dm'])
                who = 'diabetic patients' if cond else 'patients'
                anchor = {'kind': 'measurement', 'concept': 'hba1c', 'op': op, 'value': v, 'latest_only': latest}
                fu_kinds, fu = ['visit'], {'visit': dict(VISIT)}
                q = r.choice([f'Which {who} with {lab} had no visit within {n} days after it?',
                              f'List {who} with {lab} and no follow-up visit within {n} days.'])
            elif kind == 'phq':
                v = r.choice([10, 15, 20])
                anchor = {'kind': 'measurement', 'concept': 'phq9', 'op': 'ge', 'value': v, 'latest_only': False}
                if r.random() < 0.5:
                    q = f'Which patients with a PHQ-9 of {v} or higher had no behavioral health follow-up within {n} days?'
                    fu_kinds, fu = ['bh'], {'bh_codes': list(BH)}
                else:
                    q = f'Which patients with a PHQ-9 of {v} or higher had no follow-up visit within {n} days?'
                    fu_kinds, fu = ['visit'], {'visit': dict(VISIT)}
            else:
                v = r.choice([160, 190])
                anchor = {'kind': 'measurement', 'concept': 'ldl', 'op': 'ge', 'value': v, 'latest_only': False}
                fu_kinds, fu = ['visit'], {'visit': dict(VISIT)}
                q = f'Which patients with an LDL of {v} or higher had no follow-up visit within {n} days?'
            spec = {'anchor': anchor, 'window': {'lookback': {'months': 12}, 'lower_inclusive': False, 'min_days_before': n},
                    'followup': {'kinds': fu_kinds, 'days': n, **fu}, 'population': _pop(CONDITIONS[cond][0] if cond else None)}
            self.add('event_followup_gap', f'measurement:{kind}', q, spec, 'patient_set')

    # -- adherence_pdc ------------------------------------------------------------------------------------------------
    def adherence(self):
        r = self.rng
        cls_phr = {'statin': ['statins', 'a statin'], 'metformin': ['metformin'], 'sglt2': ['SGLT2 inhibitors'], 'acei': ['ACE inhibitors'],
                   'arb': ['ARBs']}
        for _ in range(120):
            cls = r.choice(list(cls_phr))
            thr = r.choice([70, 75, 80, 80, 90])
            fills = r.choice([2, 2, 3])
            period = r.choice(['last_year', 'last_year', '2029', '2028'])
            ptxt = {'last_year': r.choice(['', ' last year', ' in the prior calendar year']), '2029': ' in 2029', '2028': ' in 2028'}[period]
            ftxt = '' if fills == 2 and r.random() < 0.6 else f' with {fills} or more fills'
            phrase = r.choice(cls_phr[cls])
            q = r.choice([f'Which patients on {phrase}{ftxt} have a PDC below {thr}%{ptxt}?',
                          f'List patients{ftxt} who are non-adherent to {phrase} (PDC under {thr}%){ptxt}.',
                          f'Which patients filling {phrase}{ftxt} had a proportion of days covered below {thr}%{ptxt}?'])
            year = {'last_year': 2029, '2029': 2029, '2028': 2028}[period]
            spec = {'drug_class': {'name': cls, 'terms': TERMS[cls]}, 'threshold': thr / 100, 'min_fills': fills,
                    'period': {'start': f'{year}-01-01', 'end': f'{year}-12-31'}, 'population': {}}
            self.add('adherence_pdc', f'{cls}', q, spec, 'patient_set')


def generate(seed=SEED):
    g = _Gen(seed)
    g.therapy(); g.utilization(); g.screening(); g.followup(); g.adherence()
    seen, out = set(), []
    for it in g.items:  # identical question text with the same truth is one item
        k = (it['question'].lower(), it['group'])
        if k in seen: continue
        seen.add(k); out.append(it)
    return out


def gold_items(gold_dir):
    """The Gold 10 questions themselves (question + Definition), truth = the locked customer-id lists."""
    from pathlib import Path
    gold_dir = Path(gold_dir)
    locked = json.loads((gold_dir / 'locked.json').read_text())
    out = []
    for line in (gold_dir / 'questions.jsonl').read_text().splitlines():
        if not line.strip(): continue
        q = json.loads(line)
        if q['id'] not in locked['questions']: continue
        out.append({'id': f"gold10:{q['id']}", 'family': None, 'template': 'gold10', 'question': q['question'] + ' Definition: ' + q['definition'],
                    'answer_type': 'patient_set', 'truth': {'customer_ids': locked['questions'][q['id']]['ids']}, 'group': f"gold10:{q['id']}",
                    'provenance': {'source': 'docs/agere/gold10/locked.json (locked: Agere derivation and generator agree)',
                                   'as_of': locked['as_of']}})
    return out


def _execute(db, sql):
    return sorted({str(r[0]) for r in db.execute(sql) if r[0] is not None})


def trusted_ids(db, item, tables=None, as_of=AS_OF):
    """-> sorted patient ids for a generated item (renders the trusted spec)."""
    from clinical_core.agere.clinical import cohort_families as cf
    sql = cf.render(item['truth']['spec'], as_of, tables)
    ids = _execute(db, sql)
    if item['truth'].get('intersect_conditions'):
        other = set(_execute(db, cf.condition_sql(item['truth']['intersect_conditions'])))
        ids = [i for i in ids if i in other]
    return ids
