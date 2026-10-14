"""note_fact: clinical facts found only in free text, materialised as a semantic table (bottled extraction).

A frontier model labelled a stratified sample of text units once (tools/notes_extraction/label.py, strict JSON schema with
evidence quotes); a deterministic, section-aware extractor (this module) was written against the development half and
measured on the held-out half (tools/notes_extraction/evaluate.py, Opus adjudication of disagreements). Only fact types
whose held-out precision clears the bar are materialised (ADMITTED, loaded from the cached evaluation). Runtime cost $0.

build(path) adds `note_fact` to path/semantic.sqlite3 idempotently (drop + recreate inside one transaction); call it after
semantic_layer.build. build(path, target=...) writes the table into another SQLite file instead (testing, side builds).
"""
from pathlib import Path
import json, re, sqlite3, time

VERSION = 'note-facts/2'

# stream -> text fields read. Structured streams (condition_assessments, social_history booleans) are excluded: their
# facts are already typed columns in `record`.
TEXT_SOURCES = {
    'notes': ['text'], 'imported_documents': ['text', 'course_summary'], 'telephone_messages': ['text'],
    'imaging_reports': ['text'], 'imaging_report_fragments': ['impression'], 'diagnostic_test_reports': ['text'],
    'received_diagnostic_reports': ['text'], 'inpatient_cardiac_reports': ['text'], 'received_hospital_summaries': ['text'],
    'chart_summaries': ['text'], 'advance_care_documents': ['text'], 'care_management_contacts': ['text'],
    'nursing_observations': ['text'], 'home_health_referral_packets': ['text'], 'home_health_case_summaries': ['text'],
    'medication_clarification_contacts': ['text'], 'received_procedure_reports': ['text'], 'procedure_result_communications': ['text'],
    'outside_followup_contacts': ['text'], 'transported_handoffs': ['text'], 'endoscopy_reports': ['text'],
    'referral_status_updates': ['text'], 'community_service_outcomes': ['text'], 'diagnostic_access_contacts': ['text'],
    'hie_documents': ['text'],  # v2.2 HIE-pushed ED / discharge summaries (sectioned: Principal diagnosis, Hospital course, ...)
    # v2.3 outside PDFs: readable text only (semantic scanned_document_text: PDF text layer, else front-office OCR text with OCR errors);
    # image-only scans without OCR are present but unreadable and yield nothing. field = text_source.
    'scanned_documents': ['pdf_text_layer', 'front_office_ocr'],
}
# Sections a documentation persona copies forward from the prior note (PMH/exam/ROS/social/allergies/medications). The loader
# drops facts from a line of these sections that repeats the patient's previous note word for word (without_copy_forward).
COPY_FORWARD_SECTIONS = {'ros', 'review of systems', 'pmh', 'past medical history', 'exam', 'physical exam', 'social',
                         'social history', 'allergies', 'medications'}

FACT_DEFINITIONS = {
    'ejection_fraction': 'left ventricular ejection fraction. value = percent as a numeral (midpoint of a range), or '
                         '"not_quantifiable" when the report says it cannot be measured. qualifier = "echocardiogram" when '
                         'measured on an echo/biplane/TTE study in this text, else "reported".',
    'dyspnea': 'shortness of breath / breathlessness. value = exertional | resting | unspecified. "breathless on stairs" = '
               'exertional; "no resting breathlessness" = resting, negated.',
    'edema': 'peripheral edema/swelling of legs or ankles observed or reported (not soft-tissue swelling on imaging). value = '
             'none | trace | mild | moderate | severe | present; "edema none" = value none, negated.',
    'nyha_class': 'NYHA functional class. value = I | II | III | IV.',
    'walk_distance': 'distance walked before resting. value = metres as a numeral.',
    'medication_nonadherence': 'patient not taking a medication as prescribed: not being used, stopped on own, inconsistent '
                               'use, missed doses, no supply left / could not obtain it, or reported use on fewer than 80% of '
                               'days asked. value = reason: cost | side_effects | unsure_if_needed | forgot | supply_access | '
                               'transport | unspecified. qualifier = the medication name in lowercase generic form (first '
                               'ingredient word, e.g. "lisinopril"), or null if none named.',
    'medication_days_used': 'quantified reported use, e.g. "reports use on 4 of the last 7 days". value = days used / days '
                            'asked rounded to 2 decimals. qualifier = medication (lowercase first ingredient word).',
    'social_need': 'social need stated in text. value = transport | food | housing | financial | caregiver_support | '
                   'utilities | social_isolation. negated=true when explicitly denied ("Transport difficulty reported: '
                   'False", "food access concern: False") or the support is stated present ("Helper: relative" = '
                   'caregiver_support negated; "Helper: no_regular_helper" = caregiver_support). "could not afford" = '
                   'financial.',
    'smoking_status': 'tobacco smoking status. value = current | former | never. qualifier = quantity detail if stated.',
    'goals_of_care': 'goals-of-care / advance-care decision documented. value = hospice | comfort_care | palliative_care | '
                     'dnr | dni | full_code | healthcare_proxy_named | advance_directive | limited_treatment | '
                     'full_treatment.',
    'follow_up': 'explicit follow-up instruction with a time interval. value = interval in days as a numeral (one week = 7, '
                 'three weeks = 21; for a range use the upper bound). qualifier = what/with whom in 1-3 lowercase words '
                 '(e.g. "orthopedic", "cbc", "visit").',
    'imaging_finding': 'finding in an imaging/diagnostic study report. value = fracture | dislocation | pulmonary_edema | '
                       'pleural_effusion | pericardial_effusion | cardiomegaly | consolidation | soft_tissue_swelling | '
                       'reduced_systolic_function | normal_systolic_function | valvular_disease | coronary_disease. '
                       'qualifier = anatomic site in lowercase if stated (e.g. "wrist", "distal radius").',
    'symptom': 'other symptom stated as present or explicitly absent. value = chest_pain | syncope | palpitations | bleeding | '
               'cough | fever | nausea_vomiting | diarrhea | constipation | dizziness | wheeze | fatigue | pain | '
               'reduced_oral_intake | muscle_symptoms.',
}
FACT_VALUES = {k: None for k in FACT_DEFINITIONS}
NUMERIC_FACTS = {'ejection_fraction', 'walk_distance', 'medication_days_used', 'follow_up'}

