"""The typed intent of a single-patient chart question, parsed deterministically (no model, no SQL).

A chart question becomes one Intent (a plain dict) over the patient's structured sources:

  intent    latest_value | trend | list | count_in_window | last_event | timeline | status | sum | summary
  domain    which part of the chart (DOMAINS below: labs, medications, fills, ED visits, claims, ...)
  concept   what within the domain: a lab concept, a drug class or drug name, a condition, a vaccine, a screening test, a need
  window    the time window the question names (decision_context windows) or None (all recorded history)
  ordinal   2 for "the one before", 1 for "the first"
  attribute a field asked for ("discharge_date", "age", "practice")
  mode      labs only: 'latest' (measurement_latest) or 'any' (every result), per decision_context.lab_mode
  gaps      slots the grammar could not fill; chart_reader asks System One to choose them from the closed lists in OPTIONS

parse() is the grammar. It never guesses a patient (patient_ids does that) and never writes SQL. A question the grammar cannot type
returns an Intent with `gaps`; chart_reader fills the gaps from closed lists (System One) or declines.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path

from . import decision_context as DC

VERSION = 'chart-intents/1'
INTENTS = ('latest_value', 'trend', 'list', 'count_in_window', 'last_event', 'timeline', 'status', 'sum', 'summary', 'compare_values')

DOMAINS = {
    'lab': 'Laboratory results and vital signs (HbA1c, eGFR, LDL, blood pressure, weight, creatinine ...)',
    'medication': 'The medication list and current medicines; whether the patient is on a drug or class',
    'fill': 'Pharmacy fills and pick-ups, adherence (proportion of days covered)',
    'prescription': 'Prescriptions written, filled or never filled',
    'returned_fill': 'Dispensed fills that were returned to stock or not collected',
    'ed_visit': 'Emergency department visits (all sources)',
    'inpatient': 'Hospital admissions and discharges (all sources)',
    'observation': 'Observation stays',
    'readmission': 'Readmission within 30 days of a discharge',
    'visit': 'Office, primary care, specialist and telehealth visits',
    'appointment': 'Appointments, no-shows, cancellations, upcoming visits',
    'immunization': 'Vaccines given',
    'screening': 'Cancer screening results (mammography, colorectal)',
    'wellness': 'Annual wellness visits',
    'allergy': 'Allergies and recorded intolerances',
    'condition': 'Diagnoses and conditions (EHR, problem list, counted claims, laboratory evidence)',
    'problem_list': 'The problem list',
    'smoking': 'Tobacco use status',
    'social_need': 'Screened social needs (food, housing, transport, utilities, safety, cost of care)',
    'sdoh_vendor': 'Purchased social-determinants scores',
    'svi': 'Social vulnerability index of the home county',
    'claims': 'Payer claims and spend',
    'pharmacy_claim': 'Pharmacy claims and pharmacy spend',
    'coverage': 'Insurance coverage, plan, payer, member id, enrollment',
    'practice': 'Attributed practice and primary care clinician',
    'demographics': 'Age, sex, race, ethnicity, language, address',
    'death': 'Vital status',
    'care_gap': 'Care gaps and quality measures',
}

# -- lexicons ---------------------------------------------------------------------------------------------------------------
LAB_TERMS = [  # (pattern, concept(s))
    (r'\b(?:hba1cs?|a1cs?|hemoglobin a1cs?|haemoglobin a1cs?|glycated h\w+|glycohemoglobin|a-1-c)\b', ['hba1c']),
    (r'\b(?:egfr|e-gfr|gfr|glomerular filtration|kidney function)\b', ['egfr']),
    (r'\bldl\b', ['ldl']), (r'\bhdl\b', ['hdl']),
    (r'\btriglycerides?\b', ['triglycerides']),
    (r'\b(?:total )?cholesterol\b', ['total_cholesterol']),
    (r'\bcreatinine\b(?!\s*(?:ratio|clearance))', ['creatinine']),
    (r'\b(?:uacr|urine albumin|microalbumin\w*|albumin[/ -]*creatinine)\b', ['uacr']),
    (r'\bpotassium\b', ['potassium']), (r'\bsodium\b', ['sodium']), (r'\b(?:bun|blood urea nitrogen)\b', ['bun']),
    (r'\bhemoglobin\b|\bhgb\b', ['hemoglobin']), (r'\bplatelets?\b', ['platelets']), (r'\b(?:wbc|white (?:blood )?cell\w*)\b', ['wbc']),
    (r'\bnt-?pro-?bnp\b|\bbnp\b', ['nt_probnp']), (r'\btroponin\b', ['troponin']), (r'\btsh\b', ['tsh']), (r'\bferritin\b', ['ferritin']),
    (r'\bphq-?9\b', ['phq9']), (r'\bphq-?2\b', ['phq2']),
    (r'\b(?:blood pressure|bp)\b', ['systolic_bp', 'diastolic_bp']), (r'\bsystolic\b', ['systolic_bp']), (r'\bdiastolic\b', ['diastolic_bp']),
    (r'\b(?:heart rate|pulse)\b', ['heart_rate']), (r'\b(?:weight|weigh\w*)\b', ['weight']),
    (r'\b(?:spo2|o2 sat\w*|oxygen sat\w*)\b', ['spo2']), (r'\b(?:glucose|blood sugar)\b', ['glucose']),
]
LAB_GENERAL = re.compile(r'\b(?:labs?|lab results?|lab work|test results?|vitals?|vital signs?)\b', re.I)
UNMEASURED = re.compile(r'\b(?:bmi|body mass index|height|temperature|a-?fib burden)\b', re.I)  # no concept in the warehouse
# +1: a higher value is favourable; -1: a lower value is favourable; 0: neither (direction is reported as rising / falling)
POLARITY = {'hba1c': -1, 'ldl': -1, 'systolic_bp': -1, 'diastolic_bp': -1, 'creatinine': -1, 'uacr': -1, 'triglycerides': -1,
            'total_cholesterol': -1, 'nt_probnp': -1, 'egfr': 1, 'hdl': 1, 'phq9': -1, 'phq2': -1, 'bun': -1}
LAB_LABELS = {'hba1c': 'HbA1c', 'egfr': 'eGFR', 'ldl': 'LDL', 'hdl': 'HDL', 'systolic_bp': 'systolic blood pressure',
              'diastolic_bp': 'diastolic blood pressure', 'uacr': 'urine albumin/creatinine ratio', 'nt_probnp': 'NT-proBNP',
              'total_cholesterol': 'total cholesterol', 'bun': 'BUN', 'wbc': 'WBC', 'tsh': 'TSH', 'phq9': 'PHQ-9', 'phq2': 'PHQ-2',
              'spo2': 'oxygen saturation', 'heart_rate': 'heart rate'}

# drug class synonyms -> fill.drug_class values (also used for medication-list matching through the drug lexicon)
CLASS_TERMS = [
    (r'\bstatins?\b', 'statin'), (r'\bace[- ]?inhibitors?\b|\bacei\b|\bace-i\b', 'ace_inhibitor'),
    (r'\bangiotensin receptor blockers?\b|\barbs?\b', 'arb'), (r'\barni\b|entresto|sacubitril', 'arni'),
    (r'\bbeta[- ]?blockers?\b|\bb-?blockers?\b', 'beta_blocker'),
    (r'\bsglt-?2(?:\s*i)?\b|\bgliflozins?\b|sglt2 inhibitors?', 'sglt2_inhibitor'), (r'\bglp-?1\w*\b', 'glp1_agonist'),
    (r'\bdpp-?4\w*\b', 'dpp4_inhibitor'), (r'\bsulfonylureas?\b', 'sulfonylurea'), (r'\binsulin\b', 'insulin'),
    (r'\bmetformin\b', 'metformin'), (r'\bmras?\b|mineralocorticoid', 'mra'),
    (r'\banticoagulants?\b|\bblood thinners?\b|\bdoacs?\b', 'anticoagulant'), (r'\bantiplatelets?\b', 'antiplatelet'),
    (r'\bloop diuretics?\b', 'loop_diuretic'), (r'\bthiazides?\b|\bthiazide diuretics?\b', 'thiazide_diuretic'),
    (r'\bcalcium channel blockers?\b|\bccbs?\b', 'calcium_channel_blocker'), (r'\bopioids?\b', 'opioid'),
    (r'\bbenzodiazepines?\b|\bbenzos?\b', 'benzodiazepine'), (r'\bppis?\b|proton pump inhibitors?', 'proton_pump_inhibitor'),
    (r'\bnsaids?\b', 'nsaid'), (r'\bssris?\b', 'ssri'), (r'\bsnris?\b', 'snri'), (r'\bantipsychotics?\b', 'antipsychotic'),
    (r'\bnitrates?\b', 'nitrate'), (r'\blevothyroxine\b|thyroid replacement', 'levothyroxine'),
    (r'\binhaled corticosteroids?\b|\bics\b', 'inhaled_corticosteroid'), (r'\bgabapentinoids?\b', 'gabapentinoid'),
    (r'\bbisphosphonates?\b', 'bisphosphonate'), (r'\bantibiotics?\b', 'antibiotic'),
]

CONDITION_ALIASES = {  # extra words per library condition (the library's own text patterns are used first)
    'diabetes': r'\bdiabet\w*|\bdm2?\b', 'hypertension': r'\bhypertens\w*|\bhtn\b|high blood pressure',
    'heart_failure': r'\bheart failure\b|\bchf\b|\bhfref\b|\bhfpef\b', 'chronic_kidney_disease': r'\bckd\b|chronic kidney|kidney disease',
    'coronary_artery_disease': r'\bcad\b|coronary', 'atrial_fibrillation': r'\bafib\b|a-fib|atrial fib\w*', 'copd': r'\bcopd\b|emphysema',
}

VACCINES = [(r'\b(?:flu|influenza)\b', "influenza%"), (r'\bcovid\w*|\bsars-?cov\w*', 'COVID%'), (r'\btdap\b|tetanus', 'Tdap'),
            (r'\b(?:zoster|shingles|shingrix)\b', 'zoster%'), (r'\bpneumo\w*|\bpcv\d*', 'pneumo%'), (r'\bhpv\b', 'HPV%'),
            (r'\bmmr\b|measles', 'MMR'), (r'\bhepatitis b\b|\bhep b\b', 'hepatitis B%'), (r'\bvaricella\b|chicken ?pox', 'varicella'),
            (r'\bmeningo\w*', 'meningococcal%'), (r'\bipv\b|polio', 'IPV'), (r'\bdtap\b', 'DTaP'), (r'\bhib\b', 'Hib')]
SCREENINGS = [(r'\bmammo\w*|breast cancer screening|\bbcs\b', 'bilateral_screening_mammography', 'mammography'),
              (r'\bfit\b|cologuard|fecal (?:immunochemical|occult)|stool test|colorectal|colon cancer screening|colonoscop\w*|\bcol\b',
               'FIT', 'colorectal screening')]
NEEDS = [(r'\bfood\b|hunger|nutrition', 'food'), (r'\bhous\w+|homeless|rent\b', 'housing'), (r'\btransport\w*|ride\b|getting to', 'transport'),
         (r'\butilit\w+|electric|heating', 'utilities'), (r'\bsafety|violence|unsafe', 'safety'),
         (r'\bcost of care|afford\w*|financial|cost\b|money', 'cost_of_care')]

# -- wording ----------------------------------------------------------------------------------------------------------------
SUMMARY = re.compile(r"\b(?:summar(?:y|ize|ise)|overview|briefing|brief me|what should i know|before (?:i )?(?:see|seeing|meeting|the visit|visit)|"
                     r"snapshot|tell me about|run ?down|chart review|patient profile|catch me up|bring me up to speed|walk me through)\b", re.I)
HOW_MANY = re.compile(r'\b(?:how many|number of|count of|how often|how many times|count)\b', re.I)
HOW_MUCH = re.compile(r'\bhow much\b|\btotal (?:claims? )?(?:paid|payments?|spend|cost|billed|allowed|amount)|\bpay(?: out)?\b|\bspen[dt]\b|\bpaid\b|\bcosts?\b|\bamount\b', re.I)
STATUS_LEAD = re.compile(r"^\W*(?:and\s+)?(?:is|are|was|were|does|do|did|has|have|had|can|could|will|would|any)\b", re.I)
# A yes/no question may open with a short phrase before its verb: "At the annual wellness visit, was a memory concern raised?"
FRONTED_STATUS_LEAD = re.compile(r"^\W*[^,?]{3,80},\s*(?:and\s+)?(?:is|are|was|were|does|do|did|has|have|had|can|could|will|would|any)\b", re.I)


def asks_yes_no(text):
    return bool(STATUS_LEAD.match(text or '') or FRONTED_STATUS_LEAD.match(text or ''))
WHEN = re.compile(r'\b(?:when|what date|what day|date of|date did|how long ago)\b', re.I)
LAST = re.compile(r'\b(?:last|most recent|latest|recent(?:ly)?|newest|previous|prior|first|earliest|oldest|final)\b', re.I)
LISTISH = re.compile(r'\b(?:list|which|what (?:are|were)|show|name|every|each|all (?:of )?(?:the|his|her|their)?|what .{0,25}(?:medications|meds|allergies|'
                     r'problems|conditions|diagnoses|vaccines|immunizations|needs|visits|appointments|claims))\b', re.I)
TRENDISH = re.compile(r'\b(?:trend\w*|getting (?:better|worse)|better or worse|improv\w+|worsen\w*|deteriorat\w*|over time|change[sd]?|changing|'
                      r'compar\w+|increas\w+|decreas\w+|rising|falling|going (?:up|down)|direction|progress\w*|control)\b', re.I)
CHANGE_AMOUNT = re.compile(r'\bhow much (?:has|have|did|does)\b.*\b(?:chang\w+|gain\w*|los[et]\w*|drop\w*|rise\w*|risen|fall\w*|moved)\b|'
                           r'\b(?:gain(?:ed)?|lost|loss) (?:in|of)\b|\bchange in\b|\bfirst (?:vs|versus|and) last\b', re.I)
TIMELINE = re.compile(r'\b(?:timeline|chronolog\w*|history|over the (?:last|past)|sequence|each (?:result|reading|time)|all (?:results|values|readings|draws))\b', re.I)
ORDINAL_PREV = re.compile(r'\b(?:the one before(?: that| it)?|before (?:that|the (?:most recent|latest|last) (?:one|result|value|reading))|'
                          r'before the (?:most recent|latest|last)|second (?:most )?recent|2nd (?:most )?recent|previous(?:ly)? (?:one|result|value)|'
                          r'prior (?:one|result|value)|the (?:previous|prior) (?:one|result|value|reading))\b', re.I)
ORDINAL_FIRST = re.compile(r'\b(?:first|earliest|oldest|initial) (?:ever |recorded )?(?:result|value|reading|one|a1c|ldl|egfr|visit|admission|fill)\b', re.I)
EVER = re.compile(r'\b(?:ever|at any time|at any point|in total|all time|on record|in the (?:chart|record)|any time)\b', re.I)
NEVER_FILLED = re.compile(r'\b(?:never (?:been )?filled|not (?:been )?filled|unfilled|never (?:picked up|collected)|not picked up|abandoned)\b', re.I)
NOTE_CUES = re.compile(r"\b(?:notes?|note says|documented in|document\w*|mention\w*|chart say|says?|discussed|discussion|conversation|talk|goals[- ]of[- ]care|"
                       r"code status|dnr|dni|(?:resuscitat\w*|\bcpr\b)|chest compressions|breathing tube|intubat\w*|advance directive|polst|living will|why was|"
                       r"reason (?:for|\w+ was)|accept(?:ed)?|declin\w*|refus\w*|report(?:s|ed)? taking|discharge summary|impression|"
                       r"radiology|x-?ray report|ruled out|rule[- ]out|stress test|wellness visit|noted|recorded in the notes|trouble|barriers?|"
                       r"intoleran\w*|goals of care|memory concern|raised|later notes|anywhere in)\b", re.I)

SEASON_TERMS = re.compile(r'\b(?:this|the|last|past|current)\s+(?:past\s+)?(?:flu\s+|vaccine\s+|respiratory\s+)?season\b', re.I)
LAST_N = re.compile(r'\b(?:in |during |over |within |for )?the (?:last|past|previous) (\d+|one|two|three|four|five|six|twelve|eighteen|twenty-four)'
                    r'\s*(day|week|month|year)s?\b|\b(?:in |during |over |within )?(?:the )?(?:last|past) (\d+|one|two|three|four|five|six|twelve) (day|week|month|year)s?\b', re.I)
SINCE = re.compile(r'\bsince\s+((?:19|20)\d\d)(?:-(\d\d)-(\d\d))?\b', re.I)
BETWEEN = re.compile(r'\bbetween\s+((?:19|20)\d\d-\d\d-\d\d)\s+and\s+((?:19|20)\d\d-\d\d-\d\d)\b', re.I)
_NUM = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'twelve': 12, 'eighteen': 18, 'twenty-four': 24}


# -- windows ----------------------------------------------------------------------------------------------------------------
def window_of(question, as_of, domain=None):
    """The window the question names, or None. -> {'kind', 'start', 'end', 'start_exclusive', 'phrase'} (end inclusive)."""
    q = question or ''
    m = BETWEEN.search(q)
    if m:
        return {'kind': 'between', 'start': m[1], 'end': m[2], 'start_exclusive': False, 'phrase': m.group()}
    for w in DC.year_window(q, as_of):
        if w['kind'] == 'rolling_12_months':
            return {'kind': w['kind'], 'start': w['start'], 'end': w['end'], 'start_exclusive': True, 'phrase': w['phrase']}
    m = LAST_N.search(q)
    if m:
        n_raw = m.group(1) or m.group(3)
        unit = (m.group(2) or m.group(4)).lower()
        n = int(n_raw) if n_raw.isdigit() else _NUM.get(n_raw.lower(), 0)
        if n:
            if unit == 'year': n, unit = n * 12, 'month'
            if unit == 'month':
                if n == 12:
                    return {'kind': 'rolling_12_months', 'start': DC.add_months(as_of, -12).isoformat(), 'end': str(as_of)[:10],
                            'start_exclusive': True, 'phrase': m.group()}
                start = DC.add_months(as_of, -n).isoformat()
            else:
                start = (date.fromisoformat(str(as_of)[:10]) - timedelta(days=n * (7 if unit == 'week' else 1))).isoformat()
            return {'kind': f'last_{n}_{unit}s', 'start': start, 'end': str(as_of)[:10], 'start_exclusive': True, 'phrase': m.group()}
    for w in DC.year_window(q, as_of):
        if w['kind'] == 'calendar_year':
            return {'kind': w['kind'], 'start': w['start'], 'end': w['end'], 'start_exclusive': False, 'phrase': w['phrase']}
    m = SINCE.search(q)
    if m:
        start = f'{m[1]}-{m[2]}-{m[3]}' if m[2] else f'{m[1]}-01-01'
        return {'kind': 'since', 'start': start, 'end': str(as_of)[:10], 'start_exclusive': False, 'phrase': m.group()}
    if SEASON_TERMS.search(q):
        a = str(as_of)[:10]
        y = int(a[:4]) if a[5:7] >= '08' else int(a[:4]) - 1
        return {'kind': 'season', 'start': f'{y}-08-01', 'end': a, 'start_exclusive': False, 'phrase': SEASON_TERMS.search(q).group()}
    return None


def window_sql(column, window):
    """-> (sql fragment, params) for a date column inside the window ('' and [] when there is none)."""
    if not window:
        return '', []
    op = '>' if window.get('start_exclusive') else '>='
    return f' AND {column} {op} ? AND {column} <= ?', [window['start'], window['end']]


def describe_window(window):
    if not window:
        return 'all recorded history'
    if window['kind'] == 'rolling_12_months':
        return f"the 12 months ending {window['end']}"
    if window['kind'] == 'calendar_year':
        return f"calendar year {window['start'][:4]}"
    if window['kind'] == 'season':
        return f"the season starting {window['start']} (through {window['end']})"
    return f"{window['start']} to {window['end']}"


# -- the lexicon of drugs, from the warehouse -----------------------------------------------------------------------------------
def drug_lexicon(db):
    """{'names': {lower name: sorted classes}} from the fill table (drug_name, drug_class) and the medication list's drug keys."""
    names = {}
    for name, cls in db.execute('SELECT DISTINCT lower(drug_name), drug_class FROM fill WHERE drug_name IS NOT NULL'):
        if name and cls:
            names.setdefault(name.strip(), set()).add(cls)
    for (key,) in db.execute('SELECT DISTINCT lower(drug_key) FROM medication_list_entry WHERE drug_key IS NOT NULL'):
        if key and len(key) > 3:
            names.setdefault(key.strip(), set())
    return {'names': {k: sorted(v) for k, v in names.items()}}


