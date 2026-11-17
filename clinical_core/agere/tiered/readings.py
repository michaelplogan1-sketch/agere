"""Questions with two plain readings: answer both, and ask only when they give different answers.

Rules: patients or events ("how many patients were admitted"); initial or final denials ("what was our denial rate").

Some questions are worded the way people talk but can be counted two ways, and both are reasonable. "How many patients
were admitted from the ED in 2029?" is asked by an operations lead who means admissions, and by a quality lead who means
distinct patients; on the synthetic warehouse the two are 261 and 175. A system that picks one silently is wrong for
half its users, and nothing in the answer tells them. Agere recognises these wordings by rule (no model), answers each
reading as an explicit question through the same ladder, and:
  - when the readings agree, answers once, saying so;
  - when they differ, returns both, so the user picks (the local app shows a choice card; other callers get both rows).
A reading the governed definitions settle is not a two-reading question, so only wordings no definition decides are listed.

    detect(question)   -> {'rule', 'ask', 'readings': [{'id', 'label', 'question'}]} or None
"""
import contextvars
import os
import re

ENABLED = contextvars.ContextVar('agere_readings', default=None)   # None: follow AGERE_TIERED_READINGS (on by default)


def enabled():
    v = ENABLED.get()
    return v if v is not None else os.environ.get('AGERE_TIERED_READINGS', 'on') == 'on'


# "patients were admitted" counts people or admissions. The event noun names the second reading.
_EVENT = {'admitted': 'admission', 'readmitted': 'readmission', 'hospitalized': 'hospitalization', 'hospitalised': 'hospitalisation',
          'discharged': 'discharge', 'transferred': 'transfer', 'boarded': 'boarded visit', 'seen': 'visit', 'treated': 'visit',
          'intubated': 'intubation', 'transfused': 'transfusion'}
_COUNTING = r'(?:how many|number of|count(?: of| the)?|what (?:share|percent(?:age)?|proportion|fraction) of|(?:share|percent(?:age)?|proportion|fraction) of)'
_PEOPLE = r'(?:patients|people|members|adults|children|kids|residents|individuals)'
_PATIENTS_EVENTS = re.compile(rf'\b{_COUNTING}\s+(?:our\s+|the\s+|all\s+)?(?:[a-z-]+\s+){{0,3}}?{_PEOPLE}\s+(?:who\s+)?((?:were|was|got|have been|had been)\s+(?:\w+\s+)?)?'
                              rf'({"|".join(_EVENT)})\b', re.I)
# Without "were", a participle followed by another verb only describes the patients: "patients seen by cardiology had a stay".
_LATER_VERB = re.compile(r'\b(?:had|have|has|were|was|are|is|with|without|who)\b', re.I)
# Wording that already settles the unit.
_SETTLED = re.compile(r'\b(?:distinct|unique|different|individual|each (?:patient|person|member)|per (?:patient|person|member)|once|separately|'
                      r'more than once|at least once|every (?:admission|visit|stay|discharge)|times)\b', re.I)


# "Denial rate" names three HFMA measures; two of them (initial and final) are on every claims feed and differ fourfold here.
_DENIAL = re.compile(r'\bdenial (?:rate|percentage|share)s?\b|\b(?:share|percent(?:age)?|proportion|fraction|rate) of (?:our |the )?claims (?:were |was |that were |get |got )?denied\b|'
                     r'\bhow many (?:of (?:our|the) )?claims (?:were|was|got) denied\b', re.I)
_DENIAL_SETTLED = re.compile(r'\b(?:initial(?:ly)?|first|final(?:ly)?|upheld|overturn\w*|remain\w*|still|ever|eventually|permanent\w*|remittance|write[- ]?offs?|appeal\w*|'
                             r'first_status|final_status)\b', re.I)


# Only the hospital's own revenue cycle: a question about claims in general keeps the warehouse's 'ever denied' measure.
_HOSPITAL_FRAME = re.compile(r'\b(?:our|we|us|hospital|health system|revenue cycle|billing|billed|submitted)\b', re.I)


def _denials(q):
    if not _DENIAL.search(q) or _DENIAL_SETTLED.search(q) or not _HOSPITAL_FRAME.search(q): return None
    base = q.rstrip(' ?.')
    return {'rule': 'initial_or_final_denials',
            'ask': 'Do you mean the initial denial rate (denied at first adjudication) or the final denial rate (still denied at the end)? They give different answers here.',
            'readings': [{'id': 'initial', 'label': 'Initial denials', 'question': base + '? Use initial denials: claims denied at first adjudication.',
                          'meaning': 'Claims denied the first time the payer adjudicated them, whether or not later paid'},
                         {'id': 'final', 'label': 'Final denials', 'question': base + '? Use final denials: claims still denied at their final status.',
                          'meaning': 'Claims still denied after resubmission and appeal'}]}