DDL = """CREATE TABLE note_fact(patient_id TEXT, record_id TEXT, stream TEXT, fact_type TEXT, value, unit TEXT,
  negated INTEGER, temporality TEXT, event_date TEXT, evidence_text TEXT, extractor TEXT, confidence REAL,
  qualifier TEXT, experiencer TEXT, source_field TEXT, span_start INTEGER, span_end INTEGER);
CREATE INDEX note_fact_type ON note_fact(fact_type, value, patient_id);
CREATE INDEX note_fact_patient ON note_fact(patient_id, fact_type);"""

SCHEMA_DOC = ('One row per clinical fact found only in free text (notes, imported documents, phone messages, imaging and '
              'diagnostic reports, chart summaries, advance-care documents, HIE ED/discharge summaries). Text copied '
              'forward unchanged from the previous note (PMH/exam/ROS/social lines) counts once, at its first note date. '
              'fact_type/value/unit with negated (1 = '
              'documented absent) and temporality (current | historical); hypothetical mentions (return precautions) are '
              'excluded. value is numeric for ejection_fraction (%), medication_days_used (fraction), walk_distance (m), '
              'follow_up (days). qualifier = medication, modality, site or follow-up target. evidence_text quotes the '
              'source span. Only fact types with held-out precision >= the admission bar are present.')


def units(record_row, extra=None):
    """(field, text) text units of one record (record_id, stream, payload json). extra = {record_id: {field: text}} for text that
    is not in the payload (outside-PDF text layers)."""
    stream, payload = record_row[1], record_row[2]
    d = json.loads(payload) if isinstance(payload, str) else payload
    if extra and record_row[0] in extra: d = {**d, **extra[record_row[0]]}
    for f in TEXT_SOURCES.get(stream, ()):
        t = d.get(f)
        if isinstance(t, str) and len(t.strip()) >= 3:
            yield f, t


# ---------------------------------------------------------------------------------------------------------------------
# Deterministic extractor
# ---------------------------------------------------------------------------------------------------------------------

EXTRACTOR = 'rules/2'
_SENT = re.compile(r'(?:[^.\n;]|\.(?=[^\s]))+(?:[.;]|\n|$)')  # '2.5 mg' does not end a sentence
HYPOTHETICAL = re.compile(
    r"\b(?:seek|urgent assessment for|requires urgent|warrants?|return (?:if|for)|call (?:if|back if|us|sooner)|contact (?:us|"
    r"this practice|the practice) (?:sooner |if |for )|if (?:not|symptoms|unable|there|you|progress)|ask(?:ed)? (?:whether|about|them)|"
    r"urgent evaluation for|call for|return earlier for|discussed|tell us about|review(?:ed)? the pharmacy balance|bring containers|do not (?:increase|change)|discuss(?:ed)? (?:safe|outpatient|"
    r"oral)|precautions?|reassess(?:ment)? (?:promptly )?if|watch for|report any|should .* prompt)\b", re.I)
HISTORICAL = re.compile(r'\b(?:earlier explanation on file|prior entry dated|history on file|previously|prior report|'
                        r'received outside discharge document|history of|prior evidence|visit on \d{4}-\d\d-\d\d recorded|'
                        r'plan recorded then|seen in the (?:emergency department|hospital) on)\b', re.I)
NUMWORDS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
            'twelve': 12, 'fourteen': 14, 'a': 1, 'an': 1}
UNIT_DAYS = {'day': 1, 'week': 7, 'month': 30, 'year': 365}


# Note-level sections ('Label:' or ALL-CAPS header). Sub-labels inside a section (CV:, Resp:, Ext:, Gen:) keep the major
# section, so 'Ext: no edema' stays in exam and 'lisinopril (cough)' under ALLERGIES stays in allergies.
MAJOR_SECTIONS = {'cc', 'chief complaint', 'hpi', 'history of present illness', 'interval history', 'ros', 'review of systems',
                  'pmh', 'past medical history', 'medications', 'current medications', 'allergies', 'social', 'social history',
                  'vitals', 'exam', 'physical exam', 'data', 'assessment and plan', 'assessment', 'plan', 'a/p', 'basis',
                  'development', 'screening', 'growth', 'immunizations', 'principal diagnosis', 'hospital course',
                  'discharge medications', 'disposition', 'follow-up', 'visit type', 's', 'o', 'admission date'}


def _sentences(text, majors=False):
    """(start, end, sentence, section[, major]) - section = nearest preceding ALL-CAPS header or 'Label:' prefix; major =
    nearest preceding note-level section (MAJOR_SECTIONS), unchanged by sub-labels."""
    section, major = '', ''
    out = []
    for m in _SENT.finditer(text):
        s = m.group(0)
        if not s.strip(): continue
        if re.fullmatch(r'\s*\d{1,2}\.\s*', s):  # '1.' item marker: run-on notes start the numbered plan without a header
            if major in COPY_FORWARD_SECTIONS or major in ('data', 'vitals', ''): major = 'assessment and plan'
            continue
        hm = re.match(r'\s*(?:\d+\.\s*)?([A-Z][A-Za-z /-]{2,30}):\s', s)
        if s.strip().isupper() and len(s.strip()) < 40:
            section = s.strip().lower()
            if section.rstrip(':') in MAJOR_SECTIONS: major = section.rstrip(':')
            continue
        if hm:
            section = hm.group(1).lower()
            if section in MAJOR_SECTIONS: major = section
        out.append((m.start(), m.end(), s, section, major) if majors else (m.start(), m.end(), s, section))
    return out


