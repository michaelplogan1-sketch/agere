"""The clarifying conversation: when a question is unclear and a person is asking, a language model talks it through with them.

Before this (to 2026-11-17) an unclear question went back to the person once, as a card of readings, and their reply was
answered without another question. Hospital questions are often unclear in two or three ways at once (which facilities,
which event, which date, which grouping), and one card cannot settle all of them. Here a language model holds the
back-and-forth:

  - it is given the customer's certified definitions (customer.py), the customer's own facilities (own_organization), the
    governed definitions (catalogue.py) and the person's saved defaults, and asks only what none of them settles;
  - it remembers every exchange in the conversation (the state travels in the saved clarification checkpoint);
  - after MAX_EXCHANGES replies it stops asking: each reading still open is computed by the normal stack and shown side
    by side;
  - when the intent is clear it hands ONE explicit question to the normal stack. It clarifies; it never computes. It sees
    no rows, and code refuses an explicit question carrying a number that appears nowhere in the conversation;
  - what the person decided is saved as their defaults (DEFAULTS, per person), so the same thing is not asked again.

The small model leads; the frontier model is asked only when the small model's turn fails code checks or the small model
says it is unsure. A provider limit stops it (the owner's standing rule); any other failure returns None, and the caller
falls back to the one-card clarification.

    begin(question, readings)      -> turn   the first turn, for a question the stack found unclear
    reply(state, said)             -> turn   the person answered; state is the previous turn's 'state'
  turn: {'action': 'ask', 'ask', 'options', 'state', 'steps'}
        {'action': 'ready', 'question', 'state', 'steps'}       one explicit question for the stack
        {'action': 'side_by_side', 'readings', 'state', 'steps'} out of exchanges: compute each reading
        None                                                     the conversation could not be held (fall back)
"""
import contextvars
import json
import os
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from . import models

MAX_EXCHANGES = int(os.environ.get('AGERE_TIERED_CLARIFY_EXCHANGES', '3'))
PERSON = contextvars.ContextVar('agere_clarify_person', default='local')   # who is asking; the local app has one person
DEFAULTS = os.environ.get('AGERE_TIERED_DEFAULTS', 'artifacts/agere-tiered-core/person-defaults.sqlite3')


def enabled():
    return os.environ.get('AGERE_TIERED_CLARIFY_CONVERSATION', 'on') == 'on'


SYSTEM = '''You hold a short clarifying conversation with a person at a hospital or health system who asked an analytics question
that can be read more than one way. Your only job is to find out exactly what they mean. You never compute, estimate or
state any figure; another system computes the answer from the warehouse once the question is explicit.

You are given: the question; the readings the system found; the customer's own certified definitions, its own facilities
and the governed definitions; the person's saved defaults (choices they made before); and the conversation so far.

Rules:
- A certified definition, the customer's own facilities, a governed definition or a saved default settles what it covers.
  Never ask about something one of them settles; apply it.
- Ask about one thing at a time, in plain words a manager would use, with 2 to 4 short options. Never ask what was
  already answered in the conversation.
- When nothing material is left open, write ONE explicit question that states every choice (which facilities, which
  patients or events, which dates and which date field, which grouping) so it can be computed with no further choice.
  Keep the person's own figures, dates and names; add none of your own.
- "readings" lists the readings still open after what the person has said, each as a full explicit question (at least
  one; when you are ready it is just your explicit question).
- "choices" lists what the person decided in this conversation that should apply to their later questions too, each as
  {"about": "<short topic, e.g. which facilities>", "choice": "<what they chose>"}. Leave it empty when they decided
  nothing general.
- Set "confident" to false if you are unsure what to do next.

Return exactly one JSON object and nothing else:
{"status": "ask" | "ready", "ask": "<your question, when status is ask>",
 "options": [{"label": "<short>", "meaning": "<what choosing it means>"}],
 "question": "<the explicit question, when status is ready>",
 "readings": [{"label": "<short>", "question": "<full explicit question>"}],
 "choices": [{"about": "...", "choice": "..."}], "confident": true}'''


def _clock():
    """The data's own date, never today's: 'last year' is the year before it (the app's first real conversations read
    'last year' as 2025 on data that runs to 2030 and answered from almost no rows)."""
    from . import store
    try:
        db = store.connect()
        try:
            return store.as_of(db)
        finally:
            db.close()
    except Exception:
        return None


