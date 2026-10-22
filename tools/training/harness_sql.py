"""HarnessSQL-style interaction-matched training data (HarnessSQL, arXiv 2610.12274): train the bottled SQL specialist on the
interaction it will actually run -- inspect schema -> PROBE the data -> RUN -> read the checker's feedback -> DIAGNOSE -> revise ->
FINAL -- instead of question -> SQL pairs alone.

Trajectories are built from VERIFIED (question, SQL) pairs and REAL environment feedback; nothing is model-written:
  * schema      the compact schema slice the runtime prompt already uses (bottled.user_prompt);
  * PROBE       a read-only SELECT DISTINCT over a column the verified SQL filters on, executed on the stack (the observation is the
                real rows, e.g. which concept ids exist);
  * RUN         either the verified SQL (clean trajectory) or a FAILED ATTEMPT made by one controlled fault -- a filter value that
                does not occur in the field, or a field that does not exist -- kept only if bottled.check really rejects it;
  * DIAGNOSE    the checker's own message (lint/sanity/execution) turned into one sentence by rule, no model;
  * FINAL       the verified SQL, kept only if bottled.check accepts it and its result signature equals the reference signature.
Each assistant turn becomes one training example with the whole history as prompt (MLX chat JSONL, loss on that turn), split as the source
pair was split. Output: <out>/{train,valid,test}.jsonl, <out>/trajectories.jsonl, <out>/manifest.json; tools/export/training_parquet.py reads the
directory as task 'harness_sql' (denylist applies).

Usage: python tools/training/harness_sql.py --stack <stack> --pairs artifacts/bottling/data-v2 --out artifacts/training/harness-sql [--variants 1]
"""
import argparse, hashlib, json, random, re, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from clinical_core.agere.clinical import bottled, source_profile, verify  # noqa: E402

VERSION = 'harness-sql/1'
PROTOCOL = ('You work against a read-only SQLite semantic layer. Reply with exactly ONE action per turn:\n'
            'PROBE ```sql SELECT ...``` to look at the data (a SELECT DISTINCT / LIMIT 20),\n'
            'RUN ```sql SELECT ...``` to test a candidate query (you will see the checker result),\n'
            'FINAL ```sql SELECT ...``` once a query passes. Start the final query with one -- DEFINITION: comment line.\n'
            'The data snapshot is as of {as_of}.')
FIELD = re.compile(r"json_extract\(\s*(?:(\w+)\.)?payload\s*,\s*'\$\.([A-Za-z0-9_]+)'\s*\)\s*(?:=|in)\s*(\(?'[^']*'(?:\s*,\s*'[^']*')*\)?)", re.I)
COLUMN = re.compile(r"\b(?:(\w+)\.)?(concept|stream|source_table|drug_class|coverage_type|sex_reported)\s*(?:=|in)\s*(\(?'[^']*'(?:\s*,\s*'[^']*')*\)?)", re.I)


def question_and_sql(row):
    """MLX chat row (bottled.messages(question, profile, as_of, sql)) -> (question, sql) or None."""
    msgs = row.get('messages') or []
    user = next((m['content'] for m in msgs if m['role'] == 'user'), '')
    ans = next((m['content'] for m in reversed(msgs) if m['role'] == 'assistant'), '')
    q = user.rsplit('\n\nQ: ', 1)[-1].strip() if '\n\nQ: ' in user else None
    m = re.search(r'```sql\s*(.*?)```', ans, re.S)
    return (q, m.group(1).strip()) if q and m else None


def read_pairs(path):
    path = Path(path)
    out = []
    files = {'train': path / 'train.jsonl', 'valid': path / 'valid.jsonl'} if path.is_dir() else {None: path}
    for split, f in files.items():
        if not f.exists(): continue
        for line in f.read_text().splitlines():
            if not line.strip(): continue
            r = json.loads(line)
            qs = question_and_sql(r) if 'messages' in r else ((r.get('question'), r.get('sql')) if r.get('question') and r.get('sql') else None)
            if qs: out.append({'split': split or r.get('split') or 'train', 'question': qs[0], 'sql': qs[1]})
    held = path.parent / f'heldout-{path.name}.jsonl' if path.is_dir() else None
    if held and held.exists():
        for line in held.read_text().splitlines():
            r = json.loads(line)
            if r.get('question') and r.get('sql'): out.append({'split': 'test', 'question': r['question'], 'sql': r['sql']})
    return out


# ---- probes and faults ------------------------------------------------------------------------------------------------
def probe_for(sql):
    """A read-only probe of a column the SQL filters on -> probe SQL or None."""
    m = FIELD.search(sql)
    table = re.search(r'(?i)\bfrom\s+(\w+)(?:\s+(?:as\s+)?(\w+))?', sql)
    if m and table:
        return f"SELECT DISTINCT json_extract(payload,'$.{m.group(2)}') AS {m.group(2)} FROM {table.group(1)} LIMIT 20"
    m = COLUMN.search(sql)
    if m and table:
        return f'SELECT DISTINCT {m.group(2)} FROM {table.group(1)} LIMIT 20'
    return None