# -- parse --------------------------------------------------------------------------------------------------------------------
def _find_condition(q):
    try:
        from .concepts import CONDITIONS
    except Exception:  # noqa: BLE001
        return None
    best = None
    for name, spec in CONDITIONS.items():
        pats = [CONDITION_ALIASES.get(name), spec.get('text')]
        for p in pats:
            if p and re.search(p, q, re.I):
                if spec.get('exclude') and re.search(spec['exclude'], q, re.I):
                    continue
                # prefer the most specific library concept (type 2 / HFrEF over the umbrella) = the longer text pattern
                if best is None or len(spec.get('text', '')) > len(CONDITIONS[best].get('text', '')):
                    best = name
    return best


def _drug(q, lexicon):
    for pat, cls in CLASS_TERMS:
        if re.search(pat, q, re.I):
            return {'kind': 'class', 'value': cls, 'term': re.search(pat, q, re.I).group()}
    names = (lexicon or {}).get('names') or {}
    words = set(re.findall(r"[a-z][a-z-]{3,}", q.lower()))
    hit = sorted((w for w in words if w in names), key=len, reverse=True)
    if hit:
        return {'kind': 'drug', 'value': hit[0], 'classes': names[hit[0]], 'term': hit[0]}
    # a drug named in a list the warehouse does not know as a fill: ends in a common stem
    m = re.search(r"\b([a-z]{4,}(?:statin|pril|sartan|olol|dipine|gliflozin|glutide|formin|azole|mycin|cillin|oxacin|pam|lam|done|codone|orphine))\b", q, re.I)
    if m:
        return {'kind': 'drug', 'value': m.group(1).lower(), 'classes': [], 'term': m.group(1)}
    return None


