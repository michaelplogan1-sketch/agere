"""Bottling step 3: verified (question -> SQL) pairs -> MLX chat fine-tuning data + a clustered held-out test split.

Verified only: route hosted_verified_sql with agreement primary=second (two independent models agree), judge=primary /
judge=second (the judge confirmed one analyst) or decider=A/B (the typed decider confirmed one at p >= 0.8).
judge_own (judge's unconfirmed query), compiler output and anything unanswered are never used. Every benchmark
question (bench/questions, bench/capability, bench/acceptance) and its near-duplicates are excluded. Each kept query is
re-executed READ ONLY on the current semantic layer; its result signature is the reference answer for evaluation.
The test split holds out whole question clusters (token-Jaccard single linkage) so paraphrases cannot leak.

Writes <out_dir>/{train,valid}.jsonl (MLX chat format), <out_dir>/../heldout-<name>.jsonl (evaluation records with the
reference result signature) and <out_dir>/../<name>-manifest.json.
Usage: .venv/bin/python tools/bottling/prepare.py <out_dir> <stack_dir> <bench_root>... -- <answers.jsonl>...
"""
import difflib, json, random, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from clinical_core.agere.clinical import bottled, nl2sql, source_profile

VERIFIED = {'primary=second', 'judge=primary', 'judge=second', 'decider=A', 'decider=B'}
BENCH_EXCLUDE_JACCARD = 0.5   # stricter than the runtime guard: training must not even resemble a benchmark question
BENCH_EXCLUDE_RATIO = 0.8
CLUSTER_JACCARD = 0.4
TEST_SHARE, VALID_SHARE = 0.15, 0.06


def clusters(items):
    parent = list(range(len(items)))
    def find(i):
        while parent[i] != i: parent[i] = parent[parent[i]]; i = parent[i]
        return i
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if bottled.jaccard(items[i], items[j]) >= CLUSTER_JACCARD: parent[find(i)] = find(j)
    groups = {}
    for i in range(len(items)): groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def main(out_dir, stack, bench_roots, files):
    profile = source_profile.load(stack)
    as_of = json.loads((Path(stack) / 'run.json').read_text()).get('as_of') or '2029-08-15'
    bench = [b for r in bench_roots for b in bottled._bench_questions(r)]
    stats = {'rows': 0, 'unverified': 0, 'benchmark_excluded': 0, 'duplicates': 0, 'no_longer_executes': 0}
    by_q = {}
    for f in files:  # later harvests override earlier answers to the same question
        for line in open(f):
            if not line.strip(): continue
            r = json.loads(line); stats['rows'] += 1
            sql = r.get('query') or ''
            if r.get('route') != 'hosted_verified_sql' or r.get('agreement') not in VERIFIED or not re.search(r'(?i)\bselect\b', sql):
                stats['unverified'] += 1; continue
            n, toks = bottled._norm(r['question']), set(nl2sql._tokens(r['question']))
            if any(n == bn or bottled.jaccard(toks, bt) >= BENCH_EXCLUDE_JACCARD or
                   difflib.SequenceMatcher(None, n, bn).ratio() >= BENCH_EXCLUDE_RATIO for _, bn, bt in bench):
                stats['benchmark_excluded'] += 1; continue
            if n in by_q: stats['duplicates'] += 1
            by_q[n] = {'question': r['question'].strip(), 'sql': sql.strip(), 'agreement': r['agreement'], 'area': r.get('area'), 'source': Path(f).name}
    pairs = []
    for p in by_q.values():
        try:
            cols, rows = bottled.execute_ro(stack, p['sql'])
        except ValueError:
            stats['no_longer_executes'] += 1; continue
        pairs.append({**p, 'reference_signature': bottled.signature(rows), 'reference_rows': len(rows)})
    groups = clusters([set(nl2sql._tokens(p['question'])) for p in pairs])
    rng = random.Random(7); rng.shuffle(groups)
    test, valid, train, need_test, need_valid = [], [], [], TEST_SHARE * len(pairs), VALID_SHARE * len(pairs)
    for g in groups:
        target = test if len(test) < need_test and len(g) <= 12 else valid if len(valid) < need_valid and len(g) <= 12 else train
        target.extend(pairs[i] for i in g)
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    fmt = lambda p: json.dumps({'messages': bottled.messages(p['question'], profile, as_of, p['sql'])})
    (out / 'train.jsonl').write_text('\n'.join(fmt(p) for p in train) + '\n')
    (out / 'valid.jsonl').write_text('\n'.join(fmt(p) for p in valid) + '\n')
    (out.parent / f'heldout-{out.name}.jsonl').write_text('\n'.join(json.dumps(p) for p in test) + '\n')  # evaluation records, not training data
    (out.parent / f'{out.name}-manifest.json').write_text(json.dumps({**stats, 'verified_pairs': len(pairs), 'clusters': len(groups),
        'train': len(train), 'valid': len(valid), 'test': len(test), 'as_of': as_of, 'agreements': sorted(VERIFIED),
        'bench_questions_checked': len(bench), 'files': [Path(f).name for f in files]}, indent=1))
    print(json.dumps({**stats, 'verified_pairs': len(pairs), 'clusters': len(groups), 'train': len(train), 'valid': len(valid),
                      'test': len(test), 'bench_questions_checked': len(bench)}))


if __name__ == '__main__':
    args = sys.argv[1:]
    sep = args.index('--')
    main(args[0], args[1], args[2:sep], args[sep + 1:])
