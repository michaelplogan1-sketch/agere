"""Run an Agere text-to-SQL arm on an EHRSQL 2024 split and score it with the official reliability metric.

Agere's product answer path is bound to Agere's own semantic layer, so it cannot be pointed at MIMIC-IV as is. This
runner uses Agere's NL->SQL model rungs unchanged, with a MIMIC schema card in place of the Agere data dictionary,
following the shape of nl2sql.answer (one SELECT, read-only execution, one repair on an execution error):

  agere_local   Agere's local SQL writer: Qwen3-4B on MLX (clinical_core/agere/clinical/nl2sql.LocalModel). No API cost.
  agere_hosted  The same prompt on a hosted Claude model (Agere's hosted rung is claude-haiku-5-5; --model overrides).

Prompt = schema card (from the organisers' mimic_iv.sql) + rules + the k most similar TRAIN questions with their SQL
(EHRSQL train is permitted supervision; valid/test questions never enter a prompt) + the question. The model writes
one SQLite SELECT or `null` to abstain. A query that still fails after one repair is turned into an abstention
(an error scores -1 under RS; abstaining scores 0).

    .venv/bin/python -I bench/external/ehrsql_run.py --system agere_local --split valid
    .venv/bin/python -I bench/external/ehrsql_run.py --score bench/results/ehrsql/<run>.jsonl

The test split is refused. Results are JSONL under bench/results/ehrsql/ (gitignored) plus a <run>.summary.json.
"""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import argparse, json, math, re, sqlite3, sys, time

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bench.external import ehrsql

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / 'bench' / 'results' / 'ehrsql'

RULES = """You translate questions about a hospital database (MIMIC-IV demo, EHRSQL schema) into ONE SQLite SELECT.
- Use only the tables and columns in the schema. Follow the SQL style of the examples exactly (subqueries via
  admissions.hadm_id, d_items / d_labitems / d_icd_* label lookups, DENSE_RANK for "top N", datetime(...) idioms).
- "Now" is the database clock: write current_time (it is 2100-12-31 23:59:00). "this year" = datetime(current_time,
  'start of year','-0 year'); "last year" = '-1 year'; "this month" = 'start of month','-0 month'.
- "first/last hospital encounter" = the admission with dischtime IS NOT NULL ordered by admittime ASC/DESC LIMIT 1;
  "current hospital encounter" = admissions.dischtime IS NULL. ICU stays work the same way with icustays.outtime.
- String values are lowercase and must match the database text exactly as the question gives them.
- If the question cannot be answered from this database (information not stored, e.g. advice, schedules, staff,
  costs not in the cost table, blood type, anything outside the schema) or is not a data question, output exactly: null
Output only the SQL on one line (or null). No explanation, no code fence."""


def schema_card(path=ehrsql.DATA / 'mimic_iv.sql'):
    lines = []
    for table, body in re.findall(r'CREATE TABLE (\w+)\s*\((.*?)\n\);', path.read_text(), re.S):
        cols = [m.group(1) for m in re.finditer(r'^\s*(\w+)\s+[A-Z]', body, re.M) if m.group(1) != 'FOREIGN']
        lines.append(f'{table}({", ".join(cols)})')
    return '\n'.join(lines)


# --- example retrieval over the TRAIN split --------------------------------------------------------------------------
def _template_tokens(question):
    q = re.sub(r"'[^']*'", ' val ', question.lower())
    q = re.sub(r'\d+(?:\.\d+)?', ' num ', q)
    return re.findall(r'[a-z]+', q)