# Adjacent-letter transpositions of words the rules key on ('Nveer smoker', 'Follwo up', 'feevr') are read as the word.
# The repair keeps every length, so spans still index the original text.
_TYPO_VOCAB = ('never', 'former', 'current', 'smoker', 'follow', 'sooner', 'fever', 'fevers', 'cough', 'pain', 'edema',
               'chest', 'dyspnea', 'nausea', 'vomiting', 'diarrhea', 'fatigue', 'wheezes', 'wheezing', 'shortness', 'breath',
               'denies', 'negative', 'fracture', 'transportation', 'barriers', 'missing', 'doses', 'weeks', 'months', 'month',
               'week', 'days', 'within', 'recheck', 'insecurity', 'affording', 'groceries', 'rides', 'pitting', 'swelling')
_TYPO = {w[:i] + w[i + 1] + w[i] + w[i + 2:]: w for w in _TYPO_VOCAB for i in range(len(w) - 1) if w[i] != w[i + 1]}
_TYPO = {k: v for k, v in _TYPO.items() if k not in _TYPO_VOCAB}


def _repair_typos(text):
    def fix(m):
        w = m.group(0); r = _TYPO.get(w.lower())
        if not r: return w
        return r.upper() if w.isupper() and len(w) > 1 else (r[0].upper() + r[1:] if w[0].isupper() else r)
    return re.sub(r'[A-Za-z]+', fix, text)


def _drug(name):
    w = re.findall(r'(?<![\w-])[A-Za-z][A-Za-z-]+\b', name or '')
    stop = {'mg', 'ml', 'oral', 'tablet', 'capsule', 'patient', 'reports', 'says', 'the', 'extended', 'release', 'hr',
            'acetaminophen', 'presented', 'earlier', 'parent', 'guardian', 'pack', 'actuat', 'ratio', 'creatinine',
            'albumin', 'glucose', 'cholesterol', 'ldl', 'hemoglobin', 'potassium', 'abuse-deterrent', 'injectable', 'solution',
            'explanation', 'on', 'file', 'interval', 'started', 'current'}
    for x in w:
        if x.lower() not in stop: return x.lower()
    return 'acetaminophen' if any(x.lower() == 'acetaminophen' for x in w) else None


def _fact(ft, value, start, end, text, *, negated=False, temporality='current', qualifier=None, unit=None, confidence=0.9):
    return {'fact_type': ft, 'value': value, 'unit': unit, 'negated': bool(negated), 'temporality': temporality,
            'qualifier': qualifier, 'experiencer': 'patient', 'span_start': start, 'span_end': end,
            'evidence_text': text[start:end], 'confidence': confidence}


MED = r"(?P<med>[A-Za-z][A-Za-z0-9 ./\-\[\]]{2,90}?)"
P_DAYS = [re.compile(r"\breports? use on (?P<a>\d+) of (?:the (?:last|previous) )?(?P<b>\d+) days", re.I),
          re.compile(r"\bReported use (?P<a>\d+)/(?P<b>\d+) days", re.I),
          re.compile(r"\bReports (?P<a>\d+) of (?P<b>\d+) days(?: used| of use)", re.I),
          re.compile(r"\buse reported on (?P<a>\d+) of (?:the (?:last|previous) )?(?P<b>\d+) days", re.I)]
DRUG_MENTION = re.compile(r"\b([A-Za-z][a-z]{3,}(?:[ -][a-z]{4,})?)(?: \d+(?:\.\d+)? ?(?:MG|mg|mcg|MCG|units?)\b| home use\b)")
P_NOTTAKING = re.compile(MED + r": says not taking", re.I)
P_NOTUSED = re.compile(r"(?:Patient reports )?" + MED + r" is not being used: (?P<why>[^.]+)", re.I)
P_NOSUPPLY = re.compile(MED + r":? no supply remaining\. Reports difficulty obtaining the new prescription", re.I)
P_INCONSISTENT = re.compile(r"Reported use has been inconsistent", re.I)
P_NOTRESTARTED = re.compile(MED + r" still has not been restarted", re.I)
P_MISSED = re.compile(r"\b(?:admits|reports|has been|with) (?:missing|missed) (?:some |several |a few |multiple )?doses\b", re.I)
P_RANOUT = re.compile(r"\bran out of (?:some |her |his |their |the )?(?:medications?|pills|tablets|refills|inhalers?)(?: and did not refill(?: on time)?)?", re.I)
P_COST_REFILLS = re.compile(r"\bcost has made it hard to keep up (?:with|w/) (?:refills|medications?)", re.I)
REASONS = [(r'afford|cost|expens|price|pay', 'cost'), (r'made (?:them|him|her) feel|side effect|nause|dizz|cough|tired|muscle',
           'side_effects'), (r'unsure|still needed|not sure|thought', 'unsure_if_needed'), (r'forg', 'forgot'),
           (r'transport|ride|get to', 'transport'), (r'supply|refill|obtain|pharmacy', 'supply_access')]

P_EF = re.compile(r"(?:left ventricular ejection fraction|LVEF|\bEF\b|ejection fraction)(?: reported as| is| of| was|:)?\s*(?:estimated\s*)?(?:approximately |about |~)?"
                  r"(?P<a>\d{1,2})(?:\s*(?:-|–|to)\s*(?P<b>\d{1,2}))?\s*%", re.I)
P_EF_PRE = re.compile(r"(?:Estimated LVEF|HF[rpm]?EF,? EF)\s*(?P<a>\d{1,2})\s*%", re.I)
P_EF_NQ = re.compile(r"Ejection fraction cannot be (?:reliably )?quantified", re.I)

