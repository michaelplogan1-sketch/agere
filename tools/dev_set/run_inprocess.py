"""Run a question set through the real Stack in this process (evaluation scope, no Claude, no local models) and score it.

    .venv/bin/python -m tools.dev_set.run_inprocess --set dev                  # the 100 DEV questions (heldout lines are never parsed)
    .venv/bin/python -m tools.dev_set.run_inprocess --set paraphrase           # tools/dev_set/paraphrases.jsonl
    .venv/bin/python -m tools.dev_set.run_inprocess --set compose              # tools/dev_set/compose_questions.jsonl (split dev2; also --split dev2)
    .venv/bin/python -m tools.dev_set.run_inprocess --set dev3                 # tools/dev_set/dev3_questions.jsonl (split dev3, independent wording; also --split dev3)
    .venv/bin/python -m tools.dev_set.run_inprocess --set paraphrase --keys    # also load Jev / Perplexity keys (System One)
    .venv/bin/python -m tools.dev_set.run_inprocess --set dev --bucket insight --out /tmp/x.json

Without --keys no provider is reachable, so the run measures the free deterministic path only. Chains run in one conversation id,
in order, as tools/dev_set/run.py does. Scoring is run.score(). The held-out split is refused.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.dev_set import run as dev_run  # noqa: E402
from tools.dev_set.keys import BUCKETS  # noqa: E402

_REL = Path('artifacts') / 'agere-healthcare-v28-sample-0.30' / 'stack'
STACK = Path(os.environ['AGERE_STACK']) if os.environ.get('AGERE_STACK') else ROOT / _REL if (ROOT / _REL / 'semantic.sqlite3').exists() else Path(
    subprocess.run(['git', '-C', str(ROOT), 'rev-parse', '--git-common-dir'], capture_output=True, text=True).stdout.strip()).resolve().parent / _REL
FILES = {'dev': ROOT / 'tools/dev_set/questions.jsonl', 'paraphrase': ROOT / 'tools/dev_set/paraphrases.jsonl',
         'compose': ROOT / 'tools/dev_set/compose_questions.jsonl', 'dev3': ROOT / 'tools/dev_set/dev3_questions.jsonl'}


def read(which):
    out = []
    for line in FILES[which].read_text().splitlines():
        if '"split": "heldout"' in line:
            continue
        q = json.loads(line)
        if q['split'] in ('dev', 'paraphrase', 'dev2', 'dev3'):
            out.append(q)
    return out


COLD_SKIP = ('question-plans', 'learning', 'answers', 'conversations', 'quarantine-', 'answer-workspaces', 'question-context', 'record-contexts',
             'provider-receipts', 'answer-stability', 'program-work', 'context-passes', 'semantic-readings')


def cold_stack(dest):
    """A stack directory of symlinks to every file of the real stack EXCEPT the stores that remember earlier questions (plan cache, learning,
    answers, conversations, quarantine). The exact wording of a dev question then cannot be answered from a saved plan: every question is cold."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for item in STACK.iterdir():
        if item.name.startswith(COLD_SKIP) or (dest / item.name).exists() or (dest / item.name).is_symlink():
            continue
        (dest / item.name).symlink_to(item)
    return dest


def make_ask(keys, stack_dir=None):
    os.environ.update(AGERE_EVALUATION='1', AGERE_RESULT_CACHE='off', AGERE_CLAUDE='off', AGERE_LOCAL_TYPED='off', AGERE_LOCAL_NORMALIZER='off', AGERE_LOCAL_QWEN='off',
                      AGERE_LOCAL_MODEL='off', AGERE_LOCAL_SQL='off', AGERE_BOTTLED='off', AGERE_BOTTLED_COHORT='off', AGERE_LOCAL_CANDIDATE='off')
    if keys:
        from run_integrated_stack import keys as load
        load()
    else:
        for k in ('ANTHROPIC_API_KEY', 'TYPESAFE_API_KEY', 'PERPLEXITY_API_KEY'):
            os.environ.pop(k, None)
    from clinical_core.agere.clinical.stack_runtime import Stack
    from clinical_core.agere.clinical import conversation
    stack = Stack(str(stack_dir or STACK), allow_live=bool(keys))

    def ask(text, conv):
        body = {'question': text}
        if conv:
            body['conversation_id'] = conv
        try:
            return conversation.handle_compose(stack, body)
        except Exception as exc:  # noqa: BLE001
            return {'status': 'error', 'error': f'{type(exc).__name__}: {exc}'[:300]}
    return ask


def contract_of(resp):
    """The acceptance-contract receipt an answer carries (acceptance_contract.summary), compacted: {'ok', 'unmet', 'unverified'} or None."""
    a = resp.get('answer') if isinstance(resp.get('answer'), dict) else {}
    c = resp.get('acceptance_contract') or a.get('acceptance_contract') or (resp.get('answer_checks') or {}).get('acceptance_contract')
    if not isinstance(c, dict): return None
    return {'ok': c.get('ok'), 'unmet': c.get('unmet'), 'unverified': c.get('unverified'), 'route': c.get('route')}


