"""Questions that ask several separate things at once. Each part goes through the tiers on its own.

  code          no joining word, or the whole question already matches a recipe or bottle: not split (no model)
  small model   lists the separate questions, each complete on its own, or says it is one question
  code          2 to 4 distinct parts; a part may not contain a number, period or patient id the question lacks
  System One    confirms the parts together ask exactly what the question asks; unconfirmed is not split

A question that is not split is answered as one question, exactly as before.
"""
import re
import time

from . import models

MAX_PARTS = 4
SIGNAL = re.compile(r'\b(and|also|plus|as well as|along with|then)\b|;|\?\s*\S', re.I)
SYSTEM = '''A user typed one message into an analytics tool. Decide whether it asks two or more SEPARATE questions, each of
which can be answered on its own with its own result. If so, write each as a complete standalone question, carrying
over any shared words it needs (the period, the population, the document type). Change nothing else and add nothing.
A list of separate counts or measures ("the number of A, the number of B and the number of C") is several questions,
one per item.
It is ONE question, not several, when: the "and" joins conditions on the same rows (patients with both X and Y), it
lists columns of one result (billed, allowed and paid amounts by payer), it compares or combines things in one result
(which month had the most, and how many), or one part cannot be answered without the other's answer.
Return exactly one JSON object: {"parts": ["<question 1>", "<question 2>", ...]} or {"parts": null} for one question.'''
OPTIONS = {'yes': 'The listed questions together ask exactly what the original message asks: nothing is dropped, added or changed, and each can be answered on its own.',
           'no': 'Something is dropped, added or changed, the original was really one question, or a listed question depends on another.'}
_TOKEN = re.compile(r'[A-Z]{2,}-[A-Z0-9]{6,}|\d[\d,.]*(?:-\d{2})?')


GDMT_WHY = re.compile(r"^why (?:isn't|is not|isnt|is) patient (?P<patient>[A-Za-z0-9-]+) (?:not )?on (?:all|full|every|each|maximum|max|maximal|optimal|complete|the|guideline|gdmt|four|4)\b.*"
                      r"(?:gdmt|guideline|heart failure|hfref|pillar)", re.I)


READMIT_WHY = re.compile(r"^why (?:was|did|is) patient (?P<patient>[A-Za-z0-9-]+) (?:re-?admitted|get re-?admitted|being re-?admitted|"
                         r"(?:come|go|end up) back (?:in|to) (?:the )?hospital|return(?:ed)? to (?:the )?hospital|back in (?:the )?hospital)"
                         r"(?: (?:to (?:the )?hospital )?on (?P<date>20\d{2}-\d{2}-\d{2}))?\b", re.I)


def latest_readmission(patient):
    """Code only. -> the admission date of the patient's most recent inpatient stay that began 1 to 30 days after an
    earlier inpatient discharge, or None. A stay repeated by a second source on the same day is not a readmission."""
    from . import store
    rows = store.run("SELECT MAX(b.admit_date) FROM stay a, stay b WHERE a.patient_id = :p AND b.patient_id = :p AND a.type = 'inpatient' AND b.type = 'inpatient' "
                     "AND a.discharge_date IS NOT NULL AND a.admit_date < b.admit_date "
                     "AND julianday(b.admit_date) - julianday(a.discharge_date) BETWEEN 1 AND 30", {'p': patient})[1]
    return rows[0][0] if rows and rows[0][0] else None


