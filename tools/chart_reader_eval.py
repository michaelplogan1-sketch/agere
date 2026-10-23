"""Run the single-patient dev-set buckets through chart_reader in-process and score them with the dev-set scorer.

    .venv/bin/python -m tools.chart_reader_eval                      # both buckets, dev split
    .venv/bin/python -m tools.chart_reader_eval --bucket single_patient_structured --out /tmp/chart.json
    .venv/bin/python -m tools.chart_reader_eval --follow-ups standalone

Only the DEV split is read: lines of questions.jsonl marked heldout are skipped before they are parsed. Follow-ups chain through the
previous answer's subject (the conversation memory), as the service does. A note question needs builder A's note_frames; with
--fake-notes a fake answer_patient (status unknown, no evidence) stands in so the routing and the predicates can still be checked.

Output per question: correct / wrong / refused, seconds, cost (System One new paid calls and estimated USD).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clinical_core.agere.clinical import chart_reader  # noqa: E402
from tools.dev_set import run as dev_run  # noqa: E402

QUESTIONS = ROOT / 'tools' / 'dev_set' / 'questions.jsonl'
_REL = Path('artifacts') / 'agere-healthcare-v28-sample-0.30' / 'stack'
# a worktree has no artifacts folder: fall back to the main checkout that holds the live stack
STACK = ROOT / _REL if (ROOT / _REL / 'semantic.sqlite3').exists() else Path(
    __import__('subprocess').run(['git', '-C', str(ROOT), 'rev-parse', '--git-common-dir'], capture_output=True, text=True).stdout.strip()).resolve().parent / _REL
BUCKETS = ('single_patient_structured', 'single_patient_unstructured')


def read_dev(buckets):
    out = []
    for line in QUESTIONS.read_text().splitlines():
        if '"split": "heldout"' in line:
            continue
        q = json.loads(line)
        if q.get('split') == 'dev' and q['bucket'] in buckets:
            out.append(q)
    return out


class LightStack:
    """Just enough of a Stack for chart_reader: the stack directory and no live provider calls (the service passes its own)."""

    def __init__(self, path, allow_live=False):
        self.path, self.allow_live = Path(path), allow_live


class FakeNoteFrames:
    """Stand-in for note_frames when builder A's module is not on this branch: every patient is undecided."""
    FRAMES = {}

    @staticmethod
    def validate_predicate(p):
        return p

    @staticmethod
    def answer_patient(stack, predicate, patient_id, *, as_of, ledger=None):
        return {'status': 'unknown', 'detail': {}, 'evidence': [], 'p': None}


def run(args):
    qs = read_dev(args.bucket or BUCKETS)
    by_id = {q['id']: q for q in qs}
    if args.live:
        import run_integrated_stack  # in-process runs must load the provider keys or Jev / Perplexity read as unavailable
        run_integrated_stack.keys()
    stack = LightStack(args.stack, allow_live=args.live)
    nf = FakeNoteFrames if args.fake_notes else None
    rows, subjects = [], {}
    for q in qs:
        prior = subjects.get(q['follow_up_of']) if q['follow_up_of'] else None
        text = q['key'].get('standalone') if (q['follow_up_of'] and args.follow_ups == 'standalone') else q['question']
        t0 = time.time()
        try:
            out = chart_reader.ask(stack, text, prior=prior, nf=nf)
        except ModuleNotFoundError as exc:
            out = {'status': 'error', 'error': f'{exc}'}
        except Exception as exc:  # noqa: BLE001
            out = {'status': 'error', 'error': f'{type(exc).__name__}: {exc}'[:300]}
        secs = time.time() - t0
        resp = {'answer': out, 'status': out.get('status')}
        correct, detail = dev_run.score(q, resp)
        if out.get('subject'):
            subjects[q['id']] = out['subject']
        cost = out.get('cost') or {}
        verdict = 'correct' if correct is True else 'refused' if correct is None else 'WRONG'
        rows.append({'id': q['id'], 'bucket': q['bucket'], 'asked': text, 'verdict': verdict, 'status': out.get('status'), 'seconds': round(secs, 2),
                     'paid_calls': cost.get('new_paid_api_calls', 0), 'usd': cost.get('system_one_usd', 0.0), 'detail': detail,
                     'headline': (out.get('headline') or out.get('error') or '')[:240], 'intent': out.get('intent')})
        print(f"{q['id']:>6} {verdict:8} {secs:5.2f}s ${cost.get('system_one_usd', 0.0):.4f}  {text[:70]}\n         -> {(out.get('headline') or out.get('error') or '')[:150]}", flush=True)
    print('\n=== per bucket ===')
    for b in BUCKETS:
        rs = [r for r in rows if r['bucket'] == b]
        if not rs:
            continue
        n = len(rs)
        c = sum(r['verdict'] == 'correct' for r in rs)
        w = sum(r['verdict'] == 'WRONG' for r in rs)
        f = sum(r['verdict'] == 'refused' for r in rs)
        print(f"{b}: n={n} correct={c} ({100 * c / n:.1f}%) wrong={w} refused={f} seconds_total={sum(r['seconds'] for r in rs):.1f} "
              f"paid_calls={sum(r['paid_calls'] for r in rs)} usd={sum(r['usd'] for r in rs):.4f}")
    if args.out:
        Path(args.out).write_text(json.dumps(rows, indent=1, default=str))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--bucket', choices=BUCKETS, nargs='*')
    ap.add_argument('--stack', default=str(STACK))
    ap.add_argument('--follow-ups', choices=('conversation', 'standalone'), default='conversation')
    ap.add_argument('--live', action='store_true', help='allow System One calls (needs TYPESAFE_API_KEY / PERPLEXITY_API_KEY in the environment)')
    ap.add_argument('--fake-notes', action='store_true')
    ap.add_argument('--out')
    run(ap.parse_args(argv))
    return 0


if __name__ == '__main__':
    sys.exit(main())
