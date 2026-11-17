"""The note-reading test: Agere against a language model doing everything, on questions that can only be answered by reading
documents (eval/tiered/notes_v1.json, written by an independent evaluator that did not see the product).

Each question names a set of documents (its scope_sql over the raw tables), a statement that is true of a document exactly
when the question wants it counted, and what to report (count of documents, count of patients, or which patients).

The answer key (key): every document in scope is read against the statement twice, independently, by careful readers with
different instructions (the top-tier model, one document at a time, a quote required). Where they disagree a third careful
read adjudicates. Agreement before adjudication is reported, and 30 labels are set aside for a clinician's spot-check. The
key is frozen in git before any arm runs.

Arms, cheapest first, each run `runs` times so run-to-run consistency is measured:
  readers    Agere's readers on the scope's documents (System One, then the small model, the frontier reader where they are
             unsure; code counts). Isolates reading.
  stack      Agere's whole stack from the question alone: the router (SQL selects the documents, the readers read, code counts).
  llm_alone  the top-tier model given the question and the text of every document in scope, answering in one call.
Every answer is written to a journal as it is produced; a run stopped at a provider limit resumes where it stopped.

    python -m clinical_core.agere.tiered.notes_proof key [workers]
    python -m clinical_core.agere.tiered.notes_proof estimate
    python -m clinical_core.agere.tiered.notes_proof run ARM[,ARM] [runs] [workers]
    python -m clinical_core.agere.tiered.notes_proof report
"""
import json
import os
import random
import shutil
import sys
import tempfile
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import models, store

SET = Path(os.environ.get('AGERE_NOTES_SET', 'eval/tiered/notes_v1.json'))
KEY = SET.with_name(SET.stem + '_key.json')
SPOT = SET.with_name(SET.stem + '_spotcheck.json')
OUT = Path('artifacts/agere-tiered-core/notes')
KEY_MODEL = os.environ.get('AGERE_NOTES_KEY_MODEL', 'claude-opus-5-5')
ALONE_MODEL = os.environ.get('AGERE_NOTES_ALONE_MODEL', 'claude-opus-5-5')
PENALTY = 10
SPOT_N = 30

READER_A = '''You are a careful clinical documentation reviewer building an answer key. For EACH document below, decide on
its own whether that document states one thing. Read each whole document before deciding it, and never let one document
decide another. A finding that is negated, ruled out, only planned or considered, only historical when the statement is
about the present, carried forward from an earlier document only as quoted context, or about someone other than the
patient does not count unless the statement says it does. Judge meaning, not spelling: scanned text can contain
character-recognition errors. Return exactly one JSON object and nothing else, with one entry for every document:
{"documents": [{"record_id": "<id>", "answer": "yes" or "no", "quote": "<the exact words that decide it, or empty>",
 "reason": "<one sentence>"}]}'''

READER_B = '''You are the second, independent reviewer for an answer key. For EACH document below, separately, decide whether it
makes a statement true. For each one, first find every passage that bears on the statement, for or against (negations,
plans, history, quoted earlier documents, family members, values either side of a threshold); then decide strictly by
what that document says about this patient now. Do not assume anything a document does not say, and judge every
document on its own. Return exactly one JSON object and nothing else, with one entry for every document:
{"documents": [{"record_id": "<id>", "passages": ["<exact words>"], "answer": "yes" or "no", "reason": "<one sentence>"}]}'''

ADJUDICATOR = '''Two careful reviewers disagreed on whether a document states something. Read the document yourself, weigh both
reviews, and decide. A negated, ruled-out, merely planned, historical-only, quoted-from-an-earlier-document or
family-member mention does not count unless the statement says it does. Return exactly one JSON object and nothing else:
{"answer": "yes" or "no", "reason": "<one or two sentences>"}'''

ALONE_SYSTEM = '''You answer a hospital analytics question by reading the documents you are given; they are every document
the question is about. Read each one and decide it against the question yourself. A negated, ruled-out, merely planned,
historical-only or family-member mention does not count unless the question says it does.
Return exactly one JSON object and nothing else:
{"record_ids": ["<every document that the question wants counted>"], "patient_ids": ["<the patients of those documents>"],
 "count": <the number the question asks for>, "answer": "<one sentence>"}'''


# ---- the set and its documents --------------------------------------------------------------------------------------

def load():
    return json.loads(SET.read_text())


_DOCS = {}