def _norm_q(question, quotes):
    q = str(question or '')
    for quote in quotes or []:
        if quote:
            q = re.sub(re.escape(quote), ' ', q, flags=re.I)
    return re.sub(r'\s+', ' ', q).strip()


# -- condition control: a condition mapped to the measures that govern it (data: config/agere/condition_measures.json) ------------------
CONDITION_MEASURES_PATH = Path(__file__).resolve().parents[3] / 'config' / 'agere' / 'condition_measures.json'
CONTROL = re.compile(r"\bcontrol(?:led)?\b|\bgetting (?:better|worse)\b|\bbetter or worse\b|\bimprov\w+|\bworsen\w*|\bdeteriorat\w*|\bprogress(?:ing|ion)?\b|"
                     r"\bat goal\b|\bwell[- ]managed\b|\btrending\b|\bstable\b|\bunder control\b|\bdoing (?:better|worse)\b|\btrend\w*", re.I)
CONTROL_DIRECTION = re.compile(r'\b(?:better|worse|improv\w+|worsen\w*|deteriorat\w*|trend\w*|over time|chang\w+|compar\w+|progress\w*|direction|rising|falling|increas\w+|decreas\w+|going (?:up|down))\b', re.I)
_CM = {}


def condition_measures():
    """-> {condition: {'measures': [measurement concepts the warehouse has], 'source': citation}} from the data file."""
    if not _CM:
        try:
            raw = json.loads(CONDITION_MEASURES_PATH.read_text()).get('conditions') or {}
        except (OSError, ValueError):
            raw = {}
        try:
            from .concepts import MEASUREMENTS
        except Exception:  # noqa: BLE001
            MEASUREMENTS = {}
        _CM['v'] = {c: {'measures': [m for m in v.get('measures', []) if not MEASUREMENTS or m in MEASUREMENTS], 'source': v.get('source')} for c, v in raw.items()}
    return _CM['v']


