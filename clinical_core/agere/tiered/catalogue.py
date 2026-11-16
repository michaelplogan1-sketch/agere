"""Governed metric definitions: what each measure means in this warehouse, the table that holds it, and the known pitfalls.

The frontier SQL writer sees the schema and every recipe's SQL, but nothing says which of two plausible readings is the
governed one. Every wrong answer on the frozen scorecards so far was that kind of miss: a table holding only each patient's
latest value read as if it held every result, stay rows counted where whole stays were meant, a word the data does not use.
This module states the definitions once. The SQL writer is given them (switch: AGERE_TIERED_CATALOGUE, on by default), and an
answer states the definitions it relied on, so a reader can see the rule behind the number.

Definitions are code-reviewed text, not learned. Changing one changes what Agere answers; record why in the build log.
"""
import contextvars
import os
import re

ENABLED = contextvars.ContextVar('agere_catalogue', default=None)   # None: follow the environment switch


def enabled():
    flag = ENABLED.get()
    return (os.environ.get('AGERE_TIERED_CATALOGUE', 'on') == 'on') if flag is None else bool(flag)


# (name, words that bring it into an answer's stated definitions, the governed definition)
DEFINITIONS = (
    ('hospital stay', r'\bstays?\b|length of stay|\blos\b|admission|discharg',
     "A hospital stay is counted once: stay rows of one patient that repeat, overlap or begin on the day an earlier row ends "
     "are one stay, from the first admission to the last discharge; a row with no discharge date followed within 30 days by a "
     "row admitted and discharged on one day is one stay ending that day. Use this merge whenever the question counts stays, "
     "discharges, length of stay or readmissions. utilization_event rows of kind 'inpatient' are admission events, counted as "
     "recorded, and are the answer only when the question asks about utilization events."),
    ('readmission', r'readmi',
     "A readmission is an inpatient stay (counted once) that begins 1 to 30 days after the discharge of an earlier inpatient "
     "stay. 'Readmissions in a year' are readmission stays that begin in that year. The 30-day readmission rate for a year is: "
     "stays discharged in that year, each counted once, that are followed by a readmission, divided by all stays discharged in "
     "that year. The stay table's readmitted_30d flag counts duplicated rows and overstates the rate; do not use it."),
    ('lab and vital results', r'hba1c|a1c|hemoglobin|lab|result|ldl|creatinine|egfr|blood pressure|potassium|sodium|weight|bnp',
     "Lab and vital results are in measurement, one row per result, concept in lower snake case (hba1c, ldl, creatinine, "
     "egfr, systolic_bp, nt_probnp, weight). To count patients with a result, count or average results within a period, use "
     "measurement. measurement_latest holds only each patient's most recent value of a concept and answers only questions "
     "about the latest value."),
    ('spend', r'cost|spend|paid|pmpm|per member|dollar|expens|claim',
     "Spend is payer_claim.paid_cents on claims with counts_in_metrics = 1, by the year or month of service_from, divided by "
     "100 for dollars. Cost per member per month is that spend divided by member months (member_month rows) in the same period "
     "and line of business. The claim table (claim) is a separate source used for denials (ever_denied) and adjudication."),
    ('members and attribution', r'member|enrol|covered|coverage|attribut|panel|practice|line of business',
     "A member month is one member_month row. Members in a period are distinct patients with a member_month row in it. "
     "Line of business is member_month.line_of_business (commercial, medicaid, medicare, dual, medigap, cob_secondary). The "
     "attributed practice is member_month.attribution_practice_id for that month."),
    ('emergency and utilization events', r'emergency|\bed\b|er visit|observation|utilization',
     "Emergency department visits are utilization_event rows of kind 'ed_visit', dated by event_date; observation stays are "
     "kind 'observation'. A recently discharged patient's emergency arrival is an ed_visit within 30 days after a stay's "
     "discharge, on a day the patient was not in hospital."),
    ('conditions', r'condition|diagnos|diabetes|heart failure|copd|kidney|dementia|cancer|hypertension|on record',
     "Conditions recorded in the clinical record are in condition (concept in lower snake case). A condition 'on record' from "
     "any source, claims diagnoses included, is concept_patient with kind 'condition'. When the question names the condition "
     "table, use condition. Diabetes questions count diabetes, type_2_diabetes and type_1_diabetes together unless one is "
     "named."),
    ('pharmacy fills', r'fill|prescri|drug|medication|statin|glp|insulin|metformin',
     "Pharmacy fills are in fill, one row per fill, dated by fill_date; drug_class is lower snake case (statin, glp1_agonist, "
     "sglt2_inhibitor, ace_inhibitor, arb, beta_blocker, insulin, metformin, loop_diuretic)."),
    ('appointments', r'appointment|no[- ]?show|cancel|kept|schedul',
     "Appointments are in appointment, dated by scheduled_date. A no-show is final_status 'no_show'; a cancellation is "
     "final_status 'cancelled'. A no-show rate is no-shows divided by all appointments scheduled in the period."),
    ('care gaps', r'care gap|quality|measure|screening|mammogra|wellness|awv',
     "Care gaps (measure-style, not certified): among members enrolled in the year; diabetes HbA1c gap = ages 18 to 75 with "
     "diabetes and no HbA1c in the year or a latest value above 9; statin gap = ages 21 to 75 with coronary artery disease or "
     "myocardial infarction and no statin fill in the year; breast cancer screening gap = women 52 to 74 with no "
     "bilateral_screening_mammography from 1 October two years earlier to year end; annual wellness visit gap = Medicare or "
     "dual members with no wellness_visit awv = 1 in the year. Age is at 31 December from patient.birth_date."),
    ('social needs', r'social|food|transport|housing|utilit|insecur|need',
     "Social-need screens are in social_need: need is one of food, transport, housing, utilities, cost_of_care, safety; a "
     "positive screen is positive = 1."),
    ('deaths', r'death|died|mortality', "Deaths are in death, one row per patient, dated by death_date."),
)