def documents(q, db=None):
    """-> [{'record_id','patient_id','source','document_type','event_date','organization_id','text'}] for one question's scope."""
    if q['id'] in _DOCS: return _DOCS[q['id']]
    own = db is None
    db = db or store.connect()
    try:
        rows = db.execute(q['scope_sql']).fetchall()
        ids = list(dict.fromkeys(str(r[0]) for r in rows))
        patient = {str(r[0]): str(r[1]) for r in rows}
        texts = store.texts(ids, db)
        marks = ','.join('?' * len(ids))
        meta = {r[0]: r[1:] for r in db.execute(f'SELECT record_id, source, document_type, event_date, organization_id FROM document WHERE record_id IN ({marks})', ids)}
    finally:
        if own: db.close()
    out = []
    for i in ids:
        if i not in texts or not texts[i]['text'].strip(): continue
        src, kind, day, org = meta.get(i, (texts[i]['source'], None, texts[i]['event_date'], None))
        out.append({'record_id': i, 'patient_id': patient[i], 'source': src, 'document_type': kind, 'event_date': day, 'organization_id': org, 'text': texts[i]['text']})
    _DOCS[q['id']] = out
    return out


def reference(aggregate, labels):
    """labels: {record_id: (patient_id, 'yes'|'no')} -> the figure the question asks for."""
    yes = [(rid, p) for rid, (p, label) in labels.items() if label == 'yes']
    if aggregate == 'count_documents': return len(yes)
    if aggregate == 'count_patients': return len({p for _, p in yes})
    return sorted({p for _, p in yes})


# ---- journals ---------------------------------------------------------------------------------------------------------

_lock = threading.Lock()


