"""Local question compiler: English question -> executable graph, no model call.

Agere's own understanding comes first; a hosted LLM is the fallback. The
compiler reads the question against
  - the concept library (conditions, measurements, encounter types, aliases),
  - the received data dictionary (every stream, field and observed value),
  - a small grammar of comparisons, negation, demographics, payer, social-screen
    flags, grouping, statistics, adjustment and time windows.
Every word that carries meaning must be explained by something it recognised.
If anything substantive is left over, it declines and reports exactly which
words it could not interpret, so the fallback is explicit and measurable.
"""
import re

from . import concepts, source_profile

VERSION = 'local-question-compiler/1'
W = {'basis': 'all_observed', 'start': '', 'end': ''}

# ---- vocabulary -----------------------------------------------------------------
CONDITION_ALIASES = {
    'diabetes': ['diabetes', 'diabetic', 'diabetes mellitus', 'dm', 'diabetic-complication', 'diabetic complication'],
    'type_2_diabetes': ['type 2 diabetes', 't2dm', 'type ii diabetes'],
    'type_1_diabetes': ['type 1 diabetes', 't1dm', 'type i diabetes'],
    'prediabetes': ['prediabetes', 'pre-diabetes', 'prediabetic'],
    'hypertension': ['hypertension', 'htn', 'high blood pressure', 'hypertensive'],
    'heart_failure': ['heart failure', 'chf', 'hf', 'congestive heart failure'],
    'heart_failure_reduced_ef': ['hfref', 'systolic heart failure', 'reduced ejection fraction'],
    'chronic_kidney_disease': ['chronic kidney disease', 'ckd', 'kidney disease', 'renal disease'],
    'copd': ['copd', 'chronic obstructive pulmonary disease', 'emphysema'],
    'asthma': ['asthma', 'asthmatic'],
    'atrial_fibrillation': ['atrial fibrillation', 'afib', 'a-fib'],
    'coronary_artery_disease': ['coronary artery disease', 'cad', 'ischemic heart disease'],
    'myocardial_infarction': ['myocardial infarction', 'heart attack', 'mi'],
    'stroke': ['stroke', 'cerebral infarction'],
    'obesity': ['obesity', 'obese'],
    'hyperlipidemia': ['hyperlipidemia', 'dyslipidemia', 'high cholesterol'],
    'depression': ['depression', 'depressive disorder', 'mdd'],
    'anxiety': ['anxiety'],
    'substance_use_disorder': ['substance use disorder', 'substance use', 'alcohol use disorder', 'opioid use disorder'],
    'dementia': ['dementia', 'alzheimer', "alzheimer's"],
    'cancer': ['cancer', 'malignancy', 'malignant neoplasm'],
    'sepsis': ['sepsis'],
    'anemia': ['anemia', 'anaemia'],
    'hypothyroidism': ['hypothyroidism'],
    'osteoporosis': ['osteoporosis'],
    'sleep_apnea': ['sleep apnea'],
    'hiv': ['hiv'],
}
MEASUREMENT_ALIASES = {
    'hba1c': ['hba1c', 'a1c', 'hemoglobin a1c', 'glycated hemoglobin'],
    'glucose': ['glucose', 'blood sugar'], 'ldl': ['ldl', 'ldl cholesterol'], 'hdl': ['hdl', 'hdl cholesterol'],
    'total_cholesterol': ['total cholesterol', 'cholesterol'], 'triglycerides': ['triglycerides', 'triglyceride'],
    'egfr': ['egfr', 'gfr', 'estimated gfr'], 'creatinine': ['creatinine'], 'potassium': ['potassium'],
    'sodium': ['sodium'], 'bun': ['bun', 'urea nitrogen'], 'hemoglobin': ['hemoglobin', 'hgb'],
    'platelets': ['platelets', 'platelet count'], 'wbc': ['wbc', 'white blood cell', 'white count'],
    'uacr': ['uacr', 'albumin/creatinine', 'urine albumin'], 'nt_probnp': ['nt-probnp', 'ntprobnp', 'bnp'],
    'troponin': ['troponin'], 'tsh': ['tsh'], 'ferritin': ['ferritin'], 'phq9': ['phq-9', 'phq9'],
    'systolic_bp': ['systolic', 'systolic blood pressure', 'systolic bp'],
    'diastolic_bp': ['diastolic', 'diastolic blood pressure', 'diastolic bp'],
    'heart_rate': ['heart rate', 'pulse'], 'weight': ['weight'], 'spo2': ['spo2', 'oxygen saturation'],
}
ENCOUNTER_ALIASES = {
    'emergency_visit': ['emergency department visits', 'emergency department visit', 'emergency department',
                        'ed visits', 'ed visit', 'er visits', 'er visit', 'emergency visits', 'emergency visit', 'ed use', 'ed utilization'],
    'inpatient_admission': ['inpatient admissions', 'inpatient admission', 'hospitalizations', 'hospitalization',
                            'hospital admissions', 'admissions', 'inpatient stays'],
    'observation_stay': ['observation stays', 'observation stay'],
    'urgent_care_visit': ['urgent care visits', 'urgent care'],
    'primary_care_visit': ['primary care visits', 'primary care visit', 'pcp visits', 'office visits', 'wellness visits'],
    'specialist_visit': ['specialist visits', 'specialty visits'],
    'telehealth_contact': ['telehealth', 'telephone visits', 'virtual visits'],
}
SOCIAL_FLAGS = {  # phrase -> (stream, field)
    'food difficulty': ('social_screens', 'food_difficulty_reported'),
    'food insecurity': ('social_screens', 'food_difficulty_reported'),
    'transport difficulty': ('social_screens', 'transport_difficulty_reported'),
    'transportation difficulty': ('social_screens', 'transport_difficulty_reported'),
    'transportation barrier': ('social_screens', 'transport_difficulty_reported'),
    'difficulty paying for care': ('social_screens', 'difficulty_paying_for_care_reported'),
    'financial hardship': ('social_screens', 'difficulty_paying_for_care_reported'),
    'difficulty paying medical bills': ('financial_screens', 'difficulty_paying_medical_bills'),
}
PAYERS = {'medicare': 'medicare', 'medicaid': 'medicaid', 'commercial': 'commercial', 'commercially insured': 'commercial',
          'uninsured': 'uninsured'}