def run(qs, ask, follow_ups='conversation', quiet=False):
    rows, run_id = [], time.strftime('%H%M%S')
    for chain in dev_run.chains(qs, qs):
        conv = f'inproc-{run_id}-{chain[0]["id"]}'
        for q in chain:
            is_follow = bool(q['follow_up_of'])
            text = q['key'].get('standalone') if (is_follow and follow_ups == 'standalone') else q['question']
            use_conv = follow_ups == 'conversation' and len(chain) > 1
            t0 = time.time()
            resp = ask(text, conv if use_conv else None)
            secs = time.time() - t0
            correct, detail = dev_run.score(q, resp)
            verdict = 'correct' if correct is True else 'refused' if correct is None else 'WRONG'
            st = dev_run.status_of(resp)
            a = resp.get('answer') if isinstance(resp.get('answer'), dict) else {}
            from tools.dev_set.primary_audit import audit
            rows.append({'id': q['id'], 'bucket': q['bucket'], 'asked': text, 'verdict': verdict, 'status': st, 'seconds': round(secs, 2),
                         'primary_audit': audit(q, resp),
                         'detail': detail, 'route': dev_run.route_of(resp),
                         'read_as': ((resp.get('planner_receipt') or {}).get('interpretation') or {}).get('reworded_as'),
                         'error': str(resp.get('error') or '')[:400], 'headline': str(a.get('headline') or resp.get('error') or '')[:420],
                         'contract': contract_of(resp)})
            if not quiet:
                print(f"{q['id']:>10} {verdict:8} {secs:5.1f}s {dev_run.route_of(resp)[:24]:24} {text[:70]}\n{'':11}-> {rows[-1]['headline'][:130]}", flush=True)
    return rows


def summarize(rows):
    audited = Counter((row.get('primary_audit') or {}).get('verdict', 'unverified') for row in rows)
    print('Supplemental primary-value audit (limited coverage, not whole-question accuracy):', dict(audited))
    print('\n=== per bucket (correct / WRONG / refused) ===')
    for b in BUCKETS:
        rs = [r for r in rows if r['bucket'] == b]
        if rs:
            c = Counter(r['verdict'] for r in rs)
            print(f"{b:34} n={len(rs):3} correct={c['correct']:3} ({100 * c['correct'] / len(rs):5.1f}%) wrong={c['WRONG']:3} refused={c['refused']:3}")
    c = Counter(r['verdict'] for r in rows)
    print(f"{'OVERALL':34} n={len(rows):3} correct={c['correct']:3} ({100 * c['correct'] / max(1, len(rows)):5.1f}%) wrong={c['WRONG']:3} refused={c['refused']:3}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--set', choices=tuple(FILES), default='dev')
    ap.add_argument('--split', choices=('dev', 'dev2', 'dev3'), help='dev2 = --set compose, dev3 = --set dev3')
    ap.add_argument('--bucket', choices=BUCKETS)
    ap.add_argument('--ids', nargs='*')
    ap.add_argument('--keys', action='store_true', help='load Jev / Perplexity keys (System One reachable)')
    ap.add_argument('--follow-ups', choices=('conversation', 'standalone'), default='conversation')
    ap.add_argument('--cold', action='store_true', help='run on a stack copy with no saved plans / answers / learning (no exact-wording cache hits)')
    ap.add_argument('--quiet', action='store_true')
    ap.add_argument('--half', choices=('build', 'check', 'all'), help='dev3 only: the build half (develop) or the check half (measure transfer only)')
    ap.add_argument('--scorer', choices=('v1', 'v2'), default='v2', help='scoring rules (run.py --scorer)')
    ap.add_argument('--answer-keys', choices=('v1', 'v2'), default='v2', help='dev3 only: answer keys v2 (definitions of 2026-10-25) or v1')
    ap.add_argument('--out')
    args = ap.parse_args(argv)
    dev_run.set_scorer(args.scorer)
    which = {'dev2': 'compose', 'dev3': 'dev3'}.get(args.split, args.set)
    qs = read(which)
    if which == 'dev3' and args.answer_keys == 'v2':
        qs = dev_run.apply_keys_v2(qs)
    from tools.dev_set.halves import keep
    qs = keep(qs, args.half)
    if args.bucket:
        qs = [q for q in qs if q['bucket'] == args.bucket]
    if args.ids:
        qs = [q for q in qs if q['id'] in args.ids]
    stack_dir = cold_stack(os.environ.get('AGERE_COLD_STACK') or tempfile.mkdtemp(prefix='agere-cold-'))  # a fresh directory per run: no reading cached by an earlier run if args.cold else None
    rows = run(qs, make_ask(args.keys, stack_dir), args.follow_ups, args.quiet)
    summarize(rows)
    if args.out:
        Path(args.out).write_text(json.dumps(rows, indent=1, default=str))
    return 0


if __name__ == '__main__':
    sys.exit(main())