def known(question):
    """A question whose parts code already knows. -> (parts, how) or None. No model.
    "Why isn't patient X on all guideline heart-failure therapy?" is three questions the tiers can answer: what the
    fill records show for each pillar, the latest safety labs, and what reason each note states. The stack reports
    what is recorded and what is written; it does not judge what should be prescribed or what dose is tolerated."""
    readmit = READMIT_WHY.match(' '.join(question.split()).rstrip('?. '))
    if readmit:
        # "Why was patient X readmitted?" is three questions the tiers can answer: the two stays, what each document states
        # about each admission, and what is on record between them. With no date in the question, code finds the most
        # recent readmission. The stack reports what is recorded and written; it does not judge whether it was avoidable.
        pid = readmit.group('patient')
        try:
            when = readmit.group('date') or latest_readmission(pid)
        except Exception:
            when = None
        if when:
            return ([f"What are patient {pid}'s readmission on {when} and the stay before it?",
                     f"What do patient {pid}'s documents state about why the patient was admitted on {when}?",
                     f"What is on record for patient {pid} between the prior discharge and the admission on {when}?"],
                    {'method': 'readmission_question_split_by_code', 'usd': 0.0, 'tokens': 0, 'seconds': 0.0, 'readmission_date': when,
                     'date_from': 'question' if readmit.group('date') else 'most recent readmission on record'})
    m = GDMT_WHY.match(' '.join(question.split()).rstrip('?. '))
    if not m: return None
    pid = m.group('patient')
    return ([f"What is patient {pid}'s fill status for each guideline heart failure medication pillar?",
             f"What are patient {pid}'s latest potassium, eGFR, creatinine, blood pressure and heart rate?",
             f"What reason does each of patient {pid}'s documents give for a heart failure medication not being taken?",
             f"What barrier records are stored for patient {pid}'s guideline heart failure medications?"],
            {'method': 'heart_failure_therapy_question_split_by_code', 'usd': 0.0, 'tokens': 0, 'seconds': 0.0})


def candidate(question, bottles=None):
    """Code only: is it worth asking whether this is several questions?"""
    from . import recipes
    if not SIGNAL.search(question): return False
    if recipes.match(question) is not None: return False
    return not (bottles is not None and bottles.match(question, statuses=('promoted',)) is not None)


def _tokens(text):
    return {t.rstrip('.,') for t in _TOKEN.findall(text)}


def parts(question, s1_ledger=None):
    """-> (list of standalone questions or None, {'method','usd','tokens','seconds', ...})."""
    started = time.monotonic()
    def done(found, method, usd=0.0, tokens=0, **extra):
        return found, {'method': method, 'usd': usd, 'tokens': tokens, 'seconds': round(time.monotonic() - started, 2), **extra}
    text, step = models._claude(models.SLM, SYSTEM, 'Message: ' + question)
    try:
        found = models._json(text).get('parts')
    except ValueError:
        return done(None, 'small_model_unusable', step['usd'], step['tokens'])
    if not isinstance(found, list):
        return done(None, 'one_question', step['usd'], step['tokens'])
    found = [' '.join(str(p).split()) for p in found if isinstance(p, str)]
    if not 2 <= len(found) <= MAX_PARTS or len({p.lower() for p in found}) != len(found) or any(len(p) < 8 for p in found):
        return done(None, 'parts_unusable', step['usd'], step['tokens'], proposed=found[:MAX_PARTS + 1])
    invented = sorted(set().union(*(_tokens(p) for p in found)) - _tokens(question))
    if invented:
        return done(None, 'parts_add_values', step['usd'], step['tokens'], proposed=found, invented=invented[:6])
    from ..clinical import system_one
    usd, label, p = step['usd'], None, 0.0
    for name in ('perplexity', 'jev'):
        try:
            out = system_one.choice('Original message: ' + question + '\nListed questions:\n' + '\n'.join(f'{i}. {q}' for i, q in enumerate(found, 1)) +
                                    '\nDo the listed questions together ask exactly what the original message asks?',
                                    OPTIONS, [], as_of=time.strftime('%Y-%m-%d'), provider=name, ledger=s1_ledger)
        except Exception:  # an outage leaves the split unconfirmed; the question is answered as one
            continue
        usd += out.get('cost_usd') or 0.0
        label = out.get('label'); p = float((out.get('probabilities') or {}).get(label, 0.0)) if label else 0.0
        if label and p >= models.S1_THRESHOLD: break
    if label == 'yes' and p >= models.S1_THRESHOLD:
        return done(found, 'small_model_split_confirmed_by_system_one', usd, step['tokens'], confidence=round(p, 4))
    return done(None, 'split_not_confirmed', usd, step['tokens'], proposed=found, confidence=round(p, 4))