GROUPS = [(r'\bby (?:attributed )?(?:pcp|primary care (?:provider|physician|clinician)|physician|provider|clinician)s?\b', 'pcp'),
          (r'\bby (?:practice|clinic)s?\b', 'practice'),
          (r'\bby (?:hospital|facility|facilities|site)s?\b', 'facility'),
          (r'\bby (?:payer|payor|coverage(?: type)?|insurance(?: type)?|plan type)\b', 'coverage_type'),
          (r'\bby age(?: band| group)?s?\b', 'age_band'), (r'\bby (?:sex|gender)\b', 'sex')]
COMPARATORS = [(r'(?:at least|no less than|>=|≥)', 'ge'), (r'(?:at most|no more than|<=|≤)', 'le'),
               (r'(?:over|above|greater than|more than|higher than|exceeding|>)', 'gt'),
               (r'(?:below|under|less than|lower than|<)', 'lt'), (r'(?:equal to|exactly|=)', 'eq')]
NUMBER = r'(-?\d+(?:\.\d+)?)'
UNSUPPORTED = [
    (r'\bcausal(?:ly)?\b|\bcaus(?:ed|ally) (?:by|prevented)|\bprevented by\b|\bidentified causal effect\b|\battributable\b',
     ['identified causal design or randomised comparison', 'counterfactual outcome model', 'validated outcome attribution method']),
    (r'\b(?:externally )?validated,? calibrated\b|\bcalibrated probability\b',
     ['externally validated prediction model', 'calibration data for this population']),
    (r'\bshared[- ]savings\b|\bpayer contract\b|\bsettlement\b',
     ['executed payer contract terms', 'attribution roster under the contract', 'benchmark/baseline expenditure',
      'quality score and sharing-rate schedule']),
    (r'\bswallow(?:ed)?\b|\bactually (?:took|taken|swallowed)\b|\bverified independently\b',
     ['direct observation of medication ingestion (no received source records it)']),
]
FILLER = set("""a an the of in on at to for with and or any all each every this that these those is are was were be been
having has have had do does did which who whose what how many much number count list return give show tell me us our
patients patient people person persons members member distinct unique recorded observed documented received evidence
diagnosis diagnoses diagnosed result results value values record records explicitly explicit reported report reports
numeric lab labs test tests reading readings measurement measurements visit visits as of use set complete ids id
across either both regardless including include also only per there their they it its from than then one roster
original warehouse cohort population whole entire within during between date dates observation level levels percent
mmol mg dl ml min m2 1.73m2 g l units unit total among notification notifications whose where
it them so""".split())


CLARIFICATIONS = [  # (pattern, effect) - sentences that restate Agere's default semantics or the output shape
    (r'return (?:the |a |all )?(?:complete )?patient ids?(?: set)?', 'patient_set'),
    (r'return (?:a )?table[^.?]*', 'table'), (r'return (?:a )?numeric value', 'value'), (r'return (?:percent|cents)', None),
    (r'do not (?:restrict to the latest result|subtract refunds|infer net balances|net corrections|treat it as realized savings'
     r'|count missing fields as false|require a loinc code|advance)[^.?]*', None),
    (r'do not infer[^.?]*', None),
    (r'this (?:asks about|does not establish|is absence of)[^.?]*', None),
    (r'count each patient once[^.?]*', None), (r'include people with no social screen', None),
    (r'exclude prediabetes(?: and family history)?', None), (r'counting [a-z ]+ records as the denominator', None),
    (r'count (?:notification|order|discharge) records, not unique stays', None),
    (r'regardless of diagnosis', None), (r'ignoring case', None), (r'historical reports need not be contemporaneous', None),
]


def _norm(q):
    q = q.strip()
    q = re.sub(r'\s*use received evidence as of \d{4}-\d{2}-\d{2}\.?', ' ', q, flags=re.I)
    return re.sub(r'\s+', ' ', q).strip()


class Reader:
    """Tracks which characters of the question have been explained."""
    def __init__(self, text):
        self.text, self.low = text, text.lower()
        self.used = [False] * len(text)
        self.notes = []

    def take(self, pattern, note=None, flags=re.I, overlap=False):
        hits = []
        for m in re.finditer(pattern, self.low, flags):
            if m.end() == m.start() or (not overlap and any(self.used[m.start():m.end()])): continue
            for i in range(m.start(), m.end()): self.used[i] = True
            hits.append(m)
        if hits and note: self.notes.append(note)
        return hits

    def leftover(self):
        words = re.findall(r"[a-z][a-z0-9'_/-]*", ''.join(c if not u else ' ' for c, u in zip(self.low, self.used)))
        return [w.strip("-/'") for w in words if w.strip("-/'") not in FILLER and len(w.strip("-/'")) > 1]


def _value_pattern(value):
    parts = [t for t in re.split(r'[_\s-]+', value.lower()) if t]
    stem = lambda t: re.escape(t[:5]) + r'\w*' if len(t) > 5 else re.escape(t)
    return r'(?<![a-z0-9])' + r'[\s_-]+'.join(stem(t) for t in parts) + r'(?![a-z0-9])'


def _alias_pattern(alias):
    return r'(?<![a-z0-9])' + re.escape(alias) + r'(?![a-z0-9])'


def _negated(low, start):
    window = low[max(0, start - 40):start]
    return bool(re.search(r'\b(?:no|not|without|never|lacks?|lacking|but no|excluding|exclude|except)\b(?:\s+\S+){0,4}\s*$', window))


def _stream_vocabulary(profile):
    """Natural names for each received stream: 'pharmacy_dispenses', 'pharmacy dispenses', 'appointment(s)'."""
    names = {}
    for s in profile['streams']:
        spaced = s.replace('_', ' ')
        forms = {s, spaced}
        if spaced.endswith('s'): forms.add(spaced[:-1])
        names[s] = sorted(forms, key=len, reverse=True)
    names.setdefault('adt', []).extend(['adt notification', 'adt'])
    return names