P_DYS = [(re.compile(r"no resting (?:breathlessness|shortness of breath|dyspnea)|without (?:resting )?(?:breathlessness|dyspnea)", re.I), 'resting', True),
         (re.compile(r"no (?:chest pain, )?resting shortness of breath", re.I), 'resting', True),
         (re.compile(r"no (?:new )?(?:crackles or )?(?:dyspnea|orthopnea)|no orthopnea", re.I), 'unspecified', True),
         (re.compile(r"(?:increasing|progressive|new|worsening) orthopnea|(?<!no )\borthopnea (?:reported|present)", re.I), 'unspecified', False),
         (re.compile(r"breathless (?:on|climbing|walking up) (?:a flight of )?(?:stairs|exertion|walking|minimal)|short(?:ness)? of breath on exertion|exertional (?:dyspnea|breathlessness)|dyspnea on exertion", re.I), 'exertional', False),
         (re.compile(r"no breathlessness at rest", re.I), 'resting', True),
         (re.compile(r"(?<!no )breathless(?:ness)? at rest|(?<!no )resting (?:breathlessness|dyspnea)(?! reported: False)|short of breath at rest", re.I), 'resting', False),
         (re.compile(r"(?<!no )(?<!new )(?:shortness of breath|dyspnea|breathlessness)(?: reported| present)", re.I), 'unspecified', False),
         (re.compile(r"(?<!no )(?:reports|reporting|with|has) (?:worsening |increased )?(?:shortness of breath|breathlessness|dyspnea)", re.I), 'unspecified', False),
         (re.compile(r"breathing easier|no breathing difficulty|denies (?:shortness of breath|dyspnea)", re.I), 'unspecified', True),
         # abbreviations and bare mentions; negation from the clause ('No SOB or chest pain', 'Resp: no SOB', 'Denies orthopnea, PND')
         (re.compile(r"(?-i:\bDOE\b)|(?-i:\bSOB\b) (?:on|with) exertion", re.I), 'exertional', False),
         (re.compile(r"(?-i:\bSOB\b)|\b(?:shortness of breath|dyspnea|breathlessness|orthopnea|PND)\b", re.I), 'unspecified', None)]
# a bare symptom word counts only as a list item after a cue ('No SOB or chest pain', 'Denies orthopnea, PND', 'presented
# with dyspnea, orthopnea', 'seen ... for dyspnea'), not in prose ('does not identify the cause of breathlessness')
_LIST_ITEMS = r"\s+(?:[A-Za-z/+-]+(?: [A-Za-z/+-]+)?(?:,|,? or|,? and)\s+)*$"
LIST_NEG = re.compile(r"\b(?:no|denies|denied|without|negative for)\b" + _LIST_ITEMS, re.I)
LIST_POS = re.compile(r"(?:\b(?:with|for|reports|c/o|has|having)\b" + _LIST_ITEMS + r")|(?:[:+]\s*$)", re.I)
P_EDEMA = re.compile(r"\b(?:edema|oedema)[: ]+(?P<sev>none|trace|mild|moderate|severe|1\+|2\+|3\+|4\+|present|absent)\b"
                     r"|(?P<sev2>trace|mild|moderate|severe|1\+|2\+|3\+|4\+|pitting|ankle|leg|bilateral)(?: pitting)? (?:leg |ankle |lower[- ]extremity |peripheral |B?LE |pedal )?edema"
                     r"|\bno (?:peripheral |leg |ankle |B?LE |pedal |lower[- ]extremity )?edema"
                     r"|(?P<pos>(?:orthopnea|congestion), edema\b|swollen (?:ankles|legs)|(?:ankle|leg) swelling|\+ ?edema\b"
                     r"|(?-i:\bB?LE\b) edema|pedal edema)", re.I)
P_EDEMA_NEG_LIST = re.compile(r"\b(?:denies|no) (?:[A-Za-z]+, )*[A-Za-z]+,? or (?:new |peripheral |leg |ankle )?edema\b", re.I)
P_NYHA = re.compile(r"NYHA(?: functional)?(?: class)?\s*(?P<c>IV|III|II|I|[1-4])\b", re.I)
P_WALK = re.compile(r"(?P<m>\d{1,5}) (?:metres|meters|m) (?:reported )?(?:walked )?before rest(?:ing)?\b|\bWalked (?P<m2>\d{1,5}) ?(?:metres|meters|m)\b", re.I)

P_SOCIAL = [
    (re.compile(r"Transport difficulty reported: (?P<b>True|False)"), 'transport'),
    (re.compile(r"food access concern: (?P<b>True|False)"), 'food'),
    (re.compile(r"Getting to appointments has been difficult"), 'transport'),
    (re.compile(r"getting to (?:the )?(?:collection site|clinic|appointments?|pharmacy) (?:is|has been) difficult", re.I), 'transport'),
    (re.compile(r"(?:daughter|son|spouse|wife|husband|family|relative|friend|caregiver) (?:visits daily|helps (?:with|at)|provides (?:care|help))", re.I), 'caregiver_support'),
    (re.compile(r"(?:Helper|Assistance): (?P<h>no_regular_helper|none|relative|spouse|friend|paid_caregiver|[a-z_]+)"), 'caregiver_support'),
    (re.compile(r"could not afford|cannot afford|can't afford|unable to afford|difficulty paying", re.I), 'financial'),
    (re.compile(r"\b(?:homeless|housing (?:instability|insecurity|insecure|concern)|unstably housed|evict)", re.I), 'housing'),
    (re.compile(r"\b(?:food insecurity|not enough (?:food|to eat)|skipping meals because)", re.I), 'food'),
    (re.compile(r"\blives alone\b", re.I), 'social_isolation'),
    (re.compile(r"\btransportation (?:barriers?|insecurity|difficult(?:y|ies)|problems?|issues?)\b|\b(?:difficulty|trouble) getting "
                r"(?:rides?|transportation)\b|\btransportation (?:benefit|assistance) (?:information|referral)", re.I), 'transport'),
    (re.compile(r"\b(?:trouble|difficulty) affording (?:groceries|food)|referred to (?:community )?food (?:resources|bank|pantry)", re.I), 'food'),
    (re.compile(r"\b(?:trouble|difficulty) affording\b|\bcost has made it hard\b", re.I), 'financial'),
]
SCREEN_NEGATIVE = re.compile(r"\s*(?:screen(?:ing)?\s*)?(?::\s*)?(?:negative|denied|absent)\b", re.I)
P_SMOKE = [(re.compile(r"\b(?:Smokes tobacco daily|current(?:ly)? smok\w*|(?<!non-)(?<!non )(?<!ex-)(?<!never )(?<!former )(?<!ex )\bsmoker\b(?! \(finding\))|smokes \d+|active smoker)", re.I), 'current'),
           (re.compile(r"\b(?:Ex-smoker|former smoker|quit smoking|stopped smoking|quit \d+ years ago|history of smoking)", re.I), 'former'),
           (re.compile(r"\b(?:never smoked|non-?smoker|never a smoker|never smoker|denies (?:tobacco|smoking))", re.I), 'never')]