def _condition_phrase(low, cond):
    """The words of the question that named the condition (kept in the typed concept so the plan carries what was asked)."""
    try:
        from .concepts import CONDITIONS
    except Exception:  # noqa: BLE001
        return cond.replace('_', ' ')
    for p in (CONDITION_ALIASES.get(cond), CONDITIONS.get(cond, {}).get('text')):
        m = re.search(p, low, re.I) if p else None
        if m:
            return m.group().strip()
    return cond.replace('_', ' ')


def condition_control(q, low, domain, concept):
    """A question about the control or direction of a CONDITION ("is the diabetes control getting better or worse") is a question about the
    measures that govern it (diabetes: HbA1c; hypertension: systolic and diastolic pressure; CKD: eGFR and UACR ...). The typed concept carries
    both the condition and its measures. A lab question that merely names its condition also keeps the condition. -> (domain, concept, controlled)."""
    cond = _find_condition(low)
    spec = condition_measures().get(cond) if cond else None
    if not spec or not spec['measures']:
        return domain, concept, False
    if domain == 'lab' and isinstance(concept, dict) and concept.get('kind') == 'lab':
        return domain, {**concept, 'condition': cond, 'condition_quote': _condition_phrase(low, cond)}, False
    if domain in (None, 'condition') and CONTROL.search(low):
        ms = spec['measures']
        return 'lab', {'kind': 'lab', 'value': ms[0], 'concepts': list(ms), 'condition': cond, 'condition_quote': _condition_phrase(low, cond)}, True
    return domain, concept, False