class Examples:
    def __init__(self, data=ehrsql.DATA):
        items = ehrsql.load_split('train', data)
        self.items = [(q['question'], q['reference_sql'] or ehrsql.NULL) for q in items]
        self.docs = [Counter(_template_tokens(q)) for q, _ in self.items]
        df = Counter(t for d in self.docs for t in d)
        n = len(self.docs)
        self.idf = {t: math.log((n + 1) / (c + 0.5)) for t, c in df.items()}
        self.norms = [math.sqrt(sum((c * self.idf[t]) ** 2 for t, c in d.items())) or 1.0 for d in self.docs]

    def top(self, question, k):
        q = Counter(_template_tokens(question))
        qn = math.sqrt(sum((c * self.idf.get(t, 0)) ** 2 for t, c in q.items())) or 1.0
        scored = sorted(((sum(c * d.get(t, 0) * self.idf.get(t, 0) ** 2 for t, c in q.items()) / (qn * self.norms[i]), i)
                         for i, d in enumerate(self.docs)), reverse=True)
        picked, seen = [], set()
        for score, i in scored:
            question_i, sql = self.items[i]
            shape = re.sub(r"'[^']*'|\b\d+(?:\.\d+)?\b", '?', sql)
            if shape in seen or ehrsql.denied(question_i):
                continue
            seen.add(shape)
            picked.append((question_i, sql))
            if len(picked) == k:
                break
        return picked


def prompt(question, examples):
    shots = '\n\n'.join(f'Q: {q}\nSQL: {sql}' for q, sql in examples)
    return f'SCHEMA\n{SCHEMA}\n\nEXAMPLES\n{shots}\n\nQ: {question}\nSQL:'


def extract(text):
    text = re.sub(r'(?s)<think>.*?</think>', '', text).strip()
    text = re.sub(r'^```(?:sql)?\s*|\s*```$', '', text.strip(), flags=re.I).strip()
    if re.fullmatch(r'(?i)null;?', text):
        return ehrsql.NULL
    match = re.search(r'(?is)\b(select|with)\b.*', text)
    if not match:
        return ehrsql.NULL
    return match.group(0).split('\nQ:')[0].strip().rstrip(';').replace('\n', ' ')


def check(sql, db):
    if sql == ehrsql.NULL:
        return None
    try:
        ehrsql.execute(ehrsql.post_process_sql(sql), db, timeout_s=20)
        return None
    except (sqlite3.Error, ValueError, OverflowError) as exc:
        return f'{type(exc).__name__}: {exc}'[:300]


# --- model arms ------------------------------------------------------------------------------------------------------
def local_model():
    from clinical_core.agere.clinical.nl2sql import LocalModel, MODEL_DIR
    # Model weights are gitignored; a worktree falls back to the main checkout's copy.
    path = MODEL_DIR if MODEL_DIR.exists() else Path('/Users/mikel/clinical-insights-local/models') / MODEL_DIR.name
    model = LocalModel(path)
    return lambda system, user: model(system, user, max_tokens=512)


def hosted_model(name):
    from bench.adapters import _sender, post_json
    send = _sender(None, name, post_json)

    def call(system, user):
        schema, _, rest = user.partition('\n\nEXAMPLES\n')
        body = {'model': name, 'max_tokens': 1024, 'system': system,
                'messages': [{'role': 'user', 'content': [
                    {'type': 'text', 'text': schema, 'cache_control': {'type': 'ephemeral'}},
                    {'type': 'text', 'text': 'EXAMPLES\n' + rest}]}]}
        start = time.perf_counter()
        response, usd, _ = send(body)
        text = ''.join(b.get('text', '') for b in response.get('content', []) if b.get('type') == 'text')
        return text, {'seconds': round(time.perf_counter() - start, 2), 'model': name, 'api_usd': usd,
                      'usage': response.get('usage')}
    return call


def answer(question, model, examples, k, db):
    user = prompt(question, examples.top(question, k))
    text, meta = model(RULES, user)
    sql = extract(text)
    error = check(sql, db)
    calls = [meta]
    if error:
        repair = f'{user} {sql}\n\nThat query failed in SQLite: {error}\nWrite the corrected SQL (or null).\nSQL:'
        text, meta = model(RULES, repair)
        calls.append(meta)
        sql = extract(text)
        error = check(sql, db)
    row = {'prediction': ehrsql.NULL if error else sql, 'raw_sql': sql, 'abstain_reason':
           'execution_error' if error else ('model' if sql == ehrsql.NULL else None), 'error': error,
           'repaired': len(calls) > 1, 'seconds': round(sum(c.get('seconds', 0) for c in calls), 2),
           'api_usd': sum(c.get('api_usd') or 0 for c in calls) if any('api_usd' in c for c in calls) else 0.0,
           'model': calls[0].get('model')}
    return row


