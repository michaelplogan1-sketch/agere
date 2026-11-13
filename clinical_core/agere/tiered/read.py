"""The document-reading tier. A reading plan is a recipe whose SQL selects documents and whose `read` part is one
statement to judge against each selected document's text. Code selects the documents, fetches their text,
counts the judgments and validates the result. Models only judge one document against one statement:

  System One (Perplexity, then Jev)  yes / no with a confidence; accepted at or above the threshold
  small model (Haiku)                yes / no; a yes must quote the document verbatim, and System One must confirm
                                     that the quoted words state it, else the document goes to the frontier reader
  frontier model (Sonnet)            the same, for documents the cheaper readers could not decide

A document no reader decides stays undecided and the answer is returned as partial, never guessed.
"""
import re
import time
from concurrent.futures import ThreadPoolExecutor

from . import models, recipes, store

MAX_DOCUMENTS = 150
READERS = ('system1', 'slm', 'llm')
WORKERS = 8
OPTIONS = {'yes': 'The document states this.', 'no': 'The document does not state this, or states the opposite or its absence.'}
READER_SYSTEM = '''Decide whether one document states one thing. Return exactly one JSON object and nothing else:
{"answer": "yes" or "no", "quote": "<for yes: the exact words copied from the document that state it; for no: empty>"}
Judge meaning, not spelling: scanned text can contain character-recognition errors. A finding that is negated, ruled
out, absent, or only about someone other than the patient is "no". Copy the quote character for character.'''


def _flat(text):
    return ' '.join(str(text).split()).lower()


def _system_one(statement, doc, ledger_path, as_of):
    from ..clinical import system_one
    usd, label, p, provider, error = 0.0, None, 0.0, None, None
    for name in ('perplexity', 'jev'):
        try:
            out = system_one.choice('Does this document state the following? ' + statement, OPTIONS,
                                    [{'id': doc['record_id'], 'text': doc['text'], 'date': doc.get('event_date')}],
                                    as_of=as_of, provider=name, ledger=ledger_path)
        except Exception as exc:  # an outage is a decline; the next reader decides
            error = f'{type(exc).__name__}: {exc}'[:160]
            continue
        usd += out.get('cost_usd') or 0.0
        label = out.get('label'); p = float((out.get('probabilities') or {}).get(label, 0.0)) if label else 0.0; provider = name
        if label in OPTIONS and p >= models.S1_THRESHOLD:
            return {'label': label, 'by': 'system1', 'model': provider, 'confidence': round(p, 4), 'usd': usd, 'tokens': 0}
    return {'label': None, 'by': 'system1', 'model': provider, 'confidence': round(p, 4), 'usd': usd, 'tokens': 0,
            'reason': error or 'below the confidence threshold'}


def _model(tier, statement, doc):
    model = models.SLM if tier == 'slm' else models.LLM
    try:
        text, step = models._claude(model, READER_SYSTEM, 'Statement: ' + statement + '\n\nDocument:\n' + doc['text'])
    except Exception as exc:
        if 'limit' in str(exc).lower(): raise
        return {'label': None, 'by': tier, 'model': model, 'usd': 0.0, 'tokens': 0, 'reason': f'{type(exc).__name__}: {exc}'[:160]}
    base = {'by': tier, 'model': model, 'usd': step['usd'], 'tokens': step['tokens']}
    try:
        out = models._json(text)
    except ValueError as exc:
        return {**base, 'label': None, 'reason': str(exc)[:120]}
    label, quote = out.get('answer'), str(out.get('quote') or '').strip()
    if label == 'no': return {**base, 'label': 'no'}
    if label == 'yes' and quote and _flat(quote) in _flat(doc['text']): return {**base, 'label': 'yes', 'quote': quote}
    return {**base, 'label': None, 'reason': 'a yes without a verbatim quote from the document' if label == 'yes' else 'no usable answer'}


def judge(statement, doc, *, readers=READERS, s1_ledger=None, as_of=None):
    """One document against one statement, cheapest reader first. -> {'label': yes|no|None, 'by', 'attempts': [...]}."""
    attempts = []
    clock = as_of or time.strftime('%Y-%m-%d')
    for tier in readers:
        got = _system_one(statement, doc, s1_ledger, clock) if tier == 'system1' else _model(tier, statement, doc)
        if tier == 'slm' and got['label'] == 'yes' and 'system1' in readers and 'llm' in readers:
            # The small model's yes stands only when System One confirms that the quoted words state it; a quote can
            # be verbatim and still say something else ("home health" is not "home"). Unconfirmed goes to the frontier reader.
            check = _system_one(statement, {'record_id': doc['record_id'], 'text': got['quote'], 'event_date': doc.get('event_date')}, s1_ledger, clock)
            got['usd'] += check['usd']
            if check['label'] != 'yes':
                got = {**got, 'label': None, 'reason': 'System One did not confirm that the quoted words state it', 'unconfirmed_quote': got.pop('quote')}
        attempts.append(got)
        if got['label']: break
    last = attempts[-1] if attempts else {'label': None, 'by': None}
    return {'record_id': doc['record_id'], 'label': last['label'], 'by': last['by'] if last['label'] else None,
            **({'quote': last['quote']} if last.get('quote') else {}), 'attempts': attempts}