COMPARISON_NONLATEST = re.compile(r'\b(?:ever|never|always|every|all|previous|prior|earliest|first|before)\b', re.I)


def explicit_comparisons(text, concepts):
    """Literal user-supplied bounds; this does not supply clinical goal values."""
    if re.search(r'\bnot\b|\bno longer\b', text):
        return None
    operators = {'under': '<', 'below': '<', 'less than': '<', '<': '<',
                 'at most': '<=', '<=': '<=', 'above': '>', 'over': '>',
                 'greater than': '>', '>': '>', 'at least': '>=', '>=': '>='}
    matches = [m for m in re.finditer(r'(?<!\w)(?P<op>at most|at least|less than|greater than|under|below|above|over|<=|>=|<|>)\s*(?P<a>\d+(?:\.\d+)?)(?:\s*/\s*(?P<b>\d+(?:\.\d+)?))?', text)
               if not re.match(r'\s*(?:days?|weeks?|months?|years?|hours?)\b', text[m.end():])]
    if len(matches) != 1:
        return None
    match = matches[0]
    op = match['op']
    first = match['a']
    if set(concepts) == {'systolic_bp', 'diastolic_bp'} and match['b']:
        # Below a paired target means both components are below it. An "above"
        # pair can mean either component or both; do not invent that connective.
        if operators[op] not in ('<', '<='):
            return None
        values = [('systolic_bp', first), ('diastolic_bp', match['b'])]
    elif len(concepts) == 1 and concepts[0] and not match['b']:
        values = [(concepts[0], first)]
    else:
        return None
    unit_match = re.match(r'\s*(mmhg|%|percent|mg/dl|mmol/l|mmol/mol|ml/min(?:/1\.73m2)?)', text[match.end():])
    unit = unit_match[1] if unit_match else None
    return [{'concept': concept, 'operator': operators[op], 'value': float(value),
             **({'unit': unit} if unit else {})} for concept, value in values]


def parse(question, as_of, *, quotes=(), lexicon=None, prior=None):
    """The grammar. -> Intent dict (always; `gaps` lists what it could not fill). `prior` is the previous turn's Intent for an
    elliptical follow-up ("and the one before that?")."""
    q = _norm_q(question, quotes)
    low = q.lower()
    out = {'intent': None, 'domain': None, 'concept': None, 'window': None, 'ordinal': None, 'attribute': None, 'mode': 'latest',
           'flags': [], 'gaps': [], 'source': 'grammar', 'question': q}
    if not low:
        out['gaps'] = ['domain', 'intent']
        return out
    if any(quotes or ()):
        prior = None  # a question that names its patient is a whole question; only an elliptical follow-up inherits
    out['window'] = window_of(q, as_of)
    out['mode'] = DC.lab_mode(q)
    if SUMMARY.search(q) and not _specific_domain(low, lexicon):
        out.update(intent='summary', domain='summary')
        return out

    domain, concept = _domain_and_concept(q, low, lexicon)
    domain, concept, controlled = condition_control(q, low, domain, concept)
    note_like = bool(NOTE_CUES.search(q))
    out['note_cues'] = note_like
    inherited = False
    if domain is None and prior and prior.get('domain'):
        # elliptical follow-up: keep the previous question's subject matter and window unless this one names its own
        domain, concept = prior['domain'], prior.get('concept')
        if out['window'] is None:
            out['window'] = prior.get('window')
        inherited = True
    elif domain and prior and prior.get('domain') == domain and out['window'] is None and len(low.split()) <= 12 and re.search(
            r'\b(?:one|that|those|it|them|latest|most recent|last|previous)\b', low):
        out['window'] = prior.get('window')
        if concept is None:
            concept = prior.get('concept')
        inherited = True
    out['domain'], out['concept'] = domain, concept
    if inherited:
        out['flags'].append('inherited_from_prior')

    if ORDINAL_PREV.search(q):
        out['ordinal'] = 2
    elif ORDINAL_FIRST.search(q):
        out['ordinal'] = 1
    out['attribute'] = _attribute(domain, low)
    if domain == 'death' and WHEN.search(q):
        out['attribute'] = 'death_date'
    elif domain == 'death':
        death_word = bool(re.search(r'\b(?:deceased|dead|died|die)\b', low))
        living_word = bool(re.search(r'\balive\b|\bliving\b', low))
        if death_word and not living_word:
            out['attribute'] = 'deceased'
        if not (death_word and living_word) and re.search(r'\bnot\s+(?:deceased|dead|alive|living|died|die)\b', low):
            out['flags'].append('negated_status')
    if domain == 'fill' and re.search(r'\b(?:(?:drug|medication|therapeutic) classes|classes of (?:drugs|medications))\b', low):
        out['attribute'] = 'drug_class'
        out['flags'].append('distinct_drug_class')
    if domain == 'inpatient' and re.search(r'\b(?:most recent|latest|last)\s+(?:hospital\s+)?discharge\b', low):
        out['flags'].append('order_by_discharge')
    out['intent'] = _intent(domain, q, low, out, prior if inherited else None)
    if controlled:
        out['flags'].append('condition_control')
        out['intent'] = 'trend' if CONTROL_DIRECTION.search(q) and not out.get('ordinal') else 'latest_value'
    if domain == 'lab' and isinstance(concept, dict):
        comparisons = explicit_comparisons(low, concept.get('concepts') or [concept.get('value')])
        if comparisons and asks_yes_no(q):
            latest_scope = out['mode'] == 'latest' and not out.get('ordinal') and not COMPARISON_NONLATEST.search(low)
            prior_point = (not inherited or (prior.get('intent') == 'latest_value' and
                           prior.get('mode', 'latest') == 'latest' and not prior.get('ordinal')))
            if latest_scope and prior_point:
                out.update(intent='compare_values', comparisons=comparisons)
            else:
                out['gaps'].append('comparison_scope')  # another proposer must execute historical / universal semantics
    if domain == 'fill' and re.search(r'\b(?:pdc|proportion of days|days covered|adherence|adherent)\b', low):
        out['attribute'], out['intent'] = 'pdc', 'latest_value'
    if domain == 'prescription' and NEVER_FILLED.search(q):
        out['flags'].append('not_filled')
        out['intent'] = 'list'
    if domain == 'lab' and out['ordinal'] == 2 or (domain == 'lab' and (out['intent'] in ('trend', 'timeline')
                                                                         or (out['window'] and out['intent'] in ('status', 'count_in_window')))):
        out['mode'] = 'any'
    if domain == 'lab' and out['intent'] == 'trend' and CHANGE_AMOUNT.search(q):
        out['flags'].append('delta')
    # gaps: the slots a complete intent needs
    if out['domain'] is None:
        out['gaps'].append('domain')
    if out['intent'] is None:
        out['gaps'].append('intent')
    if out['domain'] == 'lab' and not out['concept']:
        out['gaps'].append('concept')
    if out['domain'] in ('immunization', 'screening') and out['intent'] != 'list' and not out['concept']:
        out['gaps'].append('concept')
    if out['domain'] == 'medication' and out['intent'] == 'status' and not out['concept']:
        out['gaps'].append('concept')
    if out['domain'] == 'condition' and out['intent'] == 'status' and not out['concept']:
        out['gaps'].append('concept')
    return out