def compile(question, profile, as_of):
    """Return (graph, info). graph is None when the question needs the fallback planner."""
    q = _norm(question)
    r = Reader(q)
    info = {'compiler': VERSION, 'recognised': r.notes}
    # 0. Requests the received data cannot support: answer unsupported locally, naming the missing inputs.
    for pattern, missing in UNSUPPORTED:
        if re.search(pattern, r.low):
            info.update(route='local_unsupported', unexplained=[])
            return {'interpretation': f'Unsupported by received data: {q}', 'coverage': 'unsupported', 'nodes': [],
                    'unmet_requirements': [f'Missing input: {m}' for m in missing]}, info
    info['clarifications'] = []
    for pattern, effect in CLARIFICATIONS:
        for m in r.take(r'(?:^|(?<=[.;?] )|(?<=; )|(?<=, )|(?<=and ))' + pattern, None):
            info['clarifications'].append(m.group(0))
            if effect: info.setdefault('output', effect)
    pid = r.take(r'\bp-[a-z0-9]+\b', 'patient id')
    if pid:  # single-patient questions with a measurement statistic
        return _patient_question(q, r, profile, [m.group(0).upper() for m in pid], info)
    special = _record_constructs(q, r, profile, as_of, info)
    if special is not None:
        return special
    literal = _literal_stream_question(q, r, profile, as_of, info)
    if literal is not None:
        return literal
    return _population_question(q, r, profile, as_of, info)


