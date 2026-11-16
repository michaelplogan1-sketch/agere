"""Questions with two plain readings: answer both, and ask only when they give different answers.

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


def detect(question):
    if not enabled(): return None
    q = ' '.join(str(question).split())
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