def faults(sql, profile):
    """Controlled faults -> [(kind, faulty_sql)]; the caller keeps only those the checker rejects."""
    out = []
    m = re.search(r"((?:concept|stream|source_table|drug_class)\s*=\s*)'([^']+)'", sql, re.I) or re.search(r"(\$\.[A-Za-z0-9_]+'\s*\)\s*=\s*)'([^']+)'", sql)
    if m:
        wrong = m.group(2) + 's' if not m.group(2).endswith('s') else m.group(2)[:-1]
        out.append(('wrong_value', sql[:m.start(2)] + wrong + sql[m.end(2):]))
    f = re.search(r"json_extract\(\s*(?:\w+\.)?payload\s*,\s*'\$\.([A-Za-z0-9_]+)'", sql)
    if f:
        out.append(('unknown_field', sql.replace(f"$.{f.group(1)}'", f"$.{f.group(1)}_x'", 1)))
    return out


def observation(res):
    if not res['ok']: return 'ERROR: ' + res['rejected']
    rows = res['rows']
    return f"OK columns={res['columns']} rows={len(rows)} first={json.dumps(rows[:3], default=str)}"


def diagnose(kind, res):
    msg = res['rejected']
    if kind == 'wrong_value':
        return 'DIAGNOSE: the filter value does not occur in the data' + ('; values seen: ' + msg.split('observed:')[-1].strip()[:160] if 'observed:' in msg else ': ' + msg[:160])
    if kind == 'unknown_field': return 'DIAGNOSE: that field does not exist in the stream; use a field from the schema.'
    return 'DIAGNOSE: ' + msg[:200]


def trajectory(pair, stack, profile, as_of, roster, rnd, variants=1):
    """-> list of trajectories (each a message list ending in the FINAL turn) for one verified pair."""
    q, sql = pair['question'], pair['sql']
    ref = bottled.check(stack, profile, q, sql, roster)
    if not ref['ok']: return [], 'verified SQL does not pass on this stack'
    system = PROTOCOL.format(as_of=as_of)
    head = [{'role': 'system', 'content': system}, {'role': 'user', 'content': bottled.user_prompt(q, profile)}]
    out = []
    probe = probe_for(sql)
    probe_turn = []
    if probe:
        try:
            cols, rows = bottled.execute_ro(stack, probe)
            probe_turn = [{'role': 'assistant', 'content': f'PROBE ```sql\n{probe}\n```'},
                          {'role': 'user', 'content': f'OK columns={cols} rows={len(rows)} values={json.dumps([r[0] for r in rows][:20], default=str)}'}]
        except ValueError:
            probe_turn = []
    final = {'role': 'assistant', 'content': f'FINAL ```sql\n{sql}\n```'}
    # clean trajectory: probe -> RUN verified -> FINAL
    out.append(('clean', head + probe_turn + [{'role': 'assistant', 'content': f'RUN ```sql\n{sql}\n```'},
                {'role': 'user', 'content': observation(ref)}, final]))
    cands = faults(sql, profile); rnd.shuffle(cands)
    for kind, bad in cands[:variants]:
        res = bottled.check(stack, profile, q, bad, roster)
        if res['ok']: continue                         # the fault must really be caught by the environment
        out.append((kind, head + probe_turn + [{'role': 'assistant', 'content': f'RUN ```sql\n{bad}\n```'},
                    {'role': 'user', 'content': observation(res)},
                    {'role': 'assistant', 'content': diagnose(kind, res) + f'\nFINAL ```sql\n{sql}\n```'}]))
    return out, None


def build(pairs, stack, out, variants=1, seed=7):
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    profile = source_profile.load(stack)
    as_of, roster = __import__('tools.bottling.evaluate', fromlist=['stack_facts']).stack_facts(stack)
    rnd = random.Random(seed)
    stats, rows, trajs = Counter(), {'train': [], 'valid': [], 'test': []}, []
    for p in pairs:
        ts, why = trajectory(p, stack, profile, as_of, roster, rnd, variants)
        if why: stats['skipped_' + why] += 1; continue
        group = hashlib.sha256(re.sub(r'\W+', ' ', p['question'].lower()).encode()).hexdigest()[:12]
        for kind, msgs in ts:
            stats['trajectory_' + kind] += 1
            trajs.append({'question': p['question'], 'kind': kind, 'split': p['split'], 'messages': msgs})
            for i, m in enumerate(msgs):
                if m['role'] != 'assistant': continue
                rows[p['split']].append({'messages': msgs[:i + 1], 'group_key': group, 'kind': kind, 'step': sum(x['role'] == 'assistant' for x in msgs[:i + 1])})
                stats['examples'] += 1
    for split, rs in rows.items():
        rnd.shuffle(rs)
        (out / f'{split}.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rs))
    (out / 'trajectories.jsonl').write_text(''.join(json.dumps(t) + '\n' for t in trajs))
    manifest = {'version': VERSION, 'stack': str(stack), 'pairs': len(pairs), 'stats': dict(stats), 'rows': {k: len(v) for k, v in rows.items()},
                'faults': ['wrong_value', 'unknown_field'], 'verification': 'every failed attempt rejected by bottled.check; every FINAL accepted by bottled.check',
                'model_written_text': False}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=1))
    return manifest


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--stack', required=True); ap.add_argument('--pairs', required=True); ap.add_argument('--out', required=True)
    ap.add_argument('--variants', type=int, default=1); ap.add_argument('--limit', type=int)
    a = ap.parse_args()
    pairs = read_pairs(a.pairs)[:a.limit]
    print(json.dumps(build(pairs, a.stack, a.out, a.variants), indent=1))