def summarize(path, split='valid'):
    rows = {r['id']: r for r in map(json.loads, Path(path).read_text().splitlines()) if r.get('id')}
    items = [q for q in ehrsql.load_split(split) if q['id'] in rows]
    result = ehrsql.score_sql(items, {q['id']: rows[q['id']]['prediction'] for q in items})
    per = result.pop('per_question')
    by_type = {}
    for q in items:
        by_type.setdefault(q['answer_type'], []).append(per[q['id']])
    result['by_answer_type'] = {t: {'n': len(s), 'correct': sum(x == 1 for x in s), 'wrong': sum(x == -1 for x in s),
                                    'abstained': sum(x == 0 for x in s)} for t, s in sorted(by_type.items())}
    result['abstain_reasons'] = dict(Counter(r['abstain_reason'] for r in rows.values() if r['abstain_reason']))
    result['repaired'] = sum(r['repaired'] for r in rows.values())
    result['seconds_total'] = round(sum(r['seconds'] for r in rows.values()), 1)
    result['api_usd_total'] = round(sum(r.get('api_usd') or 0 for r in rows.values()), 4)
    result['models'] = sorted({r['model'] for r in rows.values() if r.get('model')})
    result['run'] = str(path)
    Path(str(path).replace('.jsonl', '.summary.json')).write_text(json.dumps(result, indent=1) + '\n')
    return result


SCHEMA = schema_card() if (ehrsql.DATA / 'mimic_iv.sql').exists() else ''


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--system', choices=('agere_local', 'agere_hosted'), default='agere_local')
    p.add_argument('--model', default='claude-haiku-5-5', help='hosted model for agere_hosted')
    p.add_argument('--split', default='valid', choices=('valid', 'train'))
    p.add_argument('--shots', type=int, default=6)
    p.add_argument('--limit', type=int)
    p.add_argument('--ids', nargs='*')
    p.add_argument('--jobs', type=int, default=4, help='concurrent hosted calls (local always runs 1)')
    p.add_argument('--resume', type=Path)
    p.add_argument('--score', type=Path, help='only (re)score an existing run file')
    args = p.parse_args(argv)
    if args.score:
        print(json.dumps(summarize(args.score, args.split), indent=1))
        return
    items = ehrsql.load_split(args.split)
    if args.ids:
        items = [q for q in items if q['id'] in set(args.ids)]
    if args.limit:
        items = items[:args.limit]
    OUT.mkdir(parents=True, exist_ok=True)
    tag = args.system if args.system == 'agere_local' else f'{args.system}-{args.model}'
    path = args.resume or OUT / f'{tag}-{args.split}-{time.strftime("%Y%m%dT%H%M%S")}.jsonl'
    done = {json.loads(l)['id'] for l in path.read_text().splitlines() if l.strip()} if path.exists() else set()
    model = local_model() if args.system == 'agere_local' else hosted_model(args.model)
    examples = Examples()
    todo = [q for q in items if q['id'] not in done]

    def run(q):
        try:
            return q, answer(q['question'], model, examples, args.shots, ehrsql.DB)
        except Exception as exc:  # provider/model failure: abstain, keep the reason visible
            return q, {'prediction': ehrsql.NULL, 'raw_sql': None, 'abstain_reason': 'system_error',
                       'error': f'{type(exc).__name__}: {exc}'[:300], 'repaired': False, 'seconds': 0, 'api_usd': None}

    jobs = 1 if args.system == 'agere_local' else args.jobs  # one MLX model in memory, not thread-safe
    with open(path, 'a') as f, ThreadPoolExecutor(jobs) as pool:
        for n, (q, row) in enumerate(pool.map(run, todo), 1):
            f.write(json.dumps({'id': q['id'], 'split': q['split'], **row}) + '\n')
            f.flush()
            if n % 50 == 0:
                print(f'{n}/{len(todo)}', flush=True)
    print(json.dumps(summarize(path, args.split), indent=1))


if __name__ == '__main__':
    main()
