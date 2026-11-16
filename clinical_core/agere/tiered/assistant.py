"""The strongest fair comparison: a general-purpose assistant configured the way a careful health system would set one up.

The buyer review's first objection was that the proof scorecards' frontier arm is not what a hospital would deploy: one
shot of a mid-tier model, one retry, never seeing a row. This arm is the alternative at its best:
  - the top-tier model (AGERE_ASSISTANT_MODEL, default claude-opus-5-5);
  - the full schema of the same layer, and the whole written definitions document (every governed definition and the
    vocabulary notes Agere itself works from), in every prompt;
  - a SQL tool: it may run up to MAX_STEPS read-only queries, see up to 30 rows of each, and explore before it answers;
  - it may decline, or answer a question with more than one plain reading by giving each reading.
What it does not get is Agere: no recipes, no saved answers, no routing, no code checks on its SQL, no readiness probes.
Its final SQL is run once more as written and that is its answer.

    answer(question)  -> an outcome shaped like router.answer's (status, rows, steps with tokens and usd; 'readings' when it gives several)
"""
import json
import os
import re
import sqlite3
import time

from . import catalogue, models, store

MODEL = os.environ.get('AGERE_ASSISTANT_MODEL', 'claude-opus-5-5')
MAX_STEPS = int(os.environ.get('AGERE_ASSISTANT_STEPS', '8'))
SHOW_ROWS = 30
SECONDS = 20.0

SYSTEM = '''You are a data analyst assistant connected to a health system's data in a read-only SQLite database. A colleague
asks you a question; you answer it from the data. Work like a careful analyst: look at the tables and the values before you
trust them, and check your query's output makes sense.

Each reply is ONE JSON object and nothing else, one of:
  {"action": "query", "sql": "<one SELECT>", "why": "<what you are checking>"}
      run a read-only query; you will be shown its columns and up to 30 rows. Use this to explore.
  {"action": "answer", "sql": "<one SELECT whose result is the answer>", "note": "<the definition you used, one sentence>"}
      your final answer. Its result rows are the answer, so return exactly the figures asked for.
  {"action": "answer_readings", "readings": [{"label": "<what this reading counts>", "sql": "<one SELECT>"}, ...]}
      the question has more than one plain reading that gives a different answer, and you cannot tell which is meant:
      give each reading (two or three), each with its own SQL.
  {"action": "cannot_answer", "reason": "<what data is missing>"}
      the data cannot support an answer. Say so rather than build a figure from columns that do not measure it.
You have at most %(steps)d replies in all, so leave yourself one for the answer. SQLite dialect only.'''


def document():
    """The written definitions document: every governed definition and vocabulary note, as a health system would hand it over."""
    lines = ['Definitions document (the organisation\'s written metric definitions):']
    lines += [f'- {name}: {text}' for name, _, text in catalogue.DEFINITIONS]
    voc = getattr(catalogue, 'VOCABULARY', ())
    if voc:
        lines.append('Vocabulary notes (the data\'s own words for common phrasings):')
        for entry in voc:
            lines.append('- ' + ' | '.join(str(x) for x in entry))
    return '\n'.join(lines)


def _run(db, sql):
    text = str(sql or '').strip().rstrip(';')
    if ';' in text or not re.match(r'(?is)^\s*(select|with)\b', text): raise sqlite3.Error('only one SELECT statement is allowed')
    deadline = time.monotonic() + SECONDS
    db.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 20000)
    try:
        cur = db.execute(text)
        return [d[0] for d in cur.description or []], [list(r) for r in cur.fetchall()]
    finally:
        db.set_progress_handler(None, 0)


def _shown(columns, rows):
    return json.dumps({'columns': columns, 'rows': rows[:SHOW_ROWS], 'row_count': len(rows)}, default=str)


def answer(question):
    started = time.monotonic()
    db = store.connect()
    steps, transcript = [], []
    context = f'{store.schema_text(db)}\n\n{document()}'
    def done(status, **extra):
        db.close()
        return {'status': status, 'final_tier': 'assistant', 'recipe': None, 'validation': None, 'steps': steps, 'escalations': [],
                'seconds': round(time.monotonic() - started, 2), **extra}
    for i in range(MAX_STEPS):
        user = (f'{context}\n\nQuestion: {question}\n\n' + ('\n\n'.join(transcript) if transcript else '') +
                f'\n\nReply {i + 1} of {MAX_STEPS}' + (' (your last: answer, answer_readings or cannot_answer).' if i == MAX_STEPS - 1 else '.'))
        try:
            text, meta = models._claude(MODEL, SYSTEM % {'steps': MAX_STEPS}, user, timeout=300)
        except Exception as exc:
            if 'limit' in str(exc).lower(): raise
            steps.append({'tier': 'llm', 'outcome': 'error', 'reason': str(exc)[:160], 'tokens': 0, 'usd': 0.0})
            return done('partial', rows=None, missing='the assistant call failed')
        steps.append({'tier': 'llm', 'model': MODEL, 'outcome': 'step', **meta})
        try:
            move = models._json(text)
        except Exception:
            transcript.append(f'Your reply {i + 1} was not one JSON object. Reply with one JSON object.'); continue
        act = move.get('action')
        if act == 'cannot_answer':
            return done('unanswered', rows=None, missing=str(move.get('reason'))[:300])
        if act == 'query':
            try:
                out = _shown(*_run(db, move.get('sql')))
            except sqlite3.Error as exc:
                out = f'error: {exc}'
            transcript.append(f'Your reply {i + 1}: {json.dumps(move)[:4000]}\nResult: {out[:12000]}'); continue
        if act == 'answer':
            try:
                columns, rows = _run(db, move.get('sql'))
            except sqlite3.Error as exc:
                transcript.append(f'Your reply {i + 1} answered with SQL that failed: {exc}. Fix it.'); continue
            return done('answered', columns=columns, rows=rows, sql=move.get('sql'), note=move.get('note'))
        if act == 'answer_readings':
            got = []
            for r in (move.get('readings') or [])[:3]:
                try:
                    columns, rows = _run(db, r.get('sql'))
                except sqlite3.Error:
                    columns, rows = [], None
                got.append({'id': str(r.get('label'))[:80], 'label': str(r.get('label'))[:80], 'columns': columns, 'rows': rows, 'sql': r.get('sql')})
            if len(got) >= 2:
                return done('needs_choice', rows=None, readings=got)
            transcript.append(f'Your reply {i + 1} gave fewer than two readings. Answer, or give each reading.'); continue
        transcript.append(f'Your reply {i + 1} had no known action. Use query, answer, answer_readings or cannot_answer.')
    return done('partial', rows=None, missing=f'no answer in {MAX_STEPS} replies')