P_GOC = [(re.compile(r"\b(?:enrolled in|referred to|elects?|chose|accepted|transition(?:ed)? to|discharged? to) hospice|hospice (?:care|enrollment|referral)", re.I), 'hospice'),
         (re.compile(r"\bcomfort(?:-focused| focused)? (?:care|measures|focus)\b|\bto CMO\b", re.I), 'comfort_care'),
         (re.compile(r"\bpalliative care (?:consult|referral|team|involved|follow)|Encounter for palliative care", re.I), 'palliative_care'),
         (re.compile(r"CPR in cardiac/respiratory arrest: attempt\b|would want attempted resuscitation", re.I), 'full_code'),
         (re.compile(r"\b(?:DNR|do not resuscitate|DNAR)\b", re.I), 'dnr'),
         (re.compile(r"\b(?:DNI|do not intubate)\b", re.I), 'dni'),
         (re.compile(r"\bfull code\b", re.I), 'full_code'),
         (re.compile(r"Names [A-Z][a-z]+ [A-Z][a-z]+, [a-z_]+, to make healthcare decisions|names a healthcare (?:agent|proxy)", re.I), 'healthcare_proxy_named'),
         (re.compile(r"\b(?:advance_directive|personal_directive|living_will) dated\b"), 'advance_directive'),
         (re.compile(r"offer treatment that may help me recover|want all (?:available )?treatment", re.I), 'full_treatment'),
         (re.compile(r"do not want (?:prolonged )?(?:artificial|mechanical|life-sustaining)|no (?:prolonged )?artificial feeding", re.I), 'limited_treatment')]
P_FU = re.compile(r"(?P<what>[A-Za-z][A-Za-z /-]{0,40}?)?\b(?:reassessment|follow[- ]?up|recheck|repeat|review|visit|appointment|return)"
                  r"(?: [a-z]+){0,4}? (?:is requested )?(?:within|in(?: about)?|after) (?P<n>\d+(?:\s*[-–]\s*\d+)?|one|two|three|four|five|six|seven|eight|ten|twelve|fourteen|a|an) "
                  r"(?P<u>day|week|month|year)s?\b", re.I)
P_FU2 = re.compile(r"\b(?:repeat|recheck|obtain|check) (?P<what>[A-Za-z0-9 ]{2,30}?) in (?:about )?(?P<n>\d+|one|two|three|four|six|eight|twelve) (?P<u>day|week|month)s?", re.I)
P_FU_LAB = re.compile(r"\b(?P<what>BMP|CMP|CBC|labs|lipid panel|INR|TSH|BNP|A1c) in (?:about )?(?P<n>\d+|one|two|three|four|six|eight|twelve) (?P<u>day|week|month)s?\b", re.I)
# 'Follow-up: PCP within 7 days; surgeon in 2 weeks.' (HIE summaries): each clause of the follow-up section is one target + interval
P_FU_SECTION = re.compile(r"^\s*(?:follow[- ]?up:\s*)?(?P<what>[A-Za-z][A-Za-z /-]{1,30}?) (?:within|in) (?:about )?"
                          r"(?P<n>\d+(?:\s*[-–]\s*\d+)?|one|two|three|four|six|eight|twelve) (?P<u>day|week|month)s?\b", re.I)
P_IMG = [(re.compile(r"\b(?:no|without) (?:acute |displaced )?fracture(?: or dislocation)?|negative for (?:acute |displaced )?fracture|"
                     r"(?:fracture|Salter-Harris (?:injury|fracture)) (?:not seen|not identified|ruled out)", re.I), 'fracture', True),
         (re.compile(r"(?P<site>(?:[a-z-]+ ){0,4})fracture\b(?! or dislocation)", re.I), 'fracture', False),
         (re.compile(r"\bno (?:acute )?(?:fracture or )?dislocation", re.I), 'dislocation', True),
         (re.compile(r"\bno (?:large )?pericardial effusion", re.I), 'pericardial_effusion', True),
         (re.compile(r"(?<!no )(?<!no large )\bpericardial effusion (?:present|identified|noted)", re.I), 'pericardial_effusion', False),
         (re.compile(r"\bno (?:pleural )?effusions?\b|no pleural effusion", re.I), 'pleural_effusion', True),
         (re.compile(r"(?<!no )(?:small |moderate |large |bilateral |left |right )+pleural effusions?", re.I), 'pleural_effusion', False),
         (re.compile(r"\b(?:pulmonary (?:vascular )?(?:edema|congestion)|interstitial edema|vascular congestion)", re.I), 'pulmonary_edema', False),
         (re.compile(r"\bcardiomegaly|enlarged cardiac silhouette", re.I), 'cardiomegaly', False),
         (re.compile(r"\b(?:consolidation|airspace opacity|infiltrate)\b", re.I), 'consolidation', False),
         (re.compile(r"soft[- ]tissue swelling", re.I), 'soft_tissue_swelling', False),
         (re.compile(r"\breduced (?:left ventricular )?systolic function", re.I), 'reduced_systolic_function', False),
         (re.compile(r"\b(?:normal|preserved) (?:left ventricular )?systolic function", re.I), 'normal_systolic_function', False),
         (re.compile(r"(?:no (?:large |severe )?|without )(?:severe )?valvular (?:lesion|disease)", re.I), 'valvular_disease', True),
         (re.compile(r"\b(?:two|three|single|one|multi)-vessel (?:coronary )?disease", re.I), 'coronary_disease', False)]
MODALITY = re.compile(r"\b(?:x-?rays?|CXR|CT|MRI|ultrasound|radiographs?|films?|echo\w*|imaging|scan)\b", re.I)
ENCOUNTER_REASON = re.compile(r"presented to the ED with|seen in the (?:emergency department|hospital) on|principal diagnosis", re.I)
IMAGING_STREAMS = {'imaging_reports', 'imaging_report_fragments', 'diagnostic_test_reports', 'received_diagnostic_reports',
                   'inpatient_cardiac_reports', 'received_procedure_reports'}
