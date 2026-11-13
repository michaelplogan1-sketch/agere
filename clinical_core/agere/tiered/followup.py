"""Follow-up questions. A follow-up such as "and in 2028?" is turned into one complete question from the previous
question, cheapest way first, and that complete question then goes through the tiers like any other.

  code          the follow-up is only a period: swap it into the previous question (no model)
  small model   rewrites the previous question with the change the follow-up asks for
  System One    confirms the rewrite is the previous question with only that change; unconfirmed is not used

A follow-up that is not an edit of the previous question (it refers to the returned patients, asks why, or stands
on its own) is returned as None and the caller treats the words as a complete question or leaves them to the
existing path.
"""
import re
import time

from . import models

PERIOD = r'20\d{2}(?:-(?:0[1-9]|1[0-2]))?'
ONLY_PERIOD = re.compile(r'^(?:and|now|also|then|ok|okay)?[, ]*(?:what about|how about|same for|the same for)?\s*(?:in|for|during|dated in)?\s*(' + PERIOD + r')\s*\??$', re.I)
SYSTEM = '''A user asked an analytics question and then a short follow-up. Rewrite the follow-up as ONE complete standalone
question by changing in the previous question only what the follow-up changes; keep every other word of the previous
question. Return exactly one JSON object: {"standalone": "<the complete question>"}.
Return {"standalone": null} when the follow-up is not an edit of the previous question: it is already a complete
question, it refers to the patients or rows that were returned, or it asks for an explanation.'''
OPTIONS = {'yes': 'The rewritten question is the previous question with only the change the follow-up asks for.',
           'no': 'The rewritten question changes something else, drops something, adds something, or the follow-up was not an edit of the previous question.'}


def rewrite(question, previous, s1_ledger=None):
    """-> (standalone or None, {'method','usd','tokens','seconds', ...})."""
    started = time.monotonic()
    def done(text, method, usd=0.0, tokens=0, **extra):
        return text, {'method': method, 'usd': usd, 'tokens': tokens, 'seconds': round(time.monotonic() - started, 2), **extra}
    if not previous: return done(None, 'no_previous_question')
    m = ONLY_PERIOD.match(' '.join(question.split()))
    if m:
        new, old = m.group(1), re.findall(r'(?<![\d-])' + PERIOD + r'(?![\d-])', previous)
        if len(old) == 1 and len(old[0]) == len(new):
            return done(previous.replace(old[0], new), 'period_swapped_by_code')
    text, step = models._claude(models.SLM, SYSTEM, 'Previous question: ' + previous + '\nFollow-up: ' + question)
    try:
        standalone = models._json(text).get('standalone')
    except ValueError:
        return done(None, 'small_model_unusable', step['usd'], step['tokens'])
    if not isinstance(standalone, str) or len(standalone.strip()) < 8:
        return done(None, 'not_an_edit_of_the_previous_question', step['usd'], step['tokens'])
    from ..clinical import system_one
    usd, label, p = step['usd'], None, 0.0
    for name in ('perplexity', 'jev'):
        try:
            out = system_one.choice('Previous question: ' + previous + '\nFollow-up: ' + question + '\nRewritten question: ' + standalone +
                                    '\nIs the rewritten question the previous question with only the change the follow-up asks for?',
                                    OPTIONS, [], as_of=time.strftime('%Y-%m-%d'), provider=name, ledger=s1_ledger)
        except Exception:  # an outage leaves the rewrite unconfirmed; it is not used
            continue
        usd += out.get('cost_usd') or 0.0
        label = out.get('label'); p = float((out.get('probabilities') or {}).get(label, 0.0)) if label else 0.0
        if label and p >= models.S1_THRESHOLD: break
    if label == 'yes' and p >= models.S1_THRESHOLD:
        return done(' '.join(standalone.split()), 'small_model_rewrite_confirmed_by_system_one', usd, step['tokens'], confidence=round(p, 4))
    return done(None, 'rewrite_not_confirmed', usd, step['tokens'], rejected=standalone[:200], confidence=round(p, 4))
