"""Score a clinical workflow task run (submission.jsonl from bench/hybrid_run.py) with the generator's sealed
clinical-task scorer, writing the run manifest first (bench/manifest.py) and pinning the key digest after.

    .venv/bin/python bench/task_score.py tmp/hybrid/CT1 [--model claude-haiku-5-5 --runs 3]

The scorer reports aggregates only: per step precision / recall / F1 on the row keys and accuracy of each attribute
over the keys both sides share. Agere never reads the key. Subagent cost is unmetered, so the cost total is unknown.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench import manifest as run_manifest
from bench.truth_run import ECOSYSTEM, HASH_CACHE, SAMPLE

REPO = Path(__file__).resolve().parents[1]
SEMANTIC = REPO / 'artifacts/agere-healthcare-v28-sample-0.30/stack/semantic.sqlite3'
SCORER_FILES = [ECOSYSTEM / 'ecosystem/eval_scorer.py', ECOSYSTEM / 'ecosystem/eval_clinical_tasks.py']
PROMPT = REPO / 'clinical_core/agere/hybrid/analyst_prompt.md'


def run_docs(run):
    """The analyst docs this run's prompts pointed at (catalog index + its detail folder), plus the prompt template."""
    prompts = sorted((run / 'prompts').glob('*.md'))
    text = prompts[0].read_text() if prompts else ''
    docs = [d for d in (REPO / 'tmp/layers', REPO / 'clinical_core/agere/hybrid/docs') if str(d / 'catalog_index.md') in text]
    return [x for d in docs for x in (d / 'catalog_index.md', d / 'docs' if (d / 'docs').is_dir() else d)] + [PROMPT]


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('run', type=Path)
    p.add_argument('--release', default='healthcare-warehouse-v2.8')
    p.add_argument('--semantic-database', type=Path, default=SEMANTIC)
    p.add_argument('--model', default='claude-haiku-5-5')
    p.add_argument('--runs', type=int, default=3)
    args = p.parse_args(argv)
    run = args.run.resolve()
    tasks = ECOSYSTEM / 'releases' / args.release / 'questions/clinical_tasks.jsonl'
    cache = run_manifest.hash_cache(HASH_CACHE)
    m = run_manifest.build(questions_file=tasks, scorer_files=SCORER_FILES, data=[SAMPLE, args.semantic_database],
                           docs=[d for d in run_docs(run) if d.exists()],
                           model_settings={'model': args.model, 'runs': args.runs, 'release': args.release,
                                           'cost_metering': 'unmetered_subagent'},
                           cache_policy='off', system=f'hybrid_tasks:{run.name}', cache=cache)
    run_manifest.save_hash_cache(HASH_CACHE, cache)
    run_manifest.write(run, m)
    cmd = ['python3', '-m', 'ecosystem.eval_scorer', '--release', f'releases/{args.release}',
           '--clinical-tasks', str(run / 'submission.jsonl'), '--sample-release', str(SAMPLE)]
    res = subprocess.run(cmd, cwd=ECOSYSTEM, capture_output=True, text=True)
    (run / 'score.txt').write_text(res.stdout + res.stderr)
    m = run_manifest.pin_answer_key(run, res.stdout)
    costs = run_manifest.cost_summary([{'id': t['id']} for t in map(json.loads, open(tasks)) if t])
    (run / 'cost.json').write_text(json.dumps(costs, indent=1))
    try:
        result = json.loads(res.stdout)
    except ValueError:
        print(res.stdout[-2000:], res.stderr[-2000:])
        return 1
    print(f"key {str(result.get('answers_sha256'))[:8]} | scope {result.get('scope')} | manifest {m['pinned_sha256'][:12]}")
    for tid, t in result['tasks'].items():
        for step, s in t['steps'].items():
            extra = {k: v for k, v in s.items() if k not in ('precision', 'recall', 'f1')}
            print(f"  {tid}.{step}: P {s.get('precision', '-')} R {s.get('recall', '-')} F1 {s.get('f1')}"
                  + (f" {json.dumps(extra)}" if extra else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