P_SYM = [('chest_pain', r'chest (?:pain|discomfort)|(?-i:\bCP\b)'), ('syncope', r'syncop(?:e|al)|fainting|passed out'), ('palpitations', r'palpitations'),
         ('bleeding', r'(?:visible |new )?bleeding|blood (?:in (?:the )?stool|on toilet paper|reported)|visible blood|melena|\bGI bleed\b'),
         ('pain', r'(?<!chest )\bpain(?: now)? \d+/10|\b(?:ankle|wrist|knee|hip|back|flank|dental|acute|localized|abdominal|ear|neck|shoulder|joint|pelvic) pain'
                  r'|\bepigastric (?:pain|discomfort)|\b(?:with|w/) pain\b'), ('cough', r'\bcough(?:ing)?\b'),
         ('fever', r'\bfevers?\b|(?<!a)febrile'), ('nausea_vomiting', r'nausea|vomiting|(?-i:\bN/V\b)'), ('diarrhea', r'diarrh?oea|diarrhea|loose stools|(?-i:\bN/V/D\b)'),
         ('constipation', r'constipation'), ('dizziness', r'dizz|lightheaded'), ('wheeze', r'wheez'), ('fatigue', r'fatigue|tiredness'),
         ('reduced_oral_intake', r'reduced food and fluid intake|poor oral intake|less oral intake|reduced intake|oral intake remains inadequate|not eating'),
         ('muscle_symptoms', r'muscle (?:symptoms|pain|aches|weakness)|\bmyalgia')]
P_SYM = [(v, re.compile(p, re.I)) for v, p in P_SYM]
NEG_CUE = re.compile(r"\b(?:no|not|denies|denied|without|none|negative for|free of|resolved)\b[^.;]*$", re.I)


def _negated_before(sentence, idx):
    """Negation cue earlier in the same clause (comma lists keep the cue: 'No chest pain, resting dyspnea or ...')."""
    pre = sentence[:idx]
    pre = re.split(r'\b(?:but|however|although|reports|now)\b', pre)[-1]
    return bool(NEG_CUE.search(pre))


