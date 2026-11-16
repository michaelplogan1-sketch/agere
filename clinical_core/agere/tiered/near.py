"""Near match: a saved recipe answers a rewording of its question.

A saved recipe (bottle) matches only its exact wording. Most questions that reach the frontier model are a new wording
of a shape already solved. This rung lets the solved shape answer them, with no frontier call:

  code         the five promoted saved shapes whose wording shares the most distinctive words with the question
  System One   picks the one that asks exactly the same thing with different values, or none
  small model  fills that shape's slots from the question
  code         every value must be of the slot's type, and a free-text value must be words of the question
  System One   confirms the saved shape with those values is exactly what the question asks
  code         runs the saved SQL and applies the same checks as any recipe

Nothing is written or computed by a model. A wrong pick, an invented value or an inexact fit sends the question on.
"""
import json
import math
import os
import re
import time
from collections import Counter

from . import bottles as bottle_store, models

K = 5
FALLBACK = os.environ.get('AGERE_TIERED_NEAR_FALLBACK', 'off') == 'on'   # off: raised recovery 42->51 of 59 but one of three wrong-match flags in 112 came through it (2026-11-15)   # try the most similar saved shape when the first pick says none
SYSTEM = '''Fill the slots of one saved analytics question from a new question that asks the same thing in other words.
Return exactly one JSON object mapping each slot name to its value taken from the new question, or {"fits": false} if the
new question asks for something the saved question does not. A year is YYYY, a month is YYYY-MM, an integer is digits, and
a text value is copied from the new question (lower case, no added words). Do not answer the question.'''
NONE = 'None of these saved questions asks exactly the same thing as the new question'
FIT = {'exact': 'The saved question with these values asks exactly what the new question asks: the same measure, the same filters, the same grouping and the same rows.',
       'not_exact': 'The new question asks for something else or something more specific: a different measure, filter, grouping, period, ranking, comparison or any further step.'}
_WORD = re.compile(r'[a-z0-9]+')
_STOP = frozenset('a an the of to in on for by with and or is are was were be do does did how many much what which who that this it at from as have has had there each per'.split())
_INDEX = {}


def enabled():
    return os.environ.get('AGERE_TIERED_NEAR', 'off') == 'on'


def _words(text):
    return [w[:-1] if len(w) > 4 and w.endswith('s') else w for w in _WORD.findall(re.sub(r'\{[a-z_][a-z0-9_]*\}', ' ', text.lower())) if w not in _STOP]


def _index(bottles):
    """Word weights over the promoted saved shapes (code only; rebuilt when the store changes)."""
    blocked = bottles.near_blocked() if hasattr(bottles, 'near_blocked') else set()   # applied to questions they do not fit, twice or more
    saved = [b for b in bottles.full() if b['status'] == 'promoted' and b['id'] not in blocked]
    key = (str(bottles.path), len(saved), max((b['id'] for b in saved), default=0), len(blocked))
    if _INDEX.get('key') == key: return _INDEX
    docs = {b['id']: Counter(_words(b['template'])) for b in saved}
    df = Counter(w for c in docs.values() for w in c)
    idf = {w: math.log((1 + len(docs)) / (1 + n)) + 1.0 for w, n in df.items()}
    vectors = {}
    for bid, c in docs.items():
        v = {w: (1 + math.log(tf)) * idf[w] for w, tf in c.items()}
        vectors[bid] = (v, math.sqrt(sum(x * x for x in v.values())) or 1.0)
    _INDEX.clear(); _INDEX.update(key=key, idf=idf, vectors=vectors, saved={b['id']: b for b in saved})
    return _INDEX


def shortlist(question, bottles, k=K):
    """Code only. -> the k promoted saved shapes most like the question, most similar first."""
    ix = _index(bottles)
    q = {w: (1 + math.log(tf)) * ix['idf'].get(w, 0.0) for w, tf in Counter(_words(question)).items()}
    norm = math.sqrt(sum(x * x for x in q.values())) or 1.0
    score = {bid: sum(x * v.get(w, 0.0) for w, x in q.items()) / (norm * vn) for bid, (v, vn) in ix['vectors'].items()}
    ranked = sorted((bid for bid in score if score[bid] > 0), key=lambda bid: (-score[bid], bid))[:k]
    return [ix['saved'][bid] for bid in ranked]


def typed(bottle, raw, question):
    """Code only. -> the slot values with their types checked, or None. A text value must be words of the question."""
    out, low = {}, ' '.join(question.lower().split())
    for name, spec in bottle['params'].items():
        value = raw.get(name)
        if value is None: return None
        text = ' '.join(str(value).lower().replace('_', ' ').split())
        kind = spec.get('type')
        if kind not in bottle_store.SLOT or not re.fullmatch(bottle_store.SLOT[kind], text): return None
        if kind == 'int': out[name] = int(text)
        elif kind == 'text':
            if text not in low.replace('_', ' '): return None  # never a value the question does not contain
            out[name] = str(value).strip().lower() if '_' in str(value) and str(value).lower().replace('_', ' ') in low.replace('_', ' ') else text
        else: out[name] = text
    return out


_COLUMNS = []