def complete(it):
    """Recompute `gaps` from the slots now filled (after System One chose a domain, the intent and concept may follow by default)."""
    domain = it.get('domain')
    if domain and not it.get('intent') and domain not in ('summary', 'notes'):
        it['intent'] = _intent(domain, it.get('question', ''), it.get('question', '').lower(), it, None)
    gaps = []
    if not domain:
        gaps.append('domain')
    if not it.get('intent'):
        gaps.append('intent')
    if domain == 'lab' and not it.get('concept'):
        gaps.append('concept')
    if domain in ('immunization', 'screening') and it.get('intent') != 'list' and not it.get('concept'):
        gaps.append('concept')
    if domain in ('medication', 'condition') and it.get('intent') == 'status' and not it.get('concept'):
        gaps.append('concept')
    it['gaps'] = gaps
    return it


def _specific_domain(low, lexicon):
    """A summary request that also names a specific thing ("summarize the A1c trend") is a specific question."""
    return any(re.search(p, low, re.I) for p, _ in LAB_TERMS) or bool(_drug(low, lexicon))


DOMAIN_RULES = [  # first match wins; order encodes specificity
    ('care_gap', r'\bcare gaps?\b|gaps? in care|\boverdue\b|\bdue for\b|\bhedis\b|quality measures?|\bmeasure gaps?\b|\bopen gaps?\b|\bgaps\b'),
    ('readmission', r'\breadmi\w*'),
    ('observation', r'\bobservation (?:stay|status|visit|admission)s?\b|\bplaced in observation\b'),
    ('ed_visit', r'\b(?:ed|er|emergency)(?: department| room| dept\.?)? (?:visit|visits|encounter|encounters|stay|stays|use|utili[sz]ation|trips?|presentations?)\b|'
                 r'\bemergency (?:department|room)\b|\bwent to the (?:ed|er)\b|\b(?:ed|er) (?:visit|visits)\b'),
    ('returned_fill', r'\breturned? (?:fill|to stock|prescription)s?\b|\breturned\b.*\b(?:pharmacy|fills?)\b|\bfills?\b.*\breturned\b|\bnot collected\b|\bcollection\b'),
    ('prescription', r'\bprescri\w+|\brx\b|\bscripts?\b|\bnever (?:been )?filled\b|\bnot (?:been )?filled\b|\bunfilled\b'),
    ('fill', r'\bfills?\b|\bfilled\b|\brefills?\b|\bpick(?:ed|s)? up\b|\bpickups?\b|\bdispens\w+|\bpdc\b|\badheren\w+|\bproportion of days\b|\bdays covered\b'),
    ('inpatient', r'\badmission|\badmitted\b|\binpatient\b|\bhospitali[sz]\w+|\bhospital stays?\b|\bdischarg\w+|\blength of stay\b|\bin the hospital\b'),
    ('appointment', r'\bappointments?\b|\bno[- ]?shows?\b|\bmissed (?:visit|appointment)s?\b|\bcancell?ed (?:visit|appointment)s?\b|\bupcoming\b|\bscheduled\b'),
    ('immunization', r'\bvaccin\w+|\bshots?\b|\bimmuni[sz]\w+|\bflu\b|\binfluenza\b|\btdap\b|\bshingles\b|\bzoster\b'),
    ('screening', r'\bmammo\w*|\bcolonoscop\w*|\bcologuard\b|\bfit test\b|\bcolorectal\b|\bcancer screening\b|\bbreast cancer screening\b|\bscreening (?:test|result)s?\b'),
    ('wellness', r'\bannual wellness\b|\bawv\b|\bwellness visits?\b'),
    ('allergy', r'\ballerg\w+'),
    ('smoking', r'\bsmok\w+|\btobacco\b|\bcigarette\w*\b|\bvap\w+'),
    ('svi', r'\bsvi\b|social vulnerability'),
    ('sdoh_vendor', r'\bvendor\b|\bpurchased (?:sdoh|data)\b|\bsdoh (?:score|vendor|data)s?\b|\bfinancial stress\b|\bsocial isolation\b|\bbroadband\b|\bhealth literacy\b|\bhomeowner\b|\bhousehold income\b'),
    ('social_need', r'\bsocial needs?\b|\bsocial determinants?\b|\bsdoh\b|\bfood insecur\w+|\bhousing (?:insecur|instab)\w+|\btransportation\b|\bscreened positive\b|\bneeds?\b'),
    ('pharmacy_claim', r'\bpharmacy\b.*\b(?:claims?|spend|cost\w*|pa(?:y|id)|plan)\b|\bdrug (?:spend|cost)s?\b|\brx (?:spend|cost)\b'),
    ('claims', r'\bclaims?\b|\bspen[dt]\b|\bbilled\b|\ballowed\b|\bplan paid\b|\bplan pay\b|\bout[- ]of[- ]pocket\b|\bcopay\w*|\bdenied\b|\bdenials?\b'),
    ('problem_list', r'\bproblem list\b'),
    ('death', r'\bdeceased\b|\bdead\b|\bdied\b|\bdie\b|\bdeath\b|\balive\b|\bvital status\b'),
    ('practice', r'\bpractice\b|\bpcp\b|\bprimary care (?:provider|physician|doctor|clinician)\b|\battribut\w+|\bassigned (?:doctor|clinician)\b'),
    ('coverage', r'\bcoverage\b|\binsurance\b|\binsured\b|\bpayer\b|\bhealth plan\b|\bmember id\b|\benroll\w+|\bmedicare\b|\bmedicaid\b|\bline of business\b|\bmarketplace\b|\bplan\b'),
    ('demographics', r'\bhow old\b|\bage\b|\bborn\b|\bbirth\w*\b|\bdob\b|\bsex\b|\bgender\b|\brace\b|\bethnic\w*\b|\blanguage\b|\bzip\b|\bcounty\b|\baddress\b|\bmarital\b|\binterpreter\b|\bstate does\b'),
]
_DOMAIN_RX = [(d, re.compile(p, re.I)) for d, p in DOMAIN_RULES]