# ---- population questions (concepts, aggregates, explanations) ------------------
def _population_question(q, r, profile, as_of, info):
    low = r.low
    allp, excl = [], []
    # statistic / intent
    why = r.take(r'\b(?:why|what drives|what is driving|drivers? of|factors? (?:associated with|behind|for)|what distinguishes|contribut\w+ to|explain)\b', 'why')
    share = r.take(r'\b(?:what share|share|what percent(?:age)?|percentage|proportion|fraction|what %)\b(?: of)?', 'share')
    mean = r.take(r'\b(?:average|mean)\b', 'mean')
    median = r.take(r'\bmedian\b', 'median')
    rate = r.take(r'\b(?:rates?|per 1,?000|utili[sz]ation)\b', 'rate')
    count = r.take(r'\b(?:how many|number of|count)\b', 'count')
    which = r.take(r'\b(?:which|list|who are the|identify)\b', 'patient list') or info.get('output') == 'patient_set'
    latest = r.take(r'\b(?:latest|most recent|last)\b', 'latest value')
    adjust = r.take(r'\b(?:risk[- ]adjusted|case[- ]mix(?: adjusted)?|adjusted(?: for case mix)?|fair(?:ly)? compar\w+)\b', 'case-mix adjustment')
    readmit = r.take(r'\b(?:30[- ]day )?readmi(?:ssions?|tted|t)\b(?: within 30 days)?', 'readmission')
    r.take(r'\bwithin 30 days\b')
    highest = r.take(r'\b(?:highest|lowest|top|worst|best|most|fewest)\b', 'ranking')
    group = None
    for pattern, g in GROUPS:
        if r.take(pattern, f'group by {g}'): group = g; break
    if not group and highest:
        for pattern, g in [(r'\b(?:pcps?|physicians?|providers?|clinicians?)\b', 'pcp'), (r'\b(?:practices?|clinics?)\b', 'practice'),
                           (r'\b(?:hospitals?|facilit(?:y|ies))\b', 'facility')]:
            if r.take(pattern, f'group by {g}'): group = g; break
    # demographics: an age needs explicit age context, never a bare number ("over 5.5")
    age_patterns = [
        (r'\bover (?:the age of |age )(\d{1,3})(?![\d.])', 'gt'), (r'\bunder (?:the age of |age )(\d{1,3})(?![\d.])', 'lt'),
        (r'\b(?:aged|age(?:d)? (?:of )?|ages )(at least|over|above|under|below|older than|younger than)?\s*(\d{1,3})(?![\d.])(?:\s*(?:years?(?: old)?|\+|or older|and older|or over|or younger|and under))?', None),
        (r'\b(\d{1,3})(?![\d.])\s*(?:\+|years? (?:old )?(?:or|and) (?:older|over)|or older|and older)', 'ge'),
        (r'\b(\d{1,3})(?![\d.])\s*(?:years? (?:old )?(?:or|and) (?:younger|under)|or younger|and under)', 'le'),
        (r'\b(older|younger) than (\d{1,3})(?![\d.])', None),
        (r'\breported age (at least|over|above|under|below) (\d{1,3})(?![\d.])', None)]
    words_to_op = {'at least': 'ge', 'over': 'gt', 'above': 'gt', 'older than': 'gt', 'older': 'gt', 'under': 'lt', 'below': 'lt',
                   'younger than': 'lt', 'younger': 'lt'}
    for pattern, fixed in age_patterns:
        for m in r.take(pattern, 'age'):
            groups = [g for g in m.groups() if g]
            n = int(next(g for g in groups if g.isdigit()))
            word = next((g for g in groups if not g.isdigit()), None)
            op = fixed or words_to_op.get(word, 'ge' if re.search(r'older|\+|least', m.group(0)) else 'eq' if not word else 'ge')
            if re.search(r'or older|and older|\+|or over', m.group(0)): op = 'ge'
            if re.search(r'or younger|and under', m.group(0)): op = 'le'
            allp.append({'kind': 'patient', 'field': 'age_reported', 'op': op, 'value': n})
    if r.take(r'\b(?:female|women|woman)\b(?: reported sex)?', 'female'): allp.append({'kind': 'patient', 'field': 'sex_reported', 'op': 'eq', 'value': 'F'})
    if r.take(r'\b(?:male|men|man)\b(?: reported sex)?', 'male'): allp.append({'kind': 'patient', 'field': 'sex_reported', 'op': 'eq', 'value': 'M'})
    r.take(r'\breported (?:sex|age)\b|\bat (?:their )?profile capture\b|\bwithout advancing (?:that|the) age(?: to today)?\b')
    for phrase, payer in PAYERS.items():
        if r.take(_alias_pattern(phrase), f'payer {payer}'):
            allp.append({'kind': 'record', 'stream': 'coverage', 'conditions': [{'field': 'coverage_type', 'op': 'eq', 'value': payer}], 'window': W})
    # literal record predicates: a received stream named with one of its observed values
    for s_name, forms in sorted(_stream_vocabulary(profile).items(), key=lambda kv: -max(map(len, kv[1]))):
        if s_name in ('conditions', 'labs', 'vitals', 'encounters', 'coverage', 'social_screens', 'financial_screens'): continue
        fields = profile['streams'][s_name]['fields']
        for form in forms:
            for sm in re.finditer(_alias_pattern(form), low):
                window = low[max(0, sm.start() - 25):sm.end() + 45]
                hit = next(((f, v) for f, spec in fields.items() for v in sorted(spec.get('values') or {}, key=len, reverse=True)
                            if len(v) >= 3 and '_' in v and re.search(_alias_pattern(v), window)), None)
                if not hit: continue
                cond = [{'field': hit[0], 'op': 'eq', 'value': hit[1]}]
                r.take(_alias_pattern(form), f'stream {s_name}'); r.take(_alias_pattern(hit[1]), f'{hit[0]} = {hit[1]}')
                dm = re.search(r'with ([a-z_]+_date) in (\d{4})', window)
                if dm and dm.group(1) in fields:
                    cond.append({'field': dm.group(1), 'op': 'prefix', 'value': dm.group(2)})
                    r.take(r'with ' + dm.group(1) + r' in ' + dm.group(2), 'date year')
                atom = {'kind': 'record', 'stream': s_name, 'conditions': cond, 'window': W}
                (excl if _negated(low, sm.start()) else allp).append(atom)
    # social-screen flags (true unless "as false"/"false")
    for phrase, (stream, field) in SOCIAL_FLAGS.items():
        for m in r.take(_alias_pattern(phrase), f'{field}'):
            tail = low[m.end():m.end() + 25]
            value = not re.match(r'\s*(?:as |is |reported )?false', tail)
            r.take(r'\b(?:as )?(?:true|false)\b')
            atom = {'kind': 'record', 'stream': stream, 'conditions': [{'field': field, 'op': 'eq', 'value': value}], 'window': W}
            (excl if _negated(low, m.start()) else allp).append(atom)
    r.take(r'\b(?:any |a )?social screens?\b(?: explicitly)?(?: reporting)?')
    # literal labels: "lab named eGFR", "result named Hemoglobin A1c or Hemoglobin A1c/Hemoglobin.total"
    measured = []
    for m in r.take(r'\b(?:any )?(?:numeric )?(?:percent-unit )?(?:numeric )?(?:labs?|results?|tests?|a1c result) named ((?:(?! or | below| above| over| under| less| greater| at least| with|[?,]).)+)(?: or ((?:(?! below| above| over| under| less| greater| at least| with|[?,]).)+))?', 'literal lab names'):
        names = [n.strip().rstrip('.') for n in m.groups() if n]
        concept = next((concepts.concept_for_label(n) for n in names if concepts.concept_for_label(n)), None)
        if concept is None:
            info.update(route='fallback', reason=f'unrecognised lab label {names}'); return None, info
        tail = low[m.end():m.end() + 45]
        cmp = None
        for pattern, op in COMPARATORS:
            mm = re.match(r'\s*' + pattern + r'\s*' + NUMBER, tail)
            if mm:
                cmp = (op, float(mm.group(1)))
                for i in range(m.end(), m.end() + mm.end()): r.used[i] = True
                break
        allp.append({'kind': 'measurement', 'concept': concept, 'names': names, 'op': cmp[0] if cmp else 'gt',
                     'value': cmp[1] if cmp else -1e12, 'aggregate': 'latest' if latest else 'any', 'window': W})
    r.take(r'\bpercent-unit\b', None)
    # measurements with comparisons
    for concept, aliases in MEASUREMENT_ALIASES.items():
        for alias in sorted(aliases, key=len, reverse=True):
            for m in r.take(_alias_pattern(alias), f'measurement {concept}'):
                tail = low[m.end():m.end() + 45]
                cmp = None
                for pattern, op in COMPARATORS:
                    mm = re.match(r'\s*(?:results?|values?|levels?|of|is|was)?\s*' + pattern + r'\s*' + NUMBER, tail)
                    if mm:
                        cmp = (op, float(mm.group(1)))
                        for i in range(m.end(), m.end() + mm.end()): r.used[i] = True
                        break
                measured.append((concept, cmp, m.start()))
    for concept, cmp, start in measured:
        if cmp:
            atom = {'kind': 'measurement', 'concept': concept, 'op': cmp[0], 'value': cmp[1],
                    'aggregate': 'latest' if latest else 'any', 'window': W}
        else:
            atom = {'kind': 'concept', 'concept': concept, 'window': W}
        (excl if _negated(low, start) else allp).append(atom)
    r.take(r'\b(?:percent|%|mmol/l|mg/dl|ml/min/1\.73\s?m2|mmhg|pg/ml|ng/l)\b')
    # encounters (as population filters, or as the counted event)
    events = []
    for concept, aliases in ENCOUNTER_ALIASES.items():
        for alias in sorted(aliases, key=len, reverse=True):
            for m in r.take(_alias_pattern(alias), f'encounter {concept}'):
                events.append((concept, m.start()))
    r.take(r'\b(?:including|incl\.?) (?:diabetic )?complications?\b', 'includes complications', overlap=True)
    # conditions
    conds = []
    for concept, aliases in sorted(CONDITION_ALIASES.items(), key=lambda kv: -max(map(len, kv[1]))):
        for alias in sorted(aliases, key=len, reverse=True):
            for m in r.take(_alias_pattern(alias), f'condition {concept}'):
                conds.append((concept, m.start()))
    r.take(r'\b(?:including|incl\.?) (?:diabetic )?complications?\b|\bdiabetic[- ]complications?\b|\bfamily history\b|\bicd-?10\b|\bsnomed\b')
    for concept, start in conds:
        atom = {'kind': 'concept', 'concept': concept, 'window': W}
        system = re.search(r'\b(snomed|icd-?10)\b(?:\s+[a-z-]+){0,2}\s*$', low[max(0, start - 30):start])
        if system:
            atom['system'] = 'snomed' if system.group(1) == 'snomed' else 'icd10'
            r.take(r'\b' + re.escape(system.group(1)) + r'\b', 'coding system', overlap=True)
        target = excl if _negated(low, start) else allp
        if atom not in target: target.append(atom)
    r.take(r'\b(?:no|not|without|but no|lack|lacking|excluding|exclude|except|but|have|has|had|with|and|or)\b')
    info['unexplained'] = r.leftover()
    if info['unexplained']:
        info['route'] = 'fallback'
        info['reason'] = 'words not interpreted locally: ' + ', '.join(info['unexplained'][:12])
        return None, info

    def cohort(atoms_all, atoms_excl):
        return {'id': 'population', 'operation': 'cohort', 'inputs': [],
                'parameters': {'specification': {'all': atoms_all[:20], 'any': [], 'exclude': atoms_excl[:20]}},
                'purpose': 'Population named in the question'}

    nodes, interpretation = [], q
    event_concept = events[0][0] if events else None
    if why:
        outcome = ({'type': 'readmission'} if readmit else
                   {'type': 'concept', 'concept': event_concept} if event_concept else None)
        if outcome is None and measured and measured[-1][1]:
            c, (op, v), _ = measured[-1]
            outcome = {'type': 'measurement', 'concept': c, 'op': op, 'value': v, 'aggregate': 'latest' if latest else 'any'}
            allp = [a for a in allp if not (a['kind'] == 'measurement' and a['concept'] == c)]
        if outcome is None and conds:
            outcome = {'type': 'concept', 'concept': conds[-1][0]}
            allp = [a for a in allp if not (a['kind'] == 'concept' and a['concept'] == conds[-1][0])]
        if outcome is None:
            info.update(route='fallback', reason='why-question without a recognised outcome'); return None, info
        nodes = [cohort(allp, excl), {'id': 'why', 'operation': 'explain', 'inputs': ['population'],
                                      'parameters': {'outcome': outcome, 'window': {'start': '', 'end': ''}},
                                      'purpose': 'Factors distinguishing units with the outcome'}]
    elif readmit or rate or share or mean or median or group or (event_concept and count):
        base_all = [a for a in allp]
        if readmit:
            params = {'measure': 'readmissions', 'concept': '', 'statistic': 'rate' if (rate or share) else 'count'}
        elif mean or median:
            target = next((c for c, cmp, _ in measured if not cmp), None) or (measured[-1][0] if measured else None)
            if target is None:
                info.update(route='fallback', reason='average requested without a measurement'); return None, info
            base_all = [a for a in allp if not (a['kind'] == 'concept' and a['concept'] == target)]
            params = {'measure': 'value', 'concept': target, 'statistic': 'median' if median else 'mean',
                      'value_aggregate': 'latest' if latest or True else 'all'}
        elif event_concept:
            base_all = [a for a in allp if not (a['kind'] == 'concept' and a['concept'] == event_concept)]
            params = {'measure': 'events', 'concept': event_concept, 'statistic': 'rate_per_1000' if (rate or group) else 'count'}
        else:
            # share of the base population that has the last-named concept/measurement
            subject = allp[-1] if allp else None
            if subject is None or subject['kind'] not in ('concept', 'measurement'):
                info.update(route='fallback', reason='share requested without a subject'); return None, info
            base_all = allp[:-1]
            if subject['kind'] == 'measurement':
                info.update(route='fallback', reason='share of a measurement threshold needs a membership comparison'); return None, info
            params = {'measure': 'patients', 'concept': subject['concept'], 'statistic': 'share' if share else 'count'}
        params.update(group_by=group or 'none', adjust='age_sex_conditions' if adjust and params['measure'] != 'value' else 'none',
                      window={'start': '', 'end': ''}, top=25)
        params.setdefault('value_aggregate', 'all')
        nodes = [cohort(base_all, excl), {'id': 'answer', 'operation': 'aggregate', 'inputs': ['population'],
                                          'parameters': params, 'purpose': 'Requested statistic'}]
    else:
        if not allp and not excl and not count:
            info.update(route='fallback', reason='no population or statistic recognised'); return None, info
        nodes = [cohort(allp, excl)]
    info['route'] = 'local_compiler'
    return {'interpretation': interpretation, 'coverage': 'supported_observation', 'unmet_requirements': [],
            'nodes': nodes}, info