def extract(text, stream=None, field=None):
    """Deterministic facts from one text unit. Returns list of dicts with character spans into `text` (plus `section`, the
    note-level section of the sentence, and `context`, the sentence itself, which the loader uses for copy-forward)."""
    orig, text = text, _repair_typos(text)
    facts = []
    carry = False  # a historical cue carries across ';' clauses of the same sentence
    for s0, s1, sent, section, major in _sentences(text, majors=True):
        hypo = bool(HYPOTHETICAL.search(sent))
        hist = carry or bool(HISTORICAL.search(sent)) or section in ('history on file', 'prior evidence')
        carry = hist and sent.rstrip().endswith(';')
        temporal = 'historical' if hist else 'current'

        def add(ft, value, m, **kw):
            st, en = s0 + m.start(), s0 + m.end()
            kw.setdefault('temporality', temporal)
            f = _fact(ft, value, st, en, orig, **kw)
            f['section'], f['label'], f['context'] = major, section, orig[s0:s1].strip()
            facts.append(f)

        # Medication adherence (statements are never hypothetical in this corpus' phrasing)
        for m in (m for rx in P_DAYS for m in rx.finditer(sent)):
            a, b = int(m.group('a')), int(m.group('b'))
            if b <= 0 or a > b: continue
            prior = list(DRUG_MENTION.finditer(text, max(0, s0 + m.start() - 220), s0 + m.start()))
            drug = _drug(prior[-1].group(1)) if prior else None
            add('medication_days_used', round(a / b, 2), m, qualifier=drug, unit='fraction_of_days')
            if a / b < 0.8:
                empty = re.search(r'\b(?:0|no) (?:tablets?|capsules?|doses?)(?: reported)? (?:left|remaining)', sent[m.end():m.end() + 60])
                add('medication_nonadherence', 'supply_access' if empty else 'unspecified', m, qualifier=drug, confidence=0.85)
        for m in P_NOTUSED.finditer(sent):
            why = m.group('why')
            reason = next((r for p, r in REASONS if re.search(p, why, re.I)), 'unspecified')
            add('medication_nonadherence', reason, m, qualifier=_drug(m.group('med')))
            if reason == 'cost': add('social_need', 'financial', m, confidence=0.8)
        for m in P_NOSUPPLY.finditer(sent):
            add('medication_nonadherence', 'supply_access', m, qualifier=_drug(m.group('med')), confidence=0.8)
        for m in P_NOTTAKING.finditer(sent):
            add('medication_nonadherence', 'unspecified', m, qualifier=_drug(m.group('med')), confidence=0.8)
        for m in P_NOTRESTARTED.finditer(sent):
            add('medication_nonadherence', 'unspecified', m, qualifier=_drug(m.group('med')), confidence=0.8)
        if P_INCONSISTENT.search(sent):
            add('medication_nonadherence', 'unspecified', P_INCONSISTENT.search(sent), confidence=0.8)
        for m in P_MISSED.finditer(sent):
            if not _negated_before(sent, m.start()): add('medication_nonadherence', 'unspecified', m, confidence=0.8)
        for m in P_RANOUT.finditer(sent):
            if not _negated_before(sent, m.start()): add('medication_nonadherence', 'supply_access', m, confidence=0.8)
        for m in P_COST_REFILLS.finditer(sent):
            add('medication_nonadherence', 'cost', m, confidence=0.8)
        if hypo:
            continue
        # Ejection fraction
        for m in list(P_EF.finditer(sent)) + list(P_EF_PRE.finditer(sent)):
            a = int(m.group('a')); b = m.groupdict().get('b')
            v = (a + int(b)) / 2 if b else a
            if not 5 <= v <= 85: continue
            if any(f['fact_type'] == 'ejection_fraction' and f['value'] == v and s0 <= f['span_start'] < s1 for f in facts): continue
            mod = 'echocardiogram' if (stream in IMAGING_STREAMS or re.search(r'biplane|echo|TTE|transthoracic', sent, re.I)) else 'reported'
            add('ejection_fraction', int(v) if v == int(v) else v, m, unit='%', qualifier=mod)
        m = P_EF_NQ.search(sent)
        if m: add('ejection_fraction', 'not_quantifiable', m, qualifier='echocardiogram')
        # Dyspnea
        seen, used = set(), []
        for rx, val, neg in P_DYS:
            for m in rx.finditer(sent):
                if (val, neg) in seen or any(a < m.end() and m.start() < b for a, b in used): continue
                if neg is None:  # bare mention: list item after a cue only (the SOB abbreviation is always clinical)
                    pre = sent[:m.start()]
                    if m.group(0) == 'SOB': neg_ = _negated_before(sent, m.start())
                    elif LIST_NEG.search(pre): neg_ = True
                    elif LIST_POS.search(pre): neg_ = False
                    else: continue
                elif not neg and _negated_before(sent, m.start()): neg_ = True
                else: neg_ = neg
                used.append(m.span())
                if (val, neg_) in seen: continue
                seen.add((val, neg_)); add('dyspnea', val, m, negated=neg_)
        # Edema (exclude imaging soft-tissue / pulmonary edema)
        for m in P_EDEMA.finditer(sent):
            ctx = sent[max(0, m.start() - 12):m.end() + 2].lower()
            if 'pulmonary' in ctx or 'interstitial' in ctx: continue
            sev = (m.group('sev') or m.group('sev2') or '').lower()
            if m.group(0).lower().startswith('no ') or sev in ('none', 'absent') or (m.group('pos') and _negated_before(sent, m.start())):
                add('edema', 'none', m, negated=True); continue
            sev = {'1+': 'mild', '2+': 'moderate', '3+': 'severe', '4+': 'severe', 'pitting': 'present', 'ankle': 'present',
                   'leg': 'present', 'bilateral': 'present'}.get(sev, sev or 'present')
            add('edema', sev, m)
        for m in P_EDEMA_NEG_LIST.finditer(sent):
            add('edema', 'none', m, negated=True)
        for m in P_NYHA.finditer(sent):
            c = m.group('c').upper(); c = {'1': 'I', '2': 'II', '3': 'III', '4': 'IV'}.get(c, c)
            add('nyha_class', c, m)
        for m in P_WALK.finditer(sent):
            add('walk_distance', int(m.group('m') or m.group('m2')), m, unit='m')
        # Social needs
        for rx, need in P_SOCIAL:
            for m in rx.finditer(sent):
                gd = m.groupdict()
                if gd.get('b') is not None: add('social_need', need, m, negated=gd['b'] == 'False')
                elif need == 'caregiver_support' and gd.get('h') is None: add('social_need', need, m, negated=True)
                elif gd.get('h') is not None:
                    add('social_need', need, m, negated=gd['h'] not in ('no_regular_helper', 'none'))
                elif need == 'financial' and any(f['fact_type'] == 'social_need' and f['value'] == 'financial' and
                                                 f['span_start'] <= s0 + m.start() <= f['span_end'] for f in facts):
                    continue
                elif need == 'financial' and any(f['fact_type'] == 'social_need' and f['value'] == 'financial' for f in facts
                                                 if s0 <= f['span_start'] < s1):
                    continue
                else: add('social_need', need, m, negated=_negated_before(sent, m.start()) or bool(SCREEN_NEGATIVE.match(sent, m.end())))
        # Smoking
        for rx, val in P_SMOKE:
            m = rx.search(sent)
            if m: add('smoking_status', val, m, temporality='current' if temporal == 'current' or val != 'current' else temporal)
        # Goals of care
        for rx, val in P_GOC:
            m = rx.search(sent)
            if m: add('goals_of_care', val, m, negated=_negated_before(sent, m.start()) and val not in ('dnr', 'dni'))
        # Follow-up intervals
        fu_rx = (P_FU, P_FU2, P_FU_LAB) + ((P_FU_SECTION,) if major == 'follow-up' else ())
        for rx in fu_rx:
            for m in rx.finditer(sent):
                n = m.group('n').lower()
                n = int(re.split(r'\s*[-–]\s*', n)[-1]) if n[0].isdigit() else NUMWORDS[n]
                days = n * UNIT_DAYS[m.group('u').lower()]
                what = (m.group('what') or '').strip().lower()
                what = [x for x in re.findall(r'[a-z]+', what) if x not in ('a', 'an', 'the', 'is', 'and', 'then', 'up', 'follow')]
                what = ' '.join(what[-2:]) or 'visit'
                if any(f['fact_type'] == 'follow_up' and f['value'] == days and abs(f['span_start'] - (s0 + m.start())) < 60 for f in facts):
                    continue
                add('follow_up', days, m, unit='days', qualifier=what, temporality='current', confidence=0.8)
        # Imaging findings (report streams + radiology documents only)
        # encounter reasons ('presented to the ED with a hip fracture', 'Principal diagnosis: ...') are not study findings
        # unless the sentence names the study ('X-ray negative for fracture', 'CXR with vascular congestion')
        reason_only = (stream == 'hie_documents' or ENCOUNTER_REASON.search(sent) or major == 'principal diagnosis') and not MODALITY.search(sent)
        if not reason_only and not re.search(r'\((?:disorder|finding)\)', sent):  # imaging phrasing is specific enough to read in clinical notes that quote study results
            done = set()
            for rx, val, neg in P_IMG:
                for m in rx.finditer(sent):
                    if val in done: continue
                    site = None
                    if val == 'fracture' and not neg:
                        if _negated_before(sent, m.start() + m.group(0).lower().rfind('fracture')): continue
                        words = re.findall(r'[a-z]+', (m.group('site') or '').lower())
                        words = [w for w in words if w not in ('nondisplaced', 'displaced', 'extra', 'articular', 'acute', 'a', 'the', 'minimally', 'of')]
                        site = ' '.join(words[-2:]) or None
                    neg_ = neg or (not neg and val not in ('fracture',) and _negated_before(sent, m.start()))
                    done.add(val); add('imaging_finding', val, m, negated=neg_, qualifier=site)
        # Other symptoms (not in imaging reports, not reactions in allergy lists, not medication lists)
        if stream not in IMAGING_STREAMS and major not in ('allergies', 'medications', 'current medications', 'discharge medications') \
                and not re.match(r'\s*allerg', sent, re.I):
            m = re.search(r'\bafebrile\b', sent, re.I)
            if m: add('symptom', 'fever', m, negated=True, confidence=0.75)
            for val, rx in P_SYM:
                m = rx.search(sent)
                if not m: continue
                if major != 'basis' and re.search(r'(?:chronic|history of|intractable)[a-z ]{0,12}$', sent[:m.start()], re.I): continue  # problem-list diagnosis
                if sent[max(0, m.start() - 1):m.start()] == '(' and re.search(r'allerg|intoleran', text[max(0, s0 - 200):s0 + m.start()], re.I):
                    continue  # 'lisinopril (cough)' reaction in an allergy list
                if val == 'bleeding' and re.search(r'bleeding evaluation|evaluation for bleeding|cause', sent, re.I): continue
                add('symptom', val, m, negated=_negated_before(sent, m.start()), confidence=0.75)
    seen, out = set(), []
    for f in facts:  # one fact per (type, value, negation, qualifier) per unit; first mention wins
        k = (f['fact_type'], f['value'], f['negated'], f['qualifier'] if f['fact_type'].startswith('medication') else None)
        if k not in seen: seen.add(k); out.append(f)
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------------------------------------------------

