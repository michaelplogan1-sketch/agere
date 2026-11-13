"""Follow-up questions. A follow-up such as "and in 2028?" is turned into one complete question from the previous
question, cheapest way first, and that complete question then goes through the tiers like any other.

  System One    first says whether the words are a complete question on their own; if so nothing is rewritten
  code          the follow-up is only a period, or a relative one ("next year", "three months later"): swap it into
                the previous question (no model)
  small model   rewrites the previous question with the change the follow-up asks for
  System One    confirms the rewrite is the previous question with only that change
  frontier      only when System One does not confirm: rewrites the follow-up independently; the small model's
                rewrite is used only if code finds both rewrites have the same words, otherwise it is not used

A follow-up that is not an edit of the previous question (it refers to the returned patients, asks why, or stands
on its own) is returned as None and the caller treats the words as a complete question or leaves them to the
existing path.
"""
import re
import time

from . import models

PERIOD = r'20\d{2}(?:-(?:0[1-9]|1[0-2]))?'
ONLY_PERIOD = re.compile(r'^(?:and|now|also|then|ok|okay)?[, ]*(?:what about|how about|same for|the same for)?\s*(?:in|for|during|dated in)?\s*(' + PERIOD + r')\s*\??$', re.I)
_N = {'a': 1, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10, 'eleven': 11, 'twelve': 12}
_LEAD = r'^(?:and|now|also|then|ok|okay)?[, ]*(?:what about|how about|same for|the same for|same thing for)?\s*(?:in|for|during)?\s*(?:the\s+)?'
_COUNT = r'(\d{1,2}|' + '|'.join(_N) + r')'
RELATIVE = [(re.compile(_LEAD + r'(?:next|following)\s+(year|month)\s*\??$', re.I), +1),
            (re.compile(_LEAD + r'(year|month)\s+after(?:\s+that)?\s*\??$', re.I), +1),
            (re.compile(_LEAD + r'(?:previous|prior|preceding)\s+(year|month)\s*\??$', re.I), -1),
            (re.compile(_LEAD + r'(year|month)\s+before(?:\s+that)?\s*\??$', re.I), -1),
            (re.compile(_LEAD + _COUNT + r'\s+(year|month)s?\s+(?:later|after(?:\s+that)?)\s*\??$', re.I), +1),
            (re.compile(_LEAD + _COUNT + r'\s+(year|month)s?\s+(?:earlier|before(?:\s+that)?)\s*\??$', re.I), -1)]


def relative_period(question, previous):
    """"next year", "the year before", "three months later": the new period, worked out by code from the single period
    in the previous question. -> the complete question, or None when the words are not only a relative period."""
    text = ' '.join(question.split())
    for pattern, sign in RELATIVE:
        m = pattern.match(text)
        if not m: continue
        count, unit = (m.group(1), m.group(2)) if m.lastindex == 2 else ('1', m.group(1))
        n = sign * (_N[count.lower()] if count.lower() in _N else int(count))
        old = re.findall(r'(?<![\d-])' + PERIOD + r'(?![\d-])', previous or '')
        if len(old) != 1: return None
        year, month = int(old[0][:4]), int(old[0][5:7]) if len(old[0]) == 7 else None
        if unit.lower() == 'month':
            if month is None: return None  # a month step needs a month to step from
            total = year * 12 + (month - 1) + n
            new = f'{total // 12:04d}-{total % 12 + 1:02d}'
        else:
            new = f'{year + n:04d}' + (f'-{month:02d}' if month else '')
        return previous.replace(old[0], new) if 2000 <= int(new[:4]) <= 2099 else None
    return None


BORROWED = re.compile(r"\b(?:(?:in|for|during|of)\s+)?(?:that|the same)\s+(year|month|period)\b", re.I)
POINTS_BACK = re.compile(r"\b(those|them|they|these|it|its|that|this|same|instead)\b", re.I)


def borrowed_period(question, previous):
    """"what was the readmission rate that year?": the single period of the previous question is put in place of
    "that year" by code. -> the complete question, or None when anything else in the words points back."""
    m = BORROWED.search(question)
    old = re.findall(r'(?<![\d-])' + PERIOD + r'(?![\d-])', previous or '')
    if not m or len(old) != 1: return None
    unit, is_month = m.group(1).lower(), len(old[0]) == 7
    if (unit == 'year' and is_month) or (unit == 'month' and not is_month): return None  # "that year" needs a year, "that month" a month
    text = ' '.join((question[:m.start()] + ' in ' + old[0] + question[m.end():]).split()).replace(' ?', '?')
    return None if POINTS_BACK.search(text) else text