# The warehouse's own word for common phrasings: (pattern in the question, the value to use, where it lives)
VOCABULARY = (
    (r'\bglp[- ]?1\b', 'glp1_agonist', 'fill.drug_class'),
    (r'\bsglt[- ]?2\b', 'sglt2_inhibitor', 'fill.drug_class'),
    (r'\bace[- ]inhibitor', 'ace_inhibitor', 'fill.drug_class'),
    (r'\b(?:hba1c|a1c|hemoglobin a1c|glycated)\b', 'hba1c', 'measurement.concept'),
    (r'\bheart attack|myocardial infarction\b', 'myocardial_infarction', 'condition.concept'),
    (r'\bkidney disease|\bckd\b', 'chronic_kidney_disease', 'condition.concept'),
    (r'\bcoronary (?:artery )?disease|\bcad\b', 'coronary_artery_disease', 'condition.concept'),
    (r'\bfood (?:insecurity|need)', 'food', 'social_need.need'),
    (r'\btransport(?:ation)? (?:need|insecurity|barrier)', 'transport', 'social_need.need'),
    (r'\bhousing (?:need|instability|insecurity)', 'housing', 'social_need.need'),
    (r'\bno[- ]?shows?\b', 'no_show', 'appointment.final_status'),
    (r'\bmammogra', 'bilateral_screening_mammography', 'cancer_screening.test'),
    (r'\bemergency (?:department|room)|\bed visits?\b|\ber visits?\b', 'ed_visit', 'utilization_event.kind'),
    (r'\bflu (?:shot|vaccine)', 'influenza', 'immunization.vaccine'),
)


def relevant(question):
    """-> the definitions whose words appear in the question, in catalogue order."""
    q = ' ' + str(question).lower() + ' '
    return [(name, text) for name, words, text in DEFINITIONS if re.search(words, q)]


def vocabulary(question):
    q = str(question).lower()
    return [(value, where) for pattern, value, where in VOCABULARY if re.search(pattern, q)]


def block(question):
    """The text the SQL writer is given: the governed definitions this question calls for, then its vocabulary. Empty when none apply."""
    # Only the definitions the question's words call for: proof_v5 found the whole catalogue in every prompt added about half
    # to the frontier rung's cost for a gain of three right answers in 288.
    found = relevant(question)
    if not found and not vocabulary(question): return ''
    lines = ['Governed definitions (follow these; they decide between plausible readings):']
    lines += [f'- {name}: {text}' for name, text in found]
    words = vocabulary(question)
    if words:
        lines.append("This question's words in the warehouse's own vocabulary: " + '; '.join(f'{value} ({where})' for value, where in words))
    return '\n'.join(lines)


def stated(question):
    """One line for an answer: the governed definitions that apply to this question."""
    found = relevant(question)
    return ('Definitions used: ' + ' '.join(f'{name[0].upper() + name[1:]}: {text}' for name, text in found[:3])) if found else None