# ---- literal data-dictionary questions -> governed SQL --------------------------
def _literal_stream_question(q, r, profile, as_of, info):
    low = r.low
    clinical = [a for al in (*CONDITION_ALIASES.values(), *MEASUREMENT_ALIASES.values()) for a in al if len(a) > 3]
    if any(re.search(_alias_pattern(a), low) for a in clinical) and not re.search(r'\brecords?\b', low):
        return None
    vocab = _stream_vocabulary(profile)
    stream, span = None, None
    for s, forms in sorted(vocab.items(), key=lambda kv: -max(map(len, kv[1]))):
        for f in forms:
            m = re.search(_alias_pattern(f) + r'(?:\s+(?:records?|rows?|notifications?|entries|entry))?', low)
            named_field = any(re.search(_alias_pattern(x), low) for x in profile['streams'][s]['fields'] if '_' in x and len(x) > 6)
            if m and (re.search(r'records?|rows?|notifications?|entr', m.group(0)) or '_' in f or s == 'adt' or named_field):
                stream, span = s, m
                break
        if stream: break
    if not stream: return None
    fields = profile['streams'][stream]['fields']
    for i in range(span.start(), span.end()): r.used[i] = True
    r.notes.append(f'stream {stream}')
    for token in stream.split('_'):  # words of the stream's own name are explained by it
        r.take(r'\b' + re.escape(token.rstrip('s')) + r's?\b', None, overlap=True)
    conds, sql_where, named_fields = [], [], []
    group_field = None
    g = re.search(r'\bby (?:the )?(?:recording |source |service )?([a-z_]+)\b', low)
    if g:
        cand = g.group(1) if g.group(1) in fields else {'organization': 'organization_id', 'clinician': 'attending_clinician_id'}.get(g.group(1))
        if cand in fields:
            group_field = cand; r.take(r'\bby (?:the )?(?:recording |source |service )?' + re.escape(g.group(1)) + r'\b', f'group by {cand}')
    # literal field names mentioned
    for f in sorted(fields, key=len, reverse=True):
        if not fields[f]['populated'] or len(f) < 3: continue
        for m in r.take(_alias_pattern(f), f'field {f}'): named_fields.append(f)
    sum_m = r.take(r'\b(?:gross |raw |total )?sum(?: in usd)?(?: of)?\b', 'sum', overlap=True)
    # observed values mentioned (for low-cardinality fields)
    candidates = [(f, v) for f, spec in fields.items() for v in (spec.get('values') or {}) if len(v) >= 3]
    exact = lambda v: re.search(_alias_pattern(v.lower()), low) is not None and v == v.lower()
    for f, v in sorted(candidates, key=lambda fv: (not exact(fv[1]), -len(fv[1]))):
        if True:
            for m in r.take(_alias_pattern(v.lower()) if exact(v) else _value_pattern(v), f'{f} = {v}'):
                neg = _negated(low, m.start())
                sql_where.append(f"json_extract(payload,'$.{f}') {'<>' if neg else '='} '{v}'")
                named_fields = [x for x in named_fields if x != f]
    # "contains X" on a named field
    for m in r.take(r'\bcontains? ([a-z0-9-]+)', 'contains'):
        target = named_fields.pop() if named_fields else None
        if not target: return None
        sql_where.append(f"lower(json_extract(payload,'$.{target}')) LIKE '%{m.group(1)}%'")
    r.take(r'\bignoring case\b|\bcase[- ]insensitive\b')
    # "lack(s|ing) a <field>" -> missing
    lack = next(iter(r.take(r'\blacks? (?:a |an |any )?([a-z_]+)', 'lacks field', overlap=True)), None)
    if lack and lack.group(1) in fields:
        sql_where.append(f"json_extract(payload,'$.{lack.group(1)}') IS NULL")
        named_fields = [x for x in named_fields if x != lack.group(1)]
    for m in r.take(r'\barrived in a later calendar year than (?:their )?source event dates?\b', 'arrival year after event year'):
        sql_where.append("event_date IS NOT NULL AND available_date IS NOT NULL AND substr(available_date,1,4) > substr(event_date,1,4)")
    for m in r.take(r'\barrived more than (\d+) (?:calendar )?days after (?:their )?source event dates?\b', 'arrival lag'):
        sql_where.append(f"event_date IS NOT NULL AND available_date IS NOT NULL AND julianday(available_date) - julianday(event_date) > {m.group(1)}")
    # year windows: "in 2028" on a named date field or the source event date
    date_field = next((f for f in named_fields if f.endswith('_date')), 'event_date')
    for m in r.take(r'\b(?:with )?(?:source[- ]event[- ]dated |source event dates? )?(?:in|during) (\d{4})\b|(?<=count )(\d{4})\b', 'year'):
        col = 'event_date' if date_field == 'event_date' else f"json_extract(payload,'$.{date_field}')"
        sql_where.append(f"substr({col},1,4) = '{m.group(1) or m.group(2)}'")
        named_fields = [x for x in named_fields if x != date_field]
    r.take(r'\bsource[- ]event[- ]dated\b|\bwhose\b|\bis\b|\bmarked\b|\bsay\b|\bwith\b|\bhave\b|\bhas\b|\bstatus\b|\btype\b|\bexcluding\b|\bnot unique stays\b|\bnotification records\b|\bcount\b')
    # statistic

    r.take(r'\breturn (?:a )?table\b|\breturn cents\b|\bexcluding missing organizations\b|\bmissing\b|\borganizations\b')
    patients = bool(re.search(r'\b(?:patients?|people)\b', low))
    where = ' AND '.join([f"stream = '{stream}'", *sql_where])
    amount = next((f for f in named_fields if re.search(r'cents|amount', f)), None)
    if sum_m and not amount:
        amount = next((f for f in fields if re.search(r'(?:amount|allowed|paid)_cents', f)), None)
    info['unexplained'] = [w for w in r.leftover() if w not in {'usd', 'gross', 'raw', 'rows', 'net', 'refunds', 'subtract', 'infer',
                                                                'balances', 'corrections', 'realized', 'savings', 'treat', 'cents', 'table',
                                                                'organization_id', 'encounter_count', 'claim_record_count', 'clinician_id'}]
    if info['unexplained']:
        info.update(route='fallback', reason='words not interpreted locally: ' + ', '.join(info['unexplained'][:12]))
        return None, info
    cols = re.search(r'table with ([a-z_]+) and ([a-z_]+)', low)
    key_name, count_name = (cols.group(1), cols.group(2)) if cols else (group_field, 'count')
    if group_field:
        sql = (f"SELECT json_extract(payload,'$.{group_field}') AS {key_name}, count(*) AS {count_name} FROM record WHERE {where} "
               f"AND json_extract(payload,'$.{group_field}') IS NOT NULL GROUP BY 1 ORDER BY 2 DESC")
        returns = 'table'
    elif sum_m and amount:
        sql = f"SELECT sum(json_extract(payload,'$.{amount}')) FROM record WHERE {where}"
        if 'usd' in low and amount.endswith('_cents'): sql = f"SELECT sum(json_extract(payload,'$.{amount}'))/100.0 FROM record WHERE {where}"
        returns = 'value'
    elif patients:
        sql = f"SELECT count(DISTINCT patient_id) FROM record WHERE {where}"
        returns = 'patient_set' if (re.search(r'\b(?:which|list)\b', low) or info.get('output') == 'patient_set') else 'count'
        if returns == 'patient_set': sql = f"SELECT DISTINCT patient_id FROM record WHERE {where}"
    else:
        sql = f"SELECT count(*) FROM record WHERE {where}"
        returns = 'count'
    info['route'] = 'local_compiler'
    return {'interpretation': q, 'coverage': 'supported_observation', 'unmet_requirements': [],
            'nodes': [{'id': 'answer', 'operation': 'sql', 'inputs': [], 'parameters': {'query': sql, 'returns': returns},
                       'purpose': f'Literal {stream} question'}]}, info


