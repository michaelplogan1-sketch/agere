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