def _journal(name):
    path = OUT / name
    OUT.mkdir(parents=True, exist_ok=True)
    done = [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []
    return path, done


def _append(path, row):
    with _lock:
        with open(path, 'a') as f: f.write(json.dumps(row, default=str) + '\n')


def _call(model, system, user):
    text, step = models._claude(model, system, user, timeout=300)
    return models._json(text), step


def _doc_block(d, limit=None):
    text = d['text'] if limit is None else d['text'][:limit]
    return (f"Document {d['record_id']} | patient {d['patient_id']} | {d['source']} | {d['document_type']} | {d['event_date']} | "
            f"facility {d['organization_id']}\n{text}")


# ---- the answer key ---------------------------------------------------------------------------------------------------

def key(workers=6):
    """Two independent careful reads of every document in scope, adjudication of disagreements, the key and the spot-check."""
    data = load()
    path, done = _journal('key-reads.jsonl')
    have = {(r['qid'], r['record_id'], r['reader']): r for r in done}
    db = store.connect()
    pairs = [(q, d) for q in data['questions'] for d in documents(q, db)]
    db.close()
    stopped = []

    def read(job):
        """Reader A or B reads one question's documents in one call (each judged on its own); B sees them in reverse order.
        The adjudicator reads one document. A document a batch leaves out is read again on its own."""
        q, docs, reader = job
        if stopped: return
        system = {'A': READER_A, 'B': READER_B, 'adj': ADJUDICATOR}[reader]
        if reader == 'adj':
            d = docs[0]
            a, b = have[(q['id'], d['record_id'], 'A')], have[(q['id'], d['record_id'], 'B')]
            user = (f"Statement: {q['statement']}\n\n{_doc_block(d)}\n\nReviewer 1 said {a['answer']}: {a.get('reason')} Quote: \"{a.get('quote') or ''}\"\n"
                    f"Reviewer 2 said {b['answer']}: {b.get('reason')} Passages: {json.dumps(b.get('passages') or [])[:1500]}")
        else:
            order = docs if reader == 'A' else list(reversed(docs))
            user = f"Statement: {q['statement']}\n\n{len(order)} documents:\n\n" + '\n\n---\n\n'.join(_doc_block(d) for d in order)
        for attempt in range(2):
            try:
                got, step = _call(KEY_MODEL, system, user)
                entries = [got] if reader == 'adj' else got.get('documents')
                if not isinstance(entries, list): raise ValueError('no documents list')
                break
            except models.ProviderLimit as exc:
                stopped.append(str(exc)); return
            except Exception as exc:
                if attempt: print(f'key read failed {q["id"]} {reader}: {exc}', file=sys.stderr, flush=True); return
        ids = {d['record_id'] for d in docs}
        good = []
        for e in entries:
            rid = docs[0]['record_id'] if reader == 'adj' else str(e.get('record_id'))
            answer = str(e.get('answer') or '').strip().lower()
            if rid in ids and answer in ('yes', 'no') and (q['id'], rid, reader) not in have: good.append((rid, answer, e))
        for rid, answer, e in good:
            row = {'qid': q['id'], 'record_id': rid, 'reader': reader, 'answer': answer, 'quote': e.get('quote'), 'passages': e.get('passages'),
                   'reason': e.get('reason'), 'model': KEY_MODEL, 'usd': step['usd'] / len(good), 'tokens': step['tokens'] // len(good), 'batch': len(docs)}
            have[(q['id'], rid, reader)] = row
            _append(path, row)

    for readers in (('A', 'B'), ('A', 'B'), ('adj',)):   # the second pass reads one by one whatever a batch left out
        jobs = []
        for q in data['questions']:
            docs = [d for qq, d in pairs if qq['id'] == q['id']]
            for reader in readers:
                left = [d for d in docs if (q['id'], d['record_id'], reader) not in have]
                if reader == 'adj':
                    for d in docs:
                        a, b = have.get((q['id'], d['record_id'], 'A')), have.get((q['id'], d['record_id'], 'B'))
                        if a and b and a['answer'] != b['answer'] and (q['id'], d['record_id'], 'adj') not in have: jobs.append((q, [d], 'adj'))
                elif left and len(left) == len(docs):
                    jobs.append((q, left, reader))
                else:
                    jobs += [(q, [d], reader) for d in left]
        print(f'key: {len(jobs)} reads to make ({"+".join(readers)})', flush=True)
        with ThreadPoolExecutor(int(workers)) as pool: list(pool.map(read, jobs))
        if stopped: return f'STOPPED AT A PROVIDER LIMIT: {stopped[0]}. The reads made so far are saved in {path}; run key again to finish.'
    return _write_key(data, pairs, have)


def _kappa(a, b):
    n = len(a)
    if not n: return None
    po = sum(x == y for x, y in zip(a, b)) / n
    pa, pb = sum(x == 'yes' for x in a) / n, sum(x == 'yes' for x in b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return round((po - pe) / (1 - pe), 3) if pe < 1 else 1.0


def _write_key(data, pairs, have):
    questions, missing = {}, []
    a_all, b_all, by_source = [], [], defaultdict(lambda: [0, 0])
    usd = sum(r['usd'] for r in have.values())
    for q, d in pairs:
        a, b, adj = (have.get((q['id'], d['record_id'], r)) for r in ('A', 'B', 'adj'))
        if not a or not b or (a['answer'] != b['answer'] and not adj):
            missing.append((q['id'], d['record_id'])); continue
        a_all.append(a['answer']); b_all.append(b['answer'])
        by_source[d['source']][0] += 1; by_source[d['source']][1] += a['answer'] == b['answer']
        label = a['answer'] if a['answer'] == b['answer'] else adj['answer']
        entry = questions.setdefault(q['id'], {'aggregate': q['aggregate'], 'documents': {}})
        entry['documents'][d['record_id']] = {'patient_id': d['patient_id'], 'source': d['source'], 'document_type': d['document_type'], 'label': label,
                                              'reader_a': a['answer'], 'reader_b': b['answer'], 'adjudicated': a['answer'] != b['answer'],
                                              'reason': (adj or a)['reason']}
    if missing: return f'{len(missing)} documents still lack two reads or an adjudication; run key again. First: {missing[:5]}'
    for qid, entry in questions.items():
        entry['reference'] = reference(entry['aggregate'], {rid: (x['patient_id'], x['label']) for rid, x in entry['documents'].items()})
    agree = sum(x == y for x, y in zip(a_all, b_all))
    key = {'version': data['version'] + '_key', 'made': time.strftime('%Y-%m-%d'),
           'method': (f'Every document in scope read twice, independently, by careful readers with different instructions ({KEY_MODEL}; each reader '
                      "reads one question's documents in one call and judges each on its own with a quote, the second in reverse order); "
                      'disagreements adjudicated by a third careful read of that one document. The product was not used.'),
           'agreement': {'documents': len(a_all), 'agree_before_adjudication': agree, 'share': round(agree / len(a_all), 4), 'kappa': _kappa(a_all, b_all),
                         'adjudicated': len(a_all) - agree, 'yes_share': round(sum(1 for q in questions.values() for x in q['documents'].values() if x['label'] == 'yes') / len(a_all), 4),
                         'by_source': {s: {'documents': n, 'agree': k, 'share': round(k / n, 4)} for s, (n, k) in sorted(by_source.items())}},
           'cost_usd': round(usd, 2), 'questions': questions}
    KEY.write_text(json.dumps(key, indent=1) + '\n')
    _spot(data, pairs, questions)
    return json.dumps({k: v for k, v in key.items() if k != 'questions'}, indent=1)


def _spot(data, pairs, questions):
    """30 labels for a clinician, stratified: adjudicated ones, yes and no, across sources; seeded, so it is reproducible."""
    rng = random.Random(20261117)
    docs = {(q['id'], d['record_id']): (q, d) for q, d in pairs}
    pool = [(qid, rid, x) for qid, e in questions.items() for rid, x in e['documents'].items()]
    rng.shuffle(pool)
    picked, seen = [], set()
    def take(cond, n):
        for item in pool:
            if len([p for p in picked if p[3] == cond.__name__]) >= n: return
            if (item[0], item[1]) in seen or not cond(item[2]): continue
            seen.add((item[0], item[1])); picked.append((*item, cond.__name__))
    def adjudicated(x): return x['adjudicated']
    def stated_yes(x): return x['label'] == 'yes' and not x['adjudicated']
    def stated_no(x): return x['label'] == 'no' and not x['adjudicated']
    take(adjudicated, 10); take(stated_yes, (SPOT_N - len(picked)) // 2); take(stated_no, SPOT_N - len(picked))
    items = []
    for i, (qid, rid, x, why) in enumerate(picked, 1):
        q, d = docs[(qid, rid)]
        items.append({'n': i, 'question_id': qid, 'record_id': rid, 'statement': q['statement'], 'document_type': d['document_type'], 'source': d['source'],
                      'event_date': d['event_date'], 'text': d['text'], 'key_label': x['label'], 'key_reason': x['reason'], 'stratum': why,
                      'clinician_verdict': None, 'clinician_note': None})
    SPOT.write_text(json.dumps({'version': data['version'] + '_spotcheck', 'for': 'Mike (clinician): mark each clinician_verdict "agree" or "disagree"',
                                'labels': items}, indent=1) + '\n')


# ---- the arms ---------------------------------------------------------------------------------------------------------

def _setup():
    from .. import model_policy
    os.environ.pop('ANTHROPIC_API_KEY', None)
    from clinical_core.agere.providers import load_keys
    load_keys(); os.environ.pop('ANTHROPIC_API_KEY', None)
    os.environ.update({k: v for k, v in model_policy.ECONOMICAL_RUNTIME.items() if isinstance(v, str)})
    os.environ['AGERE_TIERED_AUTO_LEARN'] = 'off'


def _readers_arm(q, scratch, run):
    from . import read
    db = store.connect()
    try:
        docs, clock = documents(q, db), store.as_of(db)
    finally:
        db.close()
    ledger = str(scratch / f's1-run{run}.sqlite3')
    with ThreadPoolExecutor(read.WORKERS) as pool:
        got = list(pool.map(lambda d: read.judge(q['statement'], d, s1_ledger=ledger, as_of=clock), docs))
    labels = {e['record_id']: (d['patient_id'], e['label']) for d, e in zip(docs, got)}
    tries = [a for e in got for a in e['attempts']]
    return {'value': reference(q['aggregate'], labels), 'status': 'answered', 'labels': {r: l for r, (_, l) in labels.items()},
            'undecided': sum(1 for e in got if e['label'] is None), 'by': dict(Counter(e['by'] for e in got)),
            'usd': sum(a.get('usd', 0.0) for a in tries), 'tokens': sum(a.get('tokens', 0) for a in tries)}


def _value_from_rows(aggregate, columns, rows):
    """The figure an answer table gives for this kind of question, or None. Column-aware, the same for every question."""
    columns = [str(c).lower() for c in columns or []]
    rows = rows or []
    def distinct(col): return sorted({str(r[columns.index(col)]) for r in rows if r[columns.index(col)] is not None})
    single = len(rows) == 1 and [v for v in rows[0] if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if aggregate == 'list_patients':
        if 'patient_id' in columns: return distinct('patient_id')
        return None
    if aggregate == 'count_documents':
        if 'documents_stating' in columns and len(rows) == 1: return rows[0][columns.index('documents_stating')]
        if 'record_id' in columns: return len(distinct('record_id'))
        return single[0] if single and len(single) == 1 else None
    if 'patient_id' in columns: return len(distinct('patient_id'))
    if 'documents_stating' in columns and len(rows) == 1: return rows[0][columns.index('documents_stating')]
    return single[0] if single and len(single) == 1 else None


def _stack_arm(q, scratch, run):
    from . import bottles as bottle_store, router
    o = router.answer(q['question'], tiers=router.ALL_TIERS, bottles=bottle_store.Bottles(scratch / 'bottles.sqlite3'), s1_ledger=str(scratch / f's1-run{run}.sqlite3'))
    value = _value_from_rows(q['aggregate'], o.get('columns'), o.get('rows')) if o.get('status') == 'answered' else None
    return {'value': value, 'status': o.get('status'), 'tier': o.get('final_tier'), 'usd': sum(s.get('usd', 0.0) for s in o['steps']),
            'tokens': sum(s.get('tokens', 0) for s in o['steps']), 'columns': o.get('columns'), 'rows': (o.get('rows') or [])[:40],
            'sql': ((o.get('recipe') or {}).get('sql') or '')[:2000], 'statement': ((o.get('recipe') or {}).get('read') or {}).get('statement')}


def _alone_user(q, docs):
    return f"Question: {q['question']}\n\n{len(docs)} documents:\n\n" + '\n\n---\n\n'.join(_doc_block(d) for d in docs)


def _alone_arm(q, scratch, run):
    docs = documents(q)
    got, step = _call(ALONE_MODEL, ALONE_SYSTEM, _alone_user(q, docs))
    ids = {d['record_id'] for d in docs}
    chosen = [str(r) for r in got.get('record_ids') or [] if str(r) in ids]
    patients = sorted({str(p) for p in got.get('patient_ids') or []})
    count = got.get('count')
    if q['aggregate'] == 'list_patients': value = patients
    elif isinstance(count, (int, float)) and not isinstance(count, bool): value = int(count)
    else: value = len(chosen) if q['aggregate'] == 'count_documents' else len(patients)
    return {'value': value, 'status': 'answered', 'labels': {r: ('yes' if r in chosen else 'no') for r in ids}, 'usd': step['usd'], 'tokens': step['tokens'],
            'answer': str(got.get('answer') or '')[:400]}


ARMS = {'readers': _readers_arm, 'stack': _stack_arm, 'llm_alone': _alone_arm}


def estimate():
    """The expected cost of the llm_alone arm per run, from the documents' length (about 3.6 characters a token)."""
    from . import ledger
    data = load()
    db = store.connect()
    tin = sum((len(ALONE_SYSTEM) + len(_alone_user(q, documents(q, db)))) / 3.6 for q in data['questions'])
    db.close()
    tout = 250 * len(data['questions'])
    usd = ledger.claude_usd(ALONE_MODEL, {'input_tokens': tin, 'output_tokens': tout})
    return {'questions': len(data['questions']), 'input_tokens': int(tin), 'usd_per_run': round(usd, 2), 'usd_per_question': round(usd / len(data['questions']), 4)}


def run(arms, runs=2, workers=4):
    _setup()
    data = load()
    keyed = json.loads(KEY.read_text())['questions']
    scratch = OUT / 'scratch'
    scratch.mkdir(parents=True, exist_ok=True)
    from . import bottles as bottle_store
    if not (scratch / 'bottles.sqlite3').exists():   # a copy of the saved recipes, learning off: the set never teaches the stack
        shutil.copy(os.environ.get('AGERE_TIERED_BOTTLES', bottle_store.DEFAULT), scratch / 'bottles.sqlite3')
    out = []
    for arm in arms:
        path, done = _journal(f'journal-{data["version"]}-{arm}.jsonl')
        have = {(r['qid'], int(r['run'])) for r in done}
        jobs = [(q, r) for r in range(int(runs)) for q in data['questions'] if q['id'] in keyed and (q['id'], r) not in have]
        print(f'{arm}: {len(have)} answers saved, {len(jobs)} to run', flush=True)
        stopped = []

        def one(job):
            q, r = job
            if stopped: return
            started = time.monotonic()
            try:
                got = ARMS[arm](q, scratch, r)
            except models.ProviderLimit as exc:
                stopped.append(str(exc)); return
            except Exception as exc:
                if models.is_limit(exc): stopped.append(str(exc)); return
                got = {'value': None, 'status': 'error', 'error': f'{type(exc).__name__}: {str(exc)[:300]}', 'usd': 0.0, 'tokens': 0}
            _append(path, {'qid': q['id'], 'arm': arm, 'run': r, 'seconds': round(time.monotonic() - started, 2), **got})

        with ThreadPoolExecutor(int(workers)) as pool: list(pool.map(one, jobs))
        if stopped:
            out.append(f'STOPPED AT A PROVIDER LIMIT in {arm}: {stopped[0]}. Answers so far are saved in {path}; run again to finish.')
            break
    return '\n'.join(out + [report()])


# ---- the report -------------------------------------------------------------------------------------------------------

def _kind(value, ref):
    if value is None: return 'declined'
    if isinstance(ref, list): return 'right' if sorted(map(str, value)) == ref else 'wrong'
    return 'right' if isinstance(value, (int, float)) and int(value) == ref and float(value).is_integer() else 'wrong'


def report():
    data = load()
    keyed = json.loads(KEY.read_text())
    questions = {q['id']: q for q in data['questions'] if q['id'] in keyed['questions']}
    lines = [f"{data['version']}: {len(questions)} questions, {sum(len(e['documents']) for e in keyed['questions'].values())} documents in scope. "
             f"Key agreement before adjudication {keyed['agreement']['share']:.1%} (kappa {keyed['agreement']['kappa']}), {keyed['agreement']['adjudicated']} adjudicated.",
             'Scoring: +1 right, 0 declined, -10 wrong. A count must be exact; a patient list must match exactly.', '']
    summary = {}
    for arm in ARMS:
        path = OUT / f'journal-{data["version"]}-{arm}.jsonl'
        if not path.exists(): continue
        rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
        rows = [r for r in rows if r['qid'] in questions]
        if not rows: continue
        runs = sorted({r['run'] for r in rows})
        kinds = Counter()
        per_run = []
        doc_right = doc_n = 0
        for run in runs:
            these = [r for r in rows if r['run'] == run]
            k = Counter(_kind(r['value'], keyed['questions'][r['qid']]['reference']) for r in these)
            per_run.append(k); kinds.update(k)
            for r in these:
                for rid, label in (r.get('labels') or {}).items():
                    truth = keyed['questions'][r['qid']]['documents'].get(rid, {}).get('label')
                    if truth: doc_n += 1; doc_right += (label or 'no') == truth
        by_q = defaultdict(dict)
        for r in rows: by_q[r['qid']][r['run']] = json.dumps(r['value'], sort_keys=True)
        full = [q for q, v in by_q.items() if len(v) == len(runs)]
        same = sum(1 for q in full if len(set(by_q[q].values())) == 1)
        n = len(rows)
        usd = sum(r.get('usd', 0.0) for r in rows)
        summary[arm] = {'runs': len(runs), 'answers': n, **{k: kinds[k] for k in ('right', 'wrong', 'declined')}, 'accuracy': round(kinds['right'] / n, 4),
                        'score': kinds['right'] - PENALTY * kinds['wrong'], 'usd_per_question': round(usd / n, 4), 'seconds_per_question': round(sum(r['seconds'] for r in rows) / n, 1),
                        'same_answer_every_run': f'{same} of {len(full)}' if len(runs) > 1 else None,
                        'document_reads_right': round(doc_right / doc_n, 4) if doc_n else None,
                        'right_per_run': [k['right'] for k in per_run], 'errors': sum(1 for r in rows if r.get('status') == 'error')}
        s = summary[arm]
        lines.append(f"{arm:10s} {s['runs']} run(s): right {s['right']}, wrong {s['wrong']}, declined {s['declined']} of {n} "
                     f"({s['accuracy']:.1%}); score {s['score']}; ${s['usd_per_question']:.4f} and {s['seconds_per_question']} s a question; "
                     f"right per run {s['right_per_run']}; same answer every run {s['same_answer_every_run']}"
                     + (f"; document reads matching the key {s['document_reads_right']:.1%}" if s['document_reads_right'] is not None else '')
                     + (f"; errors {s['errors']}" if s['errors'] else ''))
    (OUT / f'report-{data["version"]}.json').write_text(json.dumps({'key_agreement': keyed['agreement'], 'arms': summary}, indent=1) + '\n')
    return '\n'.join(lines)


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'report'
    if cmd == 'key': print(key(*sys.argv[2:3]))
    elif cmd == 'estimate': print(json.dumps(estimate(), indent=1))
    elif cmd == 'run': print(run(sys.argv[2].split(','), *sys.argv[3:5]))
    else: print(report())