def _context(question):
    from . import catalogue, customer, store
    lines = []
    today = _clock()
    if today:
        lines.append(f"The data runs to {today}; treat that as today. 'This year' is {today[:4]}, 'last year' is {int(today[:4]) - 1}, and a "
                     'relative period ("last quarter", "past 12 months") counts back from that date. Never offer a year after it.')
    theirs = customer.relevant(question)
    if theirs:
        lines.append("Customer's certified definitions (these win over the governed definitions):")
        lines += [f'- {name}: {text}' for name, text in theirs]
    own = store.own_facilities()
    if own:
        lines.append("Customer's own facilities (\"our hospitals\", \"our EDs\", \"our admissions\" mean these): " + ', '.join(own))
    governed = catalogue.relevant(question)
    if governed:
        lines.append('Governed definitions:')
        lines += [f'- {name}: {text}' for name, text in governed]
    from . import knowledge   # cited clinical definitions: what the sources settle, and where they differ (worth asking)
    clinical = knowledge.context(question)
    if clinical: lines.append(clinical)
    return '\n'.join(lines) or '(none apply to this question)'


PERIOD = re.compile(r'(?i)\b(?:19|20)\d{2}\b|\b(?:year|month|quarter|period|date range|time frame|timeframe|so far|last|this|past|since)\b')


def defaults(person=None):
    """-> [{'about', 'choice'}] the person's saved defaults, oldest first."""
    p = Path(DEFAULTS)
    if not p.exists(): return []
    with closing(sqlite3.connect(p)) as db:
        return [{'about': a, 'choice': c} for a, c in db.execute('SELECT about, choice FROM person_default WHERE person = ? ORDER BY at', (person or PERSON.get(),))]


def save_defaults(choices, person=None):
    """Save the person's decisions; a later decision on the same topic replaces the earlier one."""
    good = [c for c in choices or [] if isinstance(c, dict) and str(c.get('about') or '').strip() and str(c.get('choice') or '').strip()
            and not PERIOD.search(str(c['about']) + ' ' + str(c['choice']))]   # a period belongs to one question, never a standing default
    if not good: return 0
    p = Path(DEFAULTS)
    p.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(p)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS person_default(person TEXT, about TEXT, choice TEXT, at REAL, PRIMARY KEY(person, about))')
        db.executemany('INSERT OR REPLACE INTO person_default VALUES (?, ?, ?, ?)',
                       [(person or PERSON.get(), ' '.join(str(c['about']).lower().split())[:120], ' '.join(str(c['choice']).split())[:400], time.time()) for c in good])
    return len(good)


def _numbers(text):
    return set(re.findall(r'\d+(?:\.\d+)?', str(text).replace(',', '')))


def check(turn, state, context):
    """Code checks on one model turn. -> the reason it fails, or None. The model clarifies and never computes: an explicit
    question may carry only numbers that appear in the question, the replies, the options, the definitions or the defaults."""
    if not isinstance(turn, dict) or turn.get('status') not in ('ask', 'ready'): return 'no status'
    if turn['status'] == 'ask':
        ask, options = str(turn.get('ask') or '').strip(), turn.get('options')
        if not ask: return 'empty question'
        if not isinstance(options, list) or not 2 <= len(options) <= 4: return 'needs 2 to 4 options'
        if any(not isinstance(o, dict) or not str(o.get('label') or '').strip() for o in options): return 'an option has no label'
        if ' '.join(ask.lower().split()) in {' '.join(x['asked'].lower().split()) for x in state['exchanges']}: return 'asked again'
        return None
    q = str(turn.get('question') or '').strip()
    if len(q) < 8: return 'no explicit question'
    seen = _numbers(' '.join([state['question'], context, json.dumps(state['defaults']), json.dumps(state['exchanges']), json.dumps(state['readings'])]))
    invented = _numbers(q) - seen
    if invented: return 'explicit question carries numbers not in the conversation: ' + ', '.join(sorted(invented))
    return None


def _prompt(state, context):
    lines = [f'Question: {state["question"]}']
    if state.get('why'): lines += ['', 'Why the system needs the person: ' + str(state['why'])]
    lines += ['', 'Readings the system found:']
    lines += [f'- {r["label"]}: {r["question"]}' for r in state['readings']]
    lines += ['', context, '', 'Saved defaults for this person:']
    lines += [f'- {d["about"]}: {d["choice"]}' for d in state['defaults']] or ['(none)']
    lines += ['', 'Conversation so far:']
    lines += [f'You asked: {x["asked"]} | They replied: {x["reply"]}' for x in state['exchanges']] or ['(nothing yet)']
    left = MAX_EXCHANGES - len(state['exchanges'])
    lines += ['', f'You may ask at most {left} more question(s).' if left > 0 else 'You may not ask anything more: give status ready if you can, else list the open readings.']
    return '\n'.join(lines)