def execute(question, sql, params, read, db, *, readers=READERS, s1_ledger=None):
    """Run one reading plan. -> (columns, rows, validation, steps, evidence). Raises store.RecipeError when the plan cannot run."""
    statement, mode = str((read or {}).get('statement') or '').strip(), (read or {}).get('mode') or 'count'
    for name, value in (params or {}).items():
        statement = statement.replace('{' + name + '}', str(value))
    if len(statement) < 3 or mode not in ('count', 'list'): raise store.RecipeError('A reading plan needs a statement and mode count or list')
    used = set(re.findall(r'(?<![:\w]):([a-z_][a-z0-9_]*)', sql))
    columns, selected = store.run(sql, {k: v for k, v in (params or {}).items() if k in used}, db)
    if 'record_id' not in columns: raise store.RecipeError('A reading plan must select record_id')
    at = columns.index('record_id')
    ids = list(dict.fromkeys(r[at] for r in selected))
    if len(ids) > MAX_DOCUMENTS:
        raise store.RecipeError(f'The plan selects {len(ids)} documents; this tier reads at most {MAX_DOCUMENTS} per question')
    texts = store.texts(ids, db)
    docs = [{'record_id': i, **texts[i]} for i in ids if i in texts and texts[i]['text'].strip()]
    clock = store.as_of(db)
    with ThreadPoolExecutor(WORKERS) as pool:
        evidence = list(pool.map(lambda d: judge(statement, d, readers=readers, s1_ledger=s1_ledger, as_of=clock), docs)) if docs else []
    steps = []
    for tier in readers:
        tries = [a for e in evidence for a in e['attempts'] if a['by'] == tier]
        if tries:
            steps.append({'tier': 'read', 'reader': tier, 'model': tries[0].get('model'), 'calls': len(tries),
                          'decided': sum(1 for a in tries if a['label']), 'tokens': sum(a['tokens'] for a in tries),
                          'usd': sum(a['usd'] for a in tries), 'outcome': 'read',
                          **({'undecided_reasons': sorted({str(a.get('reason'))[:80] for a in tries if not a['label']})[:3]}
                             if any(not a['label'] for a in tries) else {})})
    yes = [e for e in evidence if e['label'] == 'yes']
    undecided = [e for e in evidence if e['label'] is None]
    if mode == 'count':
        out_columns = ['documents_read', 'documents_stating', 'documents_not_stating', 'documents_undecided']
        rows = [[len(evidence), len(yes), len(evidence) - len(yes) - len(undecided), len(undecided)]]
    else:
        keep = {e['record_id'] for e in yes}
        out_columns, rows = columns, [r for r in selected if r[at] in keep]
    checks = []
    def add(name, ok, detail=''): checks.append({'name': name, 'ok': bool(ok), 'detail': detail})
    add('documents_selected', ids, f'{len(ids)} documents')
    add('document_text_available', len(docs) == len(ids), f'{len(ids) - len(docs)} selected documents have no text')
    add('every_document_decided', not undecided, f'{len(undecided)} undecided')
    add('quotes_verbatim', all(recipes_flat(e['quote']) in recipes_flat(texts[e['record_id']]['text']) for e in yes if e.get('quote')),
        f"{sum(1 for e in yes if e.get('quote'))} quoted")
    wanted = set(re.findall(r'(?<![\d-])20\d{2}(?:-\d{2})?(?![\d])', question))
    seen = sql + ' ' + ' '.join(str(v) for v in (params or {}).values())
    absent = sorted(w for w in wanted if w not in seen and w[:4] not in seen)
    add('time_window_bound', not absent, 'question periods not in the plan: ' + ', '.join(absent) if absent else ', '.join(sorted(wanted)))
    slim = [{k: v for k, v in e.items() if k != 'attempts'} | {'readers_tried': [a['by'] for a in e['attempts']]} for e in evidence]
    return out_columns, rows, {'ok': all(c['ok'] for c in checks), 'checks': checks}, steps, slim


recipes_flat = _flat