def admitted(evaluation=None):
    """Fact types admitted to the table: held-out precision >= bar, from the cached evaluation artifact."""
    if evaluation is None:
        evaluation = Path(__file__).resolve().parent / 'note_facts_admission.json'
    data = json.loads(Path(evaluation).read_text())
    return set(data['admitted']), data


def copy_forward_slot(f):
    """(fact_type, section, line label) of a fact from a copy-forward section, else None. 'Ext: no edema' -> (edema, exam, ext)."""
    if f.get('section') not in COPY_FORWARD_SECTIONS: return None
    return f['fact_type'], f['section'], f.get('label')


def copy_forward_key(f):
    """The fact plus its sentence text (typos repaired, punctuation folded): identical keys = identical evidence text."""
    return (str(f['value']), bool(f['negated']), f['qualifier'],
            re.sub(r'\W+', ' ', _repair_typos(f.get('context') or '').lower()).strip())


def without_copy_forward(facts, last):
    """Drop a note's copy-forward facts whose line repeats, word for word, what the patient's previous note with that line
    said (`last`: slot -> keys, updated in place). A changed line ('no edema' -> '2+ edema' -> 'no edema') is new evidence
    each time; an unchanged copied line counts once, at its first note date. Returns (kept facts, dropped count)."""
    by = {}
    for f in facts:
        s = copy_forward_slot(f)
        if s is not None: by.setdefault(s, set()).add(copy_forward_key(f))
    drop = {s for s, keys in by.items() if last.get(s) == keys}
    last.update(by)
    kept = [f for f in facts if copy_forward_slot(f) not in drop]
    return kept, len(facts) - len(kept)


def rows(source_db, admit, stats=None):
    """note_fact rows. Records are read per patient in note-date order; facts from copy-forward sections (PMH, exam, ROS,
    social, allergies, medications) that repeat the previous note's line are copied text, not new evidence, and are dropped
    (stats['copy_forward_dropped'])."""
    stats = stats if stats is not None else {}
    stats.setdefault('copy_forward_dropped', 0)
    cur, last = None, {}
    extra = {}
    if source_db.execute("SELECT 1 FROM sqlite_master WHERE name = 'scanned_document_text'").fetchone():
        for rid, src, text in source_db.execute('SELECT record_id, text_source, text FROM scanned_document_text'):
            extra[rid] = {src: text}
    for rid, pid, stream, ev, rec, payload in source_db.execute(
            f"SELECT record_id, patient_id, stream, event_date, recorded_date, payload FROM record WHERE stream IN "
            f"({','.join('?' * len(TEXT_SOURCES))}) ORDER BY patient_id, coalesce(event_date, recorded_date), record_id",
            list(TEXT_SOURCES)):
        if pid != cur: cur, last = pid, {}
        for field, text in units((rid, stream, payload), extra):
            facts, dropped = without_copy_forward([f for f in extract(text, stream, field) if f['fact_type'] in admit], last)
            stats['copy_forward_dropped'] += dropped
            for f in facts:
                yield (pid, rid, stream, f['fact_type'], f['value'], f['unit'], int(f['negated']), f['temporality'], ev or rec,
                       f['evidence_text'], EXTRACTOR, f['confidence'], f['qualifier'], f['experiencer'], field,
                       f['span_start'], f['span_end'])


def build(path, target=None, evaluation=None):
    """Materialise note_fact. Reads path/semantic.sqlite3 `record`; writes into `target` (default the same file).
    Idempotent: the table is dropped and rebuilt in one transaction; meta row note_fact_version records the build."""
    path = Path(path)
    started = time.perf_counter()
    src_path = path / 'semantic.sqlite3' if path.is_dir() else path
    target = Path(target) if target else src_path
    admit, data = admitted(evaluation)
    same = target.resolve() == src_path.resolve()
    out = sqlite3.connect(target, timeout=60)
    src = out if same else sqlite3.connect(f'file:{src_path}?mode=ro', uri=True)
    try:
        stats = {}
        batch = list(rows(src, admit, stats))
        with out:
            out.execute('DROP TABLE IF EXISTS note_fact')
            for stmt in DDL.split(';'):
                if stmt.strip(): out.execute(stmt)
            out.executemany('INSERT INTO note_fact VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', batch)
            out.execute('CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)')
            out.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('note_fact_version',
                        json.dumps({'version': VERSION, 'extractor': EXTRACTOR, 'admitted': sorted(admit),
                                    'precision_bar': data.get('precision_bar'), 'rows': len(batch),
                                    'copy_forward_dropped': stats['copy_forward_dropped']})))
    finally:
        if not same: src.close()
        out.close()
    return {'path': str(target), 'rows': len(batch), 'admitted': sorted(admit), 'copy_forward_dropped': stats['copy_forward_dropped'],
            'elapsed_seconds': round(time.perf_counter() - started, 1)}