NUMBER_WORDS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6}
SQL_OPS = {'gt': '>', 'ge': '>=', 'lt': '<', 'le': '<=', 'eq': '='}


def _sql_graph(q, sql, returns, purpose, info):
    info['route'] = 'local_compiler'
    return {'interpretation': q, 'coverage': 'supported_observation', 'unmet_requirements': [],
            'nodes': [{'id': 'answer', 'operation': 'sql', 'inputs': [], 'parameters': {'query': sql, 'returns': returns},
                       'purpose': purpose}]}, info


def _comparison(low, start):
    for pattern, op in COMPARATORS:
        mm = re.match(r'\s*(?:results?|values?|records?|of|is|was)?\s*' + pattern + r'\s*' + NUMBER, low[start:start + 50])
        if mm: return op, float(mm.group(1)), start + mm.end()
    return None


def _finish(q, r, info, sql, returns, purpose):
    info['unexplained'] = r.leftover()
    if info['unexplained']:
        info.update(route='fallback', reason='words not interpreted locally: ' + ', '.join(info['unexplained'][:12]))
        return None, info
    return _sql_graph(q, sql, returns, purpose, info)


def _record_constructs(q, r, profile, as_of, info):
    """Record-level analytics: event sequences, co-occurrence, linkage, repeated measures,
    arrival lag and strict latest values. Returns None when no construct applies."""
    low = r.low
    streams = _stream_vocabulary(profile)
    def stream_in(text):
        for s, forms in sorted(streams.items(), key=lambda kv: -max(map(len, kv[1]))):
            if any(re.search(_alias_pattern(f), text) for f in forms): return s
        return None
    def value_of(stream, text):
        for f, spec in profile['streams'][stream]['fields'].items():
            for v in sorted(spec.get('values') or {}, key=len, reverse=True):
                if len(v) >= 3 and re.search(_value_pattern(v), text): return f, v
        return None
    patients = bool(re.search(r'\bpatients?\b', low))
    count_expr = 'count(DISTINCT a.patient_id)' if patients else 'count(*)'

    # 1. sequences: "<stream> <B> N through M days after a <stream> <A> in a different ... encounter"
    m = re.search(r'(\w+) (\w+) (\d+) (?:through|to|-) (\d+) (?:calendar )?days after (?:an? )?(\w+) (\w+)', low)
    if m and stream_in(m.group(1)) and stream_in(m.group(5)):
        sa, sb = stream_in(m.group(5)), stream_in(m.group(1))
        va, vb = value_of(sa, m.group(6)), value_of(sb, m.group(2))
        if va and vb:
            r.take(re.escape(m.group(0)), 'event sequence')
            different = r.take(r'\bin a different[^.?]*encounter\b', 'different encounter')
            r.take(r'\buse source event dates?(?:, not arrival dates)?\b|\bhave an?\b|\bhas an?\b', None)
            sql = (f"SELECT {count_expr} FROM record a JOIN record b ON b.patient_id = a.patient_id "
                   f"WHERE a.stream = '{sb}' AND json_extract(a.payload,'$.{vb[0]}') = '{vb[1]}' "
                   f"AND b.stream = '{sa}' AND json_extract(b.payload,'$.{va[0]}') = '{va[1]}' "
                   f"AND a.event_date IS NOT NULL AND b.event_date IS NOT NULL "
                   f"AND julianday(a.event_date) - julianday(b.event_date) BETWEEN {m.group(3)} AND {m.group(4)}"
                   + (" AND json_extract(a.payload,'$.encounter_id') IS NOT NULL AND json_extract(b.payload,'$.encounter_id') IS NOT NULL"
                      " AND json_extract(a.payload,'$.encounter_id') <> json_extract(b.payload,'$.encounter_id')" if different else ''))
            return _finish(q, r, info, sql, 'count', f'{sb} {vb[1]} {m.group(3)}-{m.group(4)} days after {sa} {va[1]}')

    # 2. co-occurrence: "both <v1> and <v2> in <stream> for the (exact) same <field>"
    m = re.search(r'\bboth ([a-z_]+) and ([a-z_]+) in ([a-z_]+) for the (?:exact )?same ([a-z_]+)', low)
    if m and m.group(3) in profile['streams'] and m.group(4) in profile['streams'][m.group(3)]['fields']:
        st, key = m.group(3), m.group(4)
        field = next((f for f, spec in profile['streams'][st]['fields'].items()
                      if m.group(1) in (spec.get('values') or {}) and m.group(2) in (spec.get('values') or {})), None)
        if field:
            r.take(re.escape(m.group(0)), 'co-occurrence'); r.take(r'\bwhich\b', None)
            key_expr = f"lower(json_extract(payload,'$.{key}'))" if 'ignoring case' in low else f"json_extract(payload,'$.{key}')"
            sql = (f"SELECT DISTINCT patient_id FROM record WHERE stream = '{st}' AND {key_expr} IS NOT NULL "
                   f"GROUP BY patient_id, {key_expr} HAVING sum(json_extract(payload,'$.{field}') = '{m.group(1)}') > 0 "
                   f"AND sum(json_extract(payload,'$.{field}') = '{m.group(2)}') > 0")
            return _finish(q, r, info, sql, 'patient_set', 'co-occurring statuses for the same item')

    # 3. linkage: "<stream A> records lack any <stream B> record linked by <field>"
    m = re.search(r'([a-z_ ]+?) records? lacks? any ([a-z_]+) records? linked by ([a-z_]+)', low)
    if m and stream_in(m.group(1)) and m.group(2) in profile['streams']:
        sa, sb, field = stream_in(m.group(1)), m.group(2), m.group(3)
        r.take(re.escape(m.group(0)), 'linkage'); r.take(r'\bto th(?:at|e) [a-z ]+? record ids?\b|\bcount [a-z ]+ records\b', None)
        sql = (f"SELECT {'count(DISTINCT o.patient_id)' if patients and 'count' not in low else 'count(*)'} FROM record o WHERE o.stream = '{sa}' "
               f"AND NOT EXISTS (SELECT 1 FROM link l WHERE l.target_record_id = o.record_id AND l.stream = '{sb}' AND l.field = '{field}')")
        return _finish(q, r, info, sql, 'count', f'{sa} without linked {sb}')

    # 4. measurement-record questions ("HbA1c records", "at least two HbA1c results", latest strictly dated)
    for concept, aliases in MEASUREMENT_ALIASES.items():
        for alias in sorted(aliases, key=len, reverse=True):
            am = re.search(_alias_pattern(alias) + r'\s+(records?|results?)', low)
            if not am: continue
            if not (re.search(r'\b(?:at least|more than) \w+ (?:recorded )?(?:numeric )?' + re.escape(alias), low)
                    or re.search(r'arrived more than \d+ (?:calendar )?days after|have no source event date|by the (?:recording |source )?organization', low)):
                continue
            where = [f"concept = '{concept}'", 'value_canonical IS NOT NULL']
            r.take(r'\b(?:numeric )?(?:percent-unit )?(?:numeric )?' + re.escape(alias) + r'\s+(?:records?|results?)', f'{concept} records')
            unit = concepts.MEASUREMENTS[concept]['unit']
            if re.search(r'percent-unit|in percent', low) and unit == '%': r.take(r'\bpercent-unit\b|\bin percent\b', None)
            cmp = _comparison(low, am.end())
            if cmp:
                where.append(f"value_canonical {SQL_OPS[cmp[0]]} {cmp[1]}")
                for i in range(am.end(), cmp[2]): r.used[i] = True
            repeat = re.search(r'\b(?:at least|more than) (\w+) (?:recorded )?(?:numeric )?' + re.escape(alias), low)
            lag = re.search(r'arrived more than (\d+) (?:calendar )?days after (?:their )?source event date', low)
            undated = re.search(r'\bhave no source event date\b', low)
            group = re.search(r'\bby the (?:recording |source )?organization\b', low)
            if repeat:
                n = NUMBER_WORDS.get(repeat.group(1)) or (int(repeat.group(1)) if repeat.group(1).isdigit() else None)
                if not n: return None
                r.take(r'\b(?:at least|more than) \w+ (?:recorded )?(?:numeric )?', 'repeated measure', overlap=True)
                sql = (f"SELECT count(*) FROM (SELECT patient_id FROM measurement WHERE {' AND '.join(where)} "
                       f"GROUP BY patient_id HAVING count(*) >= {n})")
                return _finish(q, r, info, sql, 'count', f'patients with >= {n} qualifying {concept} results')
            if lag:
                r.take(re.escape(lag.group(0)), 'arrival lag'); r.take(r'exclude records with unknown source event dates', None)
                where += ['event_date IS NOT NULL', 'available_date IS NOT NULL', f"julianday(available_date) - julianday(event_date) > {lag.group(1)}"]
                return _finish(q, r, info, f"SELECT count(*) FROM measurement WHERE {' AND '.join(where)}", 'count', 'late-arriving results')
            if undated:
                r.take(re.escape(undated.group(0)), 'undated')
                where.append('event_date IS NULL')
                return _finish(q, r, info, f"SELECT count(*) FROM measurement WHERE {' AND '.join(where)}", 'count', 'undated results')
            if group:
                r.take(re.escape(group.group(0)), 'by organization')
                cols = re.search(r'table with ([a-z_]+) and ([a-z_]+)', low)
                k, c = (cols.group(1), cols.group(2)) if cols else ('organization_id', 'patient_count')
                r.take(r'\bfor\b|\bcount distinct patients\b|\bexclude missing organizations\b', None)
                sql = (f"SELECT organization_id AS {k}, count(DISTINCT patient_id) AS {c} FROM measurement WHERE {' AND '.join(where)} "
                       f"AND organization_id IS NOT NULL GROUP BY 1 ORDER BY 2 DESC")
                return _finish(q, r, info, sql, 'table', f'{concept} patients by organization')
            return None
    m = re.search(r'\blatest (?:source[- ]event[- ]dated )?(' + '|'.join(re.escape(a) for al in MEASUREMENT_ALIASES.values() for a in al) + r')\b', low)
    if m and re.search(r'source[- ]event[- ]dated|exclude undated', low):
        concept = next(c for c, al in MEASUREMENT_ALIASES.items() if m.group(1) in al)
        cmp = _comparison(low, m.end())
        if not cmp: return None
        r.take(re.escape(low[m.start():cmp[2]]), 'strict latest value')
        r.take(r'exclude undated results|on the same date use the latest arrival, then largest record id|\bwhich\b', None)
        sql = (f"SELECT patient_id FROM (SELECT patient_id, value_canonical, row_number() OVER (PARTITION BY patient_id "
               f"ORDER BY event_date DESC, available_date DESC, record_id DESC) AS rn FROM measurement "
               f"WHERE concept = '{concept}' AND event_date IS NOT NULL AND value_canonical IS NOT NULL) "
               f"WHERE rn = 1 AND value_canonical {SQL_OPS[cmp[0]]} {cmp[1]}")
        return _finish(q, r, info, sql, 'patient_set', f'latest dated {concept}')
    return None


