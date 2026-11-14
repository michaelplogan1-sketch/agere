"""Last rung: the frontier model takes over a question no cheaper tier answered.

It works the way an analyst with database access would: it runs read-only queries, may open documents, and then
writes the answer. Code keeps it bounded and honest:

  code   every query goes through store.run (one read-only SELECT, time-limited, inside the patient group if any)
  code   at most MAX_STEPS model turns and MAX_SECONDS overall; then it stops
  code   every number in the written answer must appear in the question or in something the model was shown;
         one correction is allowed, and numbers still unsupported are named in the answer's limitations

The answer is always marked unverified: the model saw rows and chose what to compute. Nothing is saved as a recipe.
"""
import json
import os
import time

from . import models, narrative, store

MAX_STEPS = 8
MAX_SECONDS = 150.0
MAX_ROWS_SHOWN = 60
MAX_CELL = 400
MAX_DOCUMENTS = 12
MAX_DOCUMENT_CHARS = 6000

SYSTEM = '''You answer one data question over a read-only SQLite semantic layer of healthcare data. Cheaper automatic
methods could not answer it, so you work it out step by step. On each turn return exactly one JSON object, nothing else:

{"sql": "<one SELECT (WITH allowed), no semicolon>"}            run a query; you are shown its columns and first rows
{"read": ["<record_id>", ...]}                                  open up to %d documents by record_id; you are shown their text
{"answer": "<plain-language answer, two to six sentences>",     finish
 "table": <number of the query whose result should be shown under the answer, or null>,
 "caveats": ["<anything the reader must know: what could not be determined, assumptions made>"]}

Rules:
- Compute every figure in SQL. Do not do arithmetic yourself: every number in your answer must appear in a query
  result or a document you were shown, or in the question.
- Find documents with a query on the document table (record_id, patient_id, source, document_type, event_date); read
  only the ones you need. Never select document text in SQL.
- Dates are ISO text. The data's current date is %s; use it, not today's date. Money columns are integer cents.
- Compare concept and drug_class with = or IN against exact values, never LIKE.
- If the data cannot answer the question, say exactly what is missing in the answer; do not guess.
- Answer exactly what was asked and nothing more: no extra comparisons, breakdowns or neighbouring figures.
- The table you name must be the answer itself: a query that returns exactly the rows and columns the question asks
  for. If your exploring queries returned more than that, run one final query that returns only the answer.
- Keep the stored units so figures agree with the rest of the system: cents stay integer cents, and a rate or share
  is a fraction between 0 and 1 rounded to 4 places, unless the question asks for dollars or percent.
- You have at most %d turns. Finish as soon as you can answer.

Tables and columns:
%s

Existing definitions (reuse them where they fit, so totals agree):
%s
'''


def enabled():
    return os.environ.get('AGERE_TIERED_TAKEOVER', 'off') == 'on'


def _cell(v):
    return v if not isinstance(v, str) or len(v) <= MAX_CELL else v[:MAX_CELL] + '...'


def answer(question, cohort=None, why=None):
    """-> {'text','columns','rows','sql','queries','documents','caveats','unsupported','steps','usd','tokens','seconds','model'}
    or None when the model produced no answer inside the limits."""
    started = time.monotonic()
    db = store.connect(cohort)
    try:
        system = SYSTEM % (MAX_DOCUMENTS, store.as_of(db), MAX_STEPS, store.schema_text(db), models.definitions())
        log = ['Question: ' + question]
        if why: log.append('Why the automatic methods stopped: ' + str(why)[:1200])
        if cohort is not None: log.append(f'Every patient table is already restricted to the {len(cohort)} selected patients.')
        seen, results, documents, steps, corrected = [question], [], [], [], False
        for turn in range(1, MAX_STEPS + 1):
            left = MAX_STEPS - turn
            if time.monotonic() - started > MAX_SECONDS: break
            text, step = models._claude(models.LLM, system, '\n\n'.join(log) + f'\n\nTurn {turn} of {MAX_STEPS}.' + ('' if left else ' This is your last turn: answer now.'))
            steps.append({**step, 'tier': 'takeover'})
            try:
                out = models._json(text)
                if not isinstance(out, dict): raise ValueError('not an object')
            except (ValueError, json.JSONDecodeError):
                log.append('Your last reply was not one JSON object. Return exactly one JSON object.'); continue
            if isinstance(out.get('sql'), str):
                try:
                    columns, rows = store.run(out['sql'], db=db)
                except store.RecipeError as exc:
                    log.append(f'Query failed: {str(exc)[:300]}\nSQL: {out["sql"][:600]}'); continue
                results.append({'sql': out['sql'], 'columns': columns, 'rows': rows})
                shown = [[_cell(v) for v in r] for r in rows[:MAX_ROWS_SHOWN]]
                seen.append(json.dumps([columns, shown], default=str))
                log.append(f'Query {len(results)}: {out["sql"]}\nColumns: {json.dumps(columns)}\nRows ({len(rows)} total'
                           + (f', first {MAX_ROWS_SHOWN} shown' if len(rows) > MAX_ROWS_SHOWN else '') + f'): {json.dumps(shown, default=str)}')
            elif isinstance(out.get('read'), list):
                ids = [str(i) for i in out['read']][:MAX_DOCUMENTS]
                found = store.texts(ids, db)
                for rid in ids:
                    body = (found.get(rid) or {}).get('text')
                    if body is None: log.append(f'Document {rid}: not found'); continue
                    documents.append(rid); seen.append(body[:MAX_DOCUMENT_CHARS])
                    log.append(f'Document {rid} ({found[rid]["event_date"]}):\n{body[:MAX_DOCUMENT_CHARS]}')
            elif isinstance(out.get('answer'), str) and out['answer'].strip():
                written = ' '.join(out['answer'].split())
                caveats = [str(c) for c in out.get('caveats') or [] if str(c).strip()][:6]
                allowed = set().union(*(narrative._numbers(s) for s in seen)) | {'90', '95', '99'}  # conventional interval levels
                unsupported = sorted(narrative._numbers(written) - allowed)
                if unsupported and not corrected and left:
                    corrected = True
                    log.append('Your answer contained numbers that are not in the question or in anything you were shown: ' + ', '.join(unsupported[:12])
                               + '. Compute them with a query, or answer without them.'); continue
                pick = out.get('table')
                table = results[pick - 1] if isinstance(pick, int) and not isinstance(pick, bool) and 1 <= pick <= len(results) else (results[-1] if results else {'columns': [], 'rows': [], 'sql': ''})
                return {'text': written, 'columns': table['columns'], 'rows': table['rows'], 'sql': table['sql'],
                        'queries': [r['sql'] for r in results], 'documents': list(dict.fromkeys(documents)), 'caveats': caveats,
                        'unsupported': unsupported, 'steps': steps, 'usd': sum(s['usd'] for s in steps), 'tokens': sum(s['tokens'] for s in steps),
                        'seconds': round(time.monotonic() - started, 2), 'model': models.LLM}
            else:
                log.append('Return exactly one JSON object with "sql", "read" or "answer".')
        return None
    finally:
        db.close()
