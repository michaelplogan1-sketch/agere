"""Run Agere's tests: each test file in its own process, from the repository root, with no paid model key.

    python run_tests.py                 # the product core: tests/core
    python run_tests.py --legacy        # also the older modules the core still uses: tests/legacy
    python run_tests.py FILE [FILE ...] # named files only

Every file runs with the repository root as working directory and on the import path, ANTHROPIC_API_KEY removed and
Claude calls off, so a test cannot spend on the paid API. A file passes when its process exits 0 within the time limit.
"""
import argparse
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def files(legacy):
    found = sorted((ROOT / 'tests' / 'core').glob('test_*.py'))
    if legacy: found += sorted((ROOT / 'tests' / 'legacy').glob('test_*.py'))
    return found


def run_one(path, limit):
    # No provider key reaches a test: every paid model and classifier service (Anthropic, OpenAI, Perplexity, TypeSafe ...).
    env = {k: v for k, v in os.environ.items() if not re.search(r'API_KEY$|_TOKEN$|_SECRET$', k) or k == 'AGERE_SECRET_KEY'}
    env.update({'PYTHONPATH': str(ROOT) + os.pathsep + env.get('PYTHONPATH', ''), 'AGERE_CLAUDE_TRANSPORT': '', 'AGERE_CLAUDE': 'off',
                'PYTHONDONTWRITEBYTECODE': '1'})
    start = time.time()
    try:
        out = subprocess.run([sys.executable, '-B', str(path)], cwd=ROOT, env=env, capture_output=True, text=True, timeout=limit)
        status = 'pass' if out.returncode == 0 else 'fail'
        tail = (out.stdout + out.stderr).strip().splitlines()[-3:]
    except subprocess.TimeoutExpired:
        status, tail = 'timeout', []
    return {'file': str(Path(path).resolve().relative_to(ROOT)), 'status': status, 'seconds': round(time.time() - start, 1), 'tail': tail}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('paths', nargs='*')
    ap.add_argument('--legacy', action='store_true')
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--timeout', type=int, default=600, help='seconds per file')
    ap.add_argument('--report', help='write one line per file (status, seconds, file) here')
    args = ap.parse_args(argv)
    todo = [Path(p) for p in args.paths] or files(args.legacy)
    with ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(lambda p: run_one(p, args.timeout), todo))
    bad = [r for r in results if r['status'] != 'pass']
    for r in bad:
        print(f"{r['status'].upper():7} {r['file']}" + ''.join(f'\n          {line}' for line in r['tail']))
    if args.report:
        Path(args.report).write_text(''.join(f"{r['status']}\t{r['seconds']}\t{r['file']}\n" for r in results))
    print(f"{len(results) - len(bad)} of {len(results)} test files passed")
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