def detect(question):
    if not enabled(): return None
    q = ' '.join(str(question).split())
    denials = _denials(q)
    if denials: return denials
    if _SETTLED.search(q): return None
    # The first wording that is the counted thing: a participle that only describes the patients is passed over.
    m = next((m for m in _PATIENTS_EVENTS.finditer(q) if m.group(1) or not _LATER_VERB.search(q[m.end():])), None)
    if not m: return None
    noun = _EVENT[m.group(2).lower()]
    plural = noun + 's'
    rate = bool(re.search(r'\b(?:share|percent|percentage|proportion|fraction|rate)\b', q, re.I))
    base = q.rstrip(' ?.')
    if rate:
        people = base + f'? Use patients as the unit: each patient counted once, whatever their number of {plural}.'
        events = base + f'? Use {plural} as the unit: each {noun} counted, so a patient with two {plural} counts twice.'
    else:
        people = base + f'? Count each patient once, however many {plural} they had.'
        events = base + f'? Count {plural}, not patients: a patient with two {plural} counts twice.'
    return {'rule': 'patients_or_events',
            'ask': f'Do you mean how many different patients, or how many {plural}? They give different answers here.',
            'readings': [{'id': 'patients', 'label': 'Different patients', 'question': people,
                          'meaning': f'Each patient counted once, however many {plural} they had'},
                         {'id': 'events', 'label': plural[0].upper() + plural[1:], 'question': events,
                          'meaning': f'Each {noun} counted, so a patient with two counts twice'}]}


def same(a, b, question=''):
    """Two readings agree when their results hold the same numbers (to 4 decimal places); labels, column names and the
    question's own years (returned as a number by one reading and as text by the other) are not compared."""
    years = {int(y) for y in re.findall(r'\b(?:19|20)\d\d\b', str(question))}
    def norm(rows):
        return [[round(float(v), 4) for v in r if isinstance(v, (int, float)) and not isinstance(v, bool) and not (isinstance(v, int) and v in years)]
                for r in rows or []]
    return norm(a) == norm(b)


# ---- candidate (off by default): value-of-information clarification ------------------------------------------------------
# From the research branch codex/research-voi-20261106 (clinical/clarification.py score(), clinical_core/information_value.py),
# ported unchanged in its arithmetic: ask only when a perfect reply is expected to remove more answer change than the
# interruption costs. The readings' headline figures (the last number on each row) are the alternative outcomes, here
# computed rather than projected; the prior over readings is uniform (no observed asker data); distance is the branch's
# |a - b| / max(1, |a|, |b|), averaged over rows; INTERRUPTION_COST 0.05 is the branch's constant, set before this trial.
# With two readings this asks when the distance is above 0.1. Switch: AGERE_TIERED_VOI=on (or VOI.set(True)).
VOI = contextvars.ContextVar('agere_readings_voi', default=None)
INTERRUPTION_COST = 0.05


def voi_enabled():
    v = VOI.get()
    return v if v is not None else os.environ.get('AGERE_TIERED_VOI', 'off') == 'on'


def _headline(rows):
    out = []
    for r in rows or []:
        nums = [float(v) for v in r if isinstance(v, (int, float)) and not isinstance(v, bool)]
        out.append(nums[-1] if nums else None)
    return out


def _distance(a, b):
    ha, hb = _headline(a), _headline(b)
    if len(ha) != len(hb) or None in ha or None in hb or not ha: return 1.0   # different shapes: the readings change the answer
    return sum(abs(x - y) / max(1.0, abs(x), abs(y)) for x, y in zip(ha, hb)) / len(ha)


def value_of_asking(rows_by_reading):
    """-> {'gross', 'net', 'ask', 'assume', 'distances'}: the expected answer change a perfect reply removes, less the interruption
    cost; 'assume' is the reading answered when not asking (the one with least expected change; the first on a tie)."""
    k = len(rows_by_reading)
    d = [[_distance(rows_by_reading[i], rows_by_reading[j]) for j in range(k)] for i in range(k)]
    expected = [sum(d[i]) / k for i in range(k)]   # uniform prior over what the asker meant
    assume = min(range(k), key=lambda i: (expected[i], i))
    gross = expected[assume]
    net = gross - INTERRUPTION_COST
    return {'gross': round(gross, 6), 'net': round(net, 6), 'ask': net > 0, 'assume': assume, 'distances': [[round(x, 6) for x in row] for row in d]}