def _domain_and_concept(q, low, lexicon):
    # specific nouns first so "A1c while on a statin" is a lab question and "is he on a statin" is a medication one
    lab = _lab_concept(q)
    drug = _drug(q, lexicon)
    for domain, rx in _DOMAIN_RX:
        if rx.search(q):
            if domain == 'social_need' and re.fullmatch(r'.*\bneeds?\b.*', low) and not re.search(
                    r'\bsocial|sdoh|food|housing|transport|screen|utilit|safety|financial|cost', low):
                continue  # "what does X need" is not a social-needs question
            if domain == 'immunization' and re.search(r'\bshots?\b', low) and not re.search(r'\bflu|vaccin|immun|covid|tdap|zoster|shingles|pneumo', low):
                continue
            if domain in ('fill', 'prescription', 'returned_fill') and not drug and domain != 'fill':
                return domain, None
            return domain, _concept_for(domain, q, drug, lab)
    if lab:
        return 'lab', lab
    if LAB_GENERAL.search(q):
        return 'lab', {'kind': 'general', 'value': 'all'}
    if UNMEASURED.search(q):
        return 'lab', {'kind': 'unmeasured', 'value': UNMEASURED.search(q).group().lower()}
    if drug or re.search(r'\bmedications?\b|\bmeds?\b|\bdrugs?\b|\bmedicines?\b|\btaking\b|\btakes\b|\bon (?:an? |the )?\w+', low):
        if drug or re.search(r'\bmedications?\b|\bmeds?\b|\bdrugs?\b|\bmedicines?\b|\btaking\b|\btakes\b', low):
            return 'medication', drug
    cond = _find_condition(low)
    if cond or re.search(r'\bdiagnos\w+|\bconditions?\b|\bcomorbid\w*|\bchronic\b|\bproblems?\b|\bhistory of\b|\bhave (?:a |an )?\w+', low):
        if cond or re.search(r'\bdiagnos\w+|\bconditions?\b|\bcomorbid\w*|\bchronic\b|\bproblems?\b', low):
            return 'condition', ({'kind': 'condition', 'value': cond} if cond else None)
    if re.search(r'\bvisits?\b|\bseen\b|\boffice\b|\bclinic\b|\btelehealth\b', low):
        return 'visit', None
    return None, None


def _lab_concept(q):
    found = []
    for pat, concepts in LAB_TERMS:
        if re.search(pat, q, re.I):
            found.append(concepts)
    if not found:
        return None
    # hemoglobin must not swallow an A1c question ("hemoglobin A1c")
    flat = [c for cs in found for c in cs]
    if 'hba1c' in flat and 'hemoglobin' in flat:
        flat.remove('hemoglobin')
    if 'creatinine' in flat and 'uacr' in flat:
        flat.remove('creatinine')
    if 'total_cholesterol' in flat and ('ldl' in flat or 'hdl' in flat):
        flat.remove('total_cholesterol')
    if 'systolic_bp' in flat and 'diastolic_bp' in flat:
        flat = ['systolic_bp', 'diastolic_bp']
    flat = list(dict.fromkeys(flat))
    return {'kind': 'lab', 'value': flat[0] if len(flat) == 1 else flat[0], 'concepts': flat}