def _columns():
    """Code only. Warehouse column names of two or more words (source_stream, text_chars), read once from the schema."""
    if not _COLUMNS:
        from . import store
        db = store.connect()
        try:
            tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view') UNION SELECT name FROM sqlite_temp_master WHERE type = 'view'")]
            _COLUMNS.extend(sorted({r[1] for t in tables for r in db.execute(f'PRAGMA table_info("{t}")') if '_' in r[1] and len(r[1]) > 5}))
        except Exception:
            _COLUMNS.append('')
        finally:
            db.close()
    return [c for c in _COLUMNS if c]


def named_but_unused(question, bottle, params):
    """Code only. Tables and columns the question names in the warehouse's own spelling ("in the condition table", new_user_180)
    that the saved query never reads. A saved recipe that reads a different table is not the same question, however alike
    the words are. Slot values (heart_failure) are not names."""
    text, sql = question.lower(), str(bottle.get('sql') or '').lower()
    values = {str(v).lower() for v in (params or {}).values()}
    named = set(re.findall(r'\b[a-z][a-z0-9]*_[a-z0-9_]+\b', text)) | set(re.findall(r'\b([a-z][a-z0-9_]*) table\b', text))
    named |= {c for c in _columns() if ' ' + c.replace('_', ' ') + ' ' in ' ' + re.sub(r'[^a-z0-9 ]', ' ', text) + ' '}   # "source stream" names source_stream
    tables = set(re.findall(r'\b([a-z][a-z0-9_]*) table\b', text)) - values
    read = set(re.findall(r'\b(?:from|join)\s+([a-z_][a-z0-9_]*)', sql))
    missing = {n for n in named - values - {'the', 'a', 'this', 'that', 'each', 'one', 'same', 'latest'} if not re.search(r'\b' + re.escape(n) + r'\b', sql)}
    missing |= {t for t in tables if t not in {'the', 'a', 'this', 'that', 'same'} and t not in read}   # a table must be read, not just mentioned as a value
    return sorted(missing)


def propose(question, bottles, s1_ledger=None):
    """-> (saved shape or None, params or None, steps). Each step is a ledger step; the last says why it stopped."""
    from . import methods
    started, steps = time.monotonic(), []
    def stop(outcome, **extra):
        steps.append({'tier': 'near', 'outcome': outcome, 'tokens': 0, 'usd': 0.0, 'seconds': round(time.monotonic() - started, 2), **extra})
        return None, None, steps
    found = shortlist(question, bottles)
    if not found: return stop('no_similar_saved_shape')
    options = {str(b['id']): b['template'] for b in found}
    options['none'] = NONE
    label, p, usd = methods._system_one('New question: ' + question + '\nWhich saved question asks exactly the same thing, with only the values in braces different?', options, s1_ledger)
    steps.append({'tier': 'near', 'model': 'system_one', 'outcome': 'picked' if label and label != 'none' else 'none_picked', 'label': label, 'confidence': round(p, 4),
                  'tokens': 0, 'usd': usd, 'shortlist': [b['id'] for b in found]})
    # With many similar saved shapes the first pick says "none" too often (8 of 38 rewordings whose shape was in the shortlist,
    # measured 2026-11-14). The most similar shape then goes forward anyway: the small model can still say it does not fit and
    # the exact-fit check still has to pass, so the pick is a shortcut, not the guard.
    if not label or label == 'none':
        label = str(found[0]['id']) if FALLBACK else None
        if not label: return stop('no_saved_shape_asks_the_same')
        steps[-1]['outcome'] = 'none_picked_most_similar_goes_forward'
    bottle = next(b for b in found if str(b['id']) == label)
    params = {}
    if bottle['params']:
        text, step = models._claude(models.SLM, SYSTEM, json.dumps({'saved_question': bottle['template'], 'slots': bottle['params'], 'new_question': question}))
        steps.append({**step, 'tier': 'near', 'outcome': 'slots_filled'})
        try:
            raw = models._json(text)
        except (ValueError, json.JSONDecodeError):
            return stop('small_model_unusable', bottle_id=bottle['id'])
        if not isinstance(raw, dict) or raw.get('fits') is False: return stop('small_model_says_it_does_not_fit', bottle_id=bottle['id'])
        params = typed(bottle, raw, question)
        if params is None: return stop('slot_values_failed_type_checks', bottle_id=bottle['id'], raw=json.dumps(raw)[:160])
    missing = named_but_unused(question, bottle, params)
    if missing: return stop('question_names_something_the_saved_query_does_not_read', bottle_id=bottle['id'], names=missing)
    filled = bottle['template']
    for name, value in params.items(): filled = filled.replace('{' + name + '}', str(value))
    label, p, usd = methods._system_one('New question: ' + question + '\nSaved question with its values: ' + filled + '\nIs the saved question exactly what the new question asks?', FIT, s1_ledger)
    steps.append({'tier': 'near', 'model': 'system_one', 'check': 'exact_fit', 'outcome': 'fits' if label == 'exact' else 'not_exact_fit', 'label': label,
                  'confidence': round(p, 4), 'tokens': 0, 'usd': usd, 'bottle_id': bottle['id'], 'read_as': filled})
    if label != 'exact': return stop('saved_shape_is_not_an_exact_fit', bottle_id=bottle['id'])
    return bottle, params, steps