def _turn(state):
    """One model turn: the small model first, the frontier model when the small model's turn fails a check or it is unsure.
    -> (turn, steps) or (None, steps)."""
    context = _context(state['question'])
    user, steps = _prompt(state, context), []
    for model in (models.SLM, models.LLM):
        try:
            text, step = models._claude(model, SYSTEM, user, timeout=120)
        except models.ProviderLimit:
            raise
        except models.ProviderUnavailable as exc:
            steps.append({'tier': 'clarify', 'model': model, 'tokens': 0, 'usd': 0.0, 'seconds': 0.0, 'error': str(exc)[:200]})
            continue
        try:
            turn = models._json(text)
        except ValueError:
            turn = None
        why = check(turn, state, context) if turn is not None else 'no JSON'
        if why is None and turn.get('confident') is False and model != models.LLM: why = 'the small model is unsure'
        steps.append({**step, 'tier': 'clarify', 'ok': why is None, **({'reason': why} if why else {})})
        if why is None: return turn, steps
    return None, steps


def _readings(turn, fallback):
    got = [{'id': f'reading{i}', 'label': str(r.get('label') or f'Reading {i}')[:120], 'question': str(r['question']).strip()}
           for i, r in enumerate(turn.get('readings') or [], 1) if isinstance(r, dict) and str(r.get('question') or '').strip()]
    return got or fallback


def _decided(state):
    """What the person decided, to save as their defaults: the model's list, or, when it gives none, each question they
    answered, by code (the model left the list empty in the app's first conversations and the person was asked again)."""
    if state.get('choices'): return state['choices']
    return [{'about': x['asked'], 'choice': x['reply']} for x in state['exchanges'] if x.get('asked') and x.get('reply')]


def _next(state):
    turn, steps = _turn(state)
    state['steps'] = state.get('steps', []) + steps
    if turn is None: return None
    state['readings'] = _readings(turn, state['readings'])
    state['choices'] = [c for c in turn.get('choices') or [] if isinstance(c, dict)]
    if turn['status'] == 'ready':
        state['choices'] = _decided(state); save_defaults(state['choices'])
        return {'action': 'ready', 'question': str(turn['question']).strip(), 'state': state, 'steps': steps}
    if len(state['exchanges']) >= MAX_EXCHANGES:   # out of exchanges: every reading still open is computed and shown
        state['choices'] = _decided(state); save_defaults(state['choices'])
        return {'action': 'side_by_side', 'readings': state['readings'], 'state': state, 'steps': steps}
    options = [{'id': f'c{len(state["exchanges"]) + 1}_{i}', 'label': str(o['label']).strip()[:120], 'meaning': str(o.get('meaning') or o['label']).strip()[:400]}
               for i, o in enumerate(turn['options'], 1)]
    state['pending'] = {'asked': str(turn['ask']).strip(), 'options': options}
    return {'action': 'ask', 'ask': state['pending']['asked'], 'options': options, 'state': state, 'steps': steps}


def begin(question, readings, why=None):
    """readings: [{'id','label','question'}] the stack found; why: what the stack found that needs the person (shown to the
    model). -> a turn, or None (fall back to the one-card question)."""
    if not enabled(): return None
    state = {'schema': 'agere-clarify-conversation/1', 'question': question, 'person': PERSON.get(), 'defaults': defaults(), 'why': why,
             'readings': [{'id': r['id'], 'label': r['label'], 'question': r['question']} for r in readings], 'exchanges': [], 'steps': []}
    return _next(state)


def reply(state, said, option=None):
    """The person answered the pending question (a picked option or their own words). -> the next turn, or None."""
    pending = state.pop('pending', None) or {'asked': '', 'options': []}
    if option and option.get('label'): said = said or option.get('meaning') or option['label']
    state['exchanges'] = state['exchanges'] + [{'asked': pending['asked'], 'reply': ' '.join(str(said).split())[:600]}]
    return _next(state)


def spent(state):
    return round(sum(s.get('usd', 0.0) for s in state.get('steps') or []), 6), sum(1 for s in state.get('steps') or [] if s.get('model') and s.get('tokens'))