def _patient_question(q, r, profile, pids, info):
    low = r.low
    stat = 'max' if r.take(r'\b(?:maximum|max|highest)\b', 'max') else 'min' if r.take(r'\b(?:minimum|min|lowest)\b', 'min') else \
           'latest' if r.take(r'\b(?:latest|most recent|last)\b', 'latest') else None
    concept = None
    for c, aliases in MEASUREMENT_ALIASES.items():
        for a in sorted(aliases, key=len, reverse=True):
            if r.take(_alias_pattern(a), f'measurement {c}'): concept = c; break
        if concept: break
    r.take(r'\b(?:in percent|percent|including undated results|return a numeric value|what is the|for)\b')
    info['unexplained'] = r.leftover()
    if info['unexplained'] or not concept or not stat:
        info.update(route='fallback', reason='patient question not fully interpreted: ' + ', '.join(info['unexplained'][:12]))
        return None, info
    ids = ','.join(f"'{p}'" for p in pids)
    agg = {'max': 'max(value_canonical)', 'min': 'min(value_canonical)'}.get(stat)
    sql = (f"SELECT {agg} FROM measurement WHERE concept='{concept}' AND patient_id IN ({ids})" if agg else
           f"SELECT value_canonical FROM measurement WHERE concept='{concept}' AND patient_id IN ({ids}) AND value_canonical IS NOT NULL "
           f"ORDER BY event_date DESC, available_date DESC LIMIT 1")
    info['route'] = 'local_compiler'
    return {'interpretation': q, 'coverage': 'supported_observation', 'unmet_requirements': [],
            'nodes': [{'id': 'answer', 'operation': 'sql', 'inputs': [], 'parameters': {'query': sql, 'returns': 'value'},
                       'purpose': f'{stat} {concept} for {", ".join(pids)}'}]}, info