def _concept_for(domain, q, drug, lab):
    if domain in ('fill', 'prescription', 'returned_fill', 'medication'):
        return drug
    if domain == 'immunization':
        for pat, like in VACCINES:
            if re.search(pat, q, re.I):
                return {'kind': 'vaccine', 'value': like}
        return None
    if domain == 'screening':
        for pat, test, label in SCREENINGS:
            if re.search(pat, q, re.I):
                return {'kind': 'screening', 'value': test, 'label': label, 'colonoscopy': bool(re.search(r'colonoscop', q, re.I))}
        return None
    if domain == 'social_need':
        for pat, need in NEEDS:
            if re.search(pat, q, re.I):
                return {'kind': 'need', 'value': need}
        return None
    if domain == 'condition':
        cond = _find_condition(q.lower())
        return {'kind': 'condition', 'value': cond} if cond else None
    if domain == 'lab':
        return lab
    if domain == 'allergy':
        m = (re.search(r"(?:allergic|allergy|allergies|intoleran\w*|reaction|sensitiv\w+)\s+(?:to|of|against)\s+(?:the\s+|any\s+)?([a-z][a-z0-9 -]{2,30}?)(?=\s*(?:[?.,;]|$|\s(?:in|for|on|at|or|and)\b))", q, re.I)
             or re.search(r"\b([a-z][a-z0-9-]{3,25})\s+(?:allergy|allergies|intolerance)\b", q, re.I))
        if m and m.group(1).lower() not in ('any', 'known', 'drug', 'food', 'the', 'what', 'which', 'recorded', 'documented'):
            return {'kind': 'substance', 'value': m.group(1).lower().strip()}
        return None
    return None


ATTRIBUTES = {
    'demographics': [(r'\bhow old\b|\bage\b', 'age'), (r'\bborn\b|\bbirth\w*|\bdob\b', 'birth_date'), (r'\bsex\b|\bgender\b', 'sex'),
                     (r'\brace\b', 'race'), (r'\bethnic\w*', 'ethnicity'), (r'\blanguage\b', 'language'), (r'\binterpreter\b', 'interpreter'),
                     (r'\bzip\b', 'zip'), (r'\bcounty\b', 'county'), (r'\bstate\b', 'state'), (r'\bmarital\b', 'marital_status')],
    'coverage': [(r'\bmember id\b', 'member_id'), (r'\bmedicare\b|\bmedicaid\b|\bproduct\b', 'product'), (r'\bline of business\b', 'line_of_business'),
                 (r'\bpayer\b|\binsurance\b|\bhealth plan\b|\bplan\b', 'coverage'), (r'\benroll\w+|\bcoverage\b', 'coverage')],
    'practice': [(r'\bpcp\b|\bprimary care (?:provider|physician|doctor|clinician)\b|\bclinician\b', 'pcp'), (r'\bpractice\b|\battribut\w+', 'practice')],
    'inpatient': [(r'\bdischarg\w+', 'discharge_date'), (r'\blength of stay\b|\blos\b|\bhow long\b', 'length_of_stay')],
    'claims': [(r'\ballowed\b', 'allowed'), (r'\bbilled\b|\bcharges?\b', 'billed'), (r'\bpatient responsib\w+|\bout[- ]of[- ]pocket\b|\bcopay\w*', 'patient_responsibility'),
               (r'\bdenied\b|\bdenials?\b', 'denied'), (r'\bpaid\b|\bplan pa\w+|\bspen[dt]\b|\bcost\w*', 'paid')],
}


def _attribute(domain, low):
    for pat, attr in ATTRIBUTES.get(domain, []):
        if re.search(pat, low):
            return attr
    return None


def _intent(domain, q, low, out, prior):
    if domain is None:
        return None
    count, status = bool(HOW_MANY.search(q)), asks_yes_no(q)
    when, last = bool(WHEN.search(q)), bool(LAST.search(q))
    listish = bool(LISTISH.search(q))
    if domain == 'lab':
        if TRENDISH.search(q) and not out.get('ordinal') and not re.search(r'\bcontrol\b', low) or (
                TRENDISH.search(q) and re.search(r'better|worse|improv|worsen|compar|trend|change', low)):
            return 'trend'
        if TIMELINE.search(q) or re.search(r'\ball (?:of )?(?:his|her|their|the)\b.*\b(?:results|values|readings)', low):
            return 'timeline'
        if count:
            return 'count_in_window'
        if status and not re.search(r'\bwhat\b', low):
            return 'status'
        return 'latest_value'
    if domain in ('ed_visit', 'inpatient', 'observation', 'visit', 'appointment', 'immunization', 'screening', 'wellness', 'fill', 'returned_fill',
                  'prescription'):
        if count:
            return 'count_in_window'
        if status and (not when or re.search(r'\bif so\b|\bif yes\b|\band when\b', low)) and not re.search(r'\bwhat\b|\bwhich\b', low):
            return 'status'
        if when or last:
            return 'last_event'
        if TIMELINE.search(q) or listish:
            return 'list'
        return 'list' if domain in ('appointment', 'visit', 'prescription', 'returned_fill') else 'last_event'
    if domain == 'readmission':
        return 'count_in_window' if count else 'status' if status or EVER.search(q) else 'list'
    if domain == 'claims' or domain == 'pharmacy_claim':
        if count:
            return 'count_in_window'
        explicit_list = bool(re.search(r'\b(?:list|which|each|every)\b', low) or (re.search(r'\bshow\b', low) and not re.search(r'\b(?:total|sum|how much|combined|overall)\b', low)))
        if HOW_MUCH.search(q) and not explicit_list:
            return 'sum'
        if listish or re.search(r'\bshow|\blist\b', low):
            return 'list'
        if HOW_MUCH.search(q) or out.get('attribute'):
            return 'sum'
        return 'sum'
    if domain in ('coverage', 'practice', 'demographics', 'death', 'smoking', 'svi'):
        if domain == 'coverage' and (listish or TIMELINE.search(q)):
            return 'list'
        if domain == 'death' or (status and domain != 'demographics'):
            return 'status'
        return 'latest_value'
    if domain == 'medication':
        if out.get('concept') and (status or re.search(r'\b(?:on|taking|takes|using)\b', low)) and not listish:
            return 'status'
        if when or last:
            return 'last_event'
        return 'list'
    if domain in ('allergy', 'problem_list', 'sdoh_vendor', 'care_gap'):
        if domain == 'allergy' and out.get('concept'):
            return 'status'
        return 'list' if domain != 'sdoh_vendor' else 'latest_value'
    if domain == 'condition':
        if out.get('concept') and (status or re.search(r'\bhave|has|diagnos', low)) and not listish:
            return 'status'
        if when or last:
            return 'last_event'
        return 'list'
    if domain == 'social_need':
        if out.get('concept') and (status or EVER.search(q)):
            return 'status'
        return 'list'
    return None
