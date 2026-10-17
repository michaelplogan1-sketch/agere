"""Score one layer directory (answers_*.jsonl, already in the scorer contract) with the generator's sealed scorer, and
write its run manifest first (bench/manifest.py). Replaces tmp/layers/score_layer.py. Each run is compared with the
frozen baseline for its question file (bench/baselines/<questions stem>.json): COMPARABLE only when the questions,
scorer, data, cache policy and pricing match; --freeze makes a run the baseline.

    .venv/bin/python bench/layer_score.py tmp/layers/L7 [--questions bench/questions/dev20.jsonl] \
        [--docs tmp/layers/catalog_index.md tmp/layers/docs] [--model claude-haiku-5-5 --runs 3]

Layer answers come from $0 subagents, so their cost is unmetered: the manifest says so and the cost total is unknown,
never $0. Rows may carry estimated_cost_usd / cached; they are then accounted as in every other scored run.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench import manifest as run_manifest
from bench.truth_run import ECOSYSTEM, HASH_CACHE, SAMPLE, SCORER_FILES

REPO = Path(__file__).resolve().parents[1]
SEMANTIC = REPO / 'artifacts/agere-healthcare-v28-sample-0.30/stack/semantic.sqlite3'
BASELINES = REPO / 'bench/baselines'
DOCS = [REPO / 'tmp/layers/catalog_index.md', REPO / 'tmp/layers/docs', REPO / 'tmp/layers/analyst_docs.md',
        REPO / 'tmp/layers/library_examples.md']


def load_answers(layer):
    ans = {}
    for f in sorted(Path(layer).glob('answers_*.jsonl')):
        for line in open(f):
            line = line.strip()
            if line.startswith('{'):
                r = json.loads(line)
                ans[r['id']] = r
    return ans


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('layer', type=Path)
    p.add_argument('--questions', type=Path, default=REPO / 'bench/questions/dev20.jsonl')
    p.add_argument('--release', default='healthcare-warehouse-v2.8')
    p.add_argument('--semantic-database', type=Path, default=SEMANTIC)
    p.add_argument('--docs', nargs='*', type=Path, help='analyst docs (default: the tmp/layers retrieval docs that exist)')
    p.add_argument('--model', help='analyst model the layer used')
    p.add_argument('--runs', type=int, help='agreement runs per question')
    p.add_argument('--cache-policy', choices=run_manifest.CACHE_POLICIES, default='off')
    p.add_argument('--freeze', action='store_true', help='make this run the frozen baseline for its question file')
    args = p.parse_args(argv)
    layer = args.layer.resolve()
    docs = args.docs if args.docs is not None else [d for d in DOCS if d.exists()]
    cache = run_manifest.hash_cache(HASH_CACHE)
    m = run_manifest.build(questions_file=args.questions, scorer_files=SCORER_FILES, data=[SAMPLE, args.semantic_database],
                           docs=docs, model_settings={'model': args.model, 'runs': args.runs, 'release': args.release,
                                                      'cost_metering': 'unmetered_subagent'},
                           cache_policy=args.cache_policy, system=f'layer:{layer.name}', cache=cache)
    run_manifest.save_hash_cache(HASH_CACHE, cache)
    run_manifest.write(layer, m)
    baseline_path = BASELINES / f'{args.questions.stem}.json'
    frozen = json.loads(baseline_path.read_text()) if baseline_path.exists() else None

    qs = {json.loads(l)['id']: json.loads(l) for l in open(args.questions) if l.strip()}
    ans = load_answers(layer)
    out = layer / 'question_answers.jsonl'
    with open(out, 'w') as f:
        for i, q in qs.items():
            f.write(json.dumps(ans.get(i) or {'id': i, 'answer_type': q['answer_type'], 'status': 'abstain'}) + '\n')
    cmd = ['python3', '-m', 'ecosystem.eval_scorer', '--release', f'releases/{args.release}', '--questions', str(out),
           '--sample-release', str(SAMPLE), '--per-question']
    res = subprocess.run(cmd, cwd=ECOSYSTEM, capture_output=True, text=True)
    (layer / 'score.txt').write_text(res.stdout + res.stderr)
    m = run_manifest.pin_answer_key(layer, res.stdout)
    costs = run_manifest.cost_summary([{'id': i, **(ans.get(i) or {'error': 'skipped: no answer'})} for i in qs])
    (layer / 'cost.json').write_text(json.dumps(costs, indent=1))
    per = dict(re.findall(r'"id": "([^"]+)",\s*"correct": (true|false)', res.stdout))
    ids = list(qs)
    answered = [i for i in ids if (ans.get(i) or {}).get('status') == 'answered']
    right = [i for i in ids if per.get(i) == 'true']
    print(f"{layer.name}: correct {len(right)}/{len(ids)} | answered {len(answered)} | right-when-answered "
          f"{sum(per.get(i) == 'true' for i in answered)}/{len(answered)} | missing {len([i for i in ids if i not in ans])}")
    print('correct:', ' '.join(right))
    print('wrong:  ', ' '.join(i for i in answered if per.get(i) != 'true'))
    print(run_manifest.format_cost(costs), '| manifest', m['pinned_sha256'][:12])
    if frozen:
        c = run_manifest.compare(frozen['manifest'], m)
        base = frozen['results'].get(frozen['reference_layer'], {})
        print(f"vs frozen baseline {baseline_path.name} ({frozen['reference_layer']} {base.get('correct')}/{len(ids)}): "
              + ('COMPARABLE' if c['comparable'] else f"NOT COMPARABLE, measurement changed: {', '.join(c['measurement_changed'])}")
              + (f"; contestant changed: {', '.join(c['contestant_changed'])}" if c['contestant_changed'] else ''))
    if args.freeze:
        results = json.loads(baseline_path.read_text())['results'] if baseline_path.exists() else {}
        results[layer.name] = {'correct': len(right), 'answered': len(answered), 'correct_ids': right,
                               'wrong_ids': [i for i in answered if per.get(i) != 'true']}
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(json.dumps({'questions': str(args.questions), 'reference_layer': layer.name,
                                             'scorer_version': (m['pinned']['scorer']['declared_version'] or {}),
                                             'manifest': m, 'results': results}, indent=1, default=str))
        print('froze', baseline_path)


if __name__ == '__main__':
    main()