SYSTEM = '''A user asked an analytics question and then a short follow-up. Rewrite the follow-up as ONE complete standalone
question by changing in the previous question only what the follow-up changes; keep every other word of the previous
question.
A follow-up that names a new thing replaces the thing of the same kind in the previous question (a condition, a
statement to look for, a value to report, a period, a grouping); it is not added to it. Ask for both only when the
follow-up says so ("as well", "too", "both", "in addition").
A follow-up that swaps what is reported ("show X instead", "just X") also drops the words that defined the old thing
reported (for example "share of claims that were ever denied"); keep only the grouping, the population and the period.
A follow-up that is a full question borrowing one detail ("that year", "they", "those reports") gets only that detail
filled in from the previous question; carry over nothing else (no grouping, no filter).
Return exactly one JSON object: {"standalone": "<the complete question>"}.
Return {"standalone": null} when the follow-up is not an edit of the previous question: it is already a complete
question, it refers to the patients or rows that were returned, or it asks for an explanation.'''
OPTIONS = {'yes': 'The rewritten question is the previous question with only the change the follow-up asks for.',
           'no': 'The rewritten question changes something else, drops something, adds something, or the follow-up was not an edit of the previous question.'}


STANDS_ALONE = {'complete': 'The text is a complete question or request: it names what to count, list or show, and can be answered without knowing what was asked before.',
                'depends': 'The text only makes sense as a change to an earlier question: it leaves out what is being counted, listed or shown, or points back to something earlier with words like that, they, it, those, same, instead, what about.'}

STANDS_ALONE_MIN = 0.6


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
    stepped = relative_period(question, previous)
    if stepped: return done(stepped, 'relative_period_by_code')
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
    # Second opinion. The frontier model rewrites the follow-up on its own, without seeing the small model's rewrite.
    # The rewrite is used only when code finds the two say the same thing word for word; anything else is declined.
    tokens, second = step['tokens'], None
    try:
        text, again = models._claude(models.LLM, SYSTEM, 'Previous question: ' + previous + '\nFollow-up: ' + question)
        usd += again['usd']; tokens += again['tokens']
        second = models._json(text).get('standalone')
    except ValueError:
        second = None
    except Exception as exc:
        if 'limit' in str(exc).lower(): raise
    if isinstance(second, str) and _words(second) == _words(standalone):
        return done(' '.join(standalone.split()), 'small_model_rewrite_matched_by_frontier_model', usd, tokens,
                    system_one={'label': label, 'confidence': round(p, 4)}, frontier_called=True)
    return done(None, 'rewrite_not_confirmed', usd, tokens, rejected=standalone[:200], confidence=round(p, 4),
                second_opinion=(second[:200] if isinstance(second, str) else None), frontier_called=True)


def _words(text):
    return re.findall(r'[a-z0-9]+(?:[-.][a-z0-9]+)*', str(text).lower())


def complete_question(question, previous, looks_like_follow_up, bottles=None, s1_ledger=None):
    """What to send through the tiers for a question asked inside a conversation.
    -> (complete question, how) or (None, how) when the words are an edit that could not be made safely.
    A question the zero-cost tiers recognise is complete as it is. Otherwise a follow-up is made complete from the
    previous question; words that are not an edit of the previous question are tried as they are."""
    from . import recipes
    if recipes.match(question) is not None or (bottles is not None and bottles.match(question, statuses=('promoted',)) is not None):
        return question, {'method': 'complete_as_asked', 'usd': 0.0, 'tokens': 0}
    if not (looks_like_follow_up and previous):
        return question, {'method': 'not_a_follow_up', 'usd': 0.0, 'tokens': 0}
    # Wording alone ("List ...", "Show ...") does not make a follow-up. System One says whether the words stand on
    # their own; only words that depend on the earlier question are rewritten.
    filled = borrowed_period(question, previous)
    if filled and (recipes.match(filled) is not None or (bottles is not None and bottles.match(filled, statuses=('promoted',)) is not None)):
        return filled, {'method': 'period_borrowed_by_code', 'usd': 0.0, 'tokens': 0}
    usd = 0.0
    try:
        if ONLY_PERIOD.match(' '.join(question.split())) or relative_period(question, previous):
            raise LookupError('a bare or relative period is never a complete question; code handles it')
        from ..clinical import system_one
        out = system_one.choice('Text typed by a user in an analytics conversation: "' + question + '"\nIs it a complete question on its own?',
                                STANDS_ALONE, [], as_of=time.strftime('%Y-%m-%d'), provider='perplexity', ledger=s1_ledger)
        usd = out.get('cost_usd') or 0.0
        sure = float((out.get('probabilities') or {}).get('complete', 0.0))
        if out.get('label') == 'complete' and sure >= STANDS_ALONE_MIN:  # a near-tie is not a verdict: let the rewrite decide
            return question, {'method': 'complete_by_system_one', 'usd': usd, 'tokens': 0, 'confidence': round(sure, 4)}
    except Exception:  # an outage: fall through to the rewrite, which has its own confirmation
        pass
    rewritten, how = rewrite(question, previous, s1_ledger)
    how['usd'] += usd
    if how['method'] == 'rewrite_not_confirmed': return None, how
    return rewritten or question, how
