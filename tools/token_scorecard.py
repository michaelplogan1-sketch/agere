"""Token-economics scorecard on a fixed realistic question set (clinical_core/agere/clinical/token_scorecard.py).

Sources (one of):
  --run [FILE]       ask the fixed set (default tools/usage/questions.jsonl) in-process through conversation.handle_compose
                     on the active warehouse (Stack(..., allow_live=True); live Jev/Perplexity; Claude when it has credits)
  --usage-run DIR    a tools/component_usage.py run (per_question.jsonl rows carry cost_telemetry)
  --stack DIR        the answer_costs rows a stack recorded (answers.sqlite3), optionally --limit N latest
Writes <out>/scorecard.json (+ per_question.jsonl for --run) and prints the card; exit 1 with --strict on a violation
(Opus without a failed Sonnet review, Claude before System One, unpriced paid calls).

Usage: python tools/token_scorecard.py --run --out artifacts/scorecard/latest [--limit N] [--strict]
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clinical_core.agere.clinical import token_scorecard as S  # noqa: E402


def from_usage_run(path):
    path = Path(path)
    log = path / 'per_question.jsonl' if path.is_dir() else path
    rows = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    return [r['cost_telemetry'] for r in rows if r.get('cost_telemetry')], str(log)


def from_stack(path, limit=None):
    from clinical_core.agere.clinical import cost_telemetry
    return cost_telemetry.rows(path, limit), str(path)


def run_set(questions, out, limit=None, stack=None):
    from tools.component_usage import load_questions, ask
    from clinical_core.agere.clinical import conversation
    if stack is None:
        from run_integrated_stack import keys
        keys()
        from clinical_core.agere.clinical.stack_runtime import Stack
        build = Path(json.loads((ROOT / 'config/agere/active_warehouse.json').read_text())['build'])
        stack = Stack(build / 'stack', allow_live=True)
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    conv, telemetry = {}, []
    with (out / 'per_question.jsonl').open('w') as log:
        for r in load_questions(questions, limit):
            started = time.perf_counter()
            row = ask(stack, conversation, r, conv)
            log.write(json.dumps({**row, 'seconds': round(time.perf_counter() - started, 1)}, default=str) + '\n'); log.flush()
            if row.get('cost_telemetry'): telemetry.append(row['cost_telemetry'])
    return telemetry, str(questions)


def main(argv=None):
    from tools.component_usage import QUESTIONS
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument('--run', nargs='?', const=str(QUESTIONS))
    src.add_argument('--usage-run'); src.add_argument('--stack')
    ap.add_argument('--out', default=str(ROOT / 'artifacts' / 'scorecard' / 'latest'))
    ap.add_argument('--limit', type=int); ap.add_argument('--strict', action='store_true')
    a = ap.parse_args(argv)
    if a.run: telemetry, source = run_set(a.run, a.out, a.limit)
    elif a.usage_run: telemetry, source = from_usage_run(a.usage_run)
    else: telemetry, source = from_stack(a.stack, a.limit)
    card = S.scorecard(telemetry, question_set=source)
    Path(a.out).mkdir(parents=True, exist_ok=True)
    (Path(a.out) / 'scorecard.json').write_text(json.dumps(card, indent=1, default=str))
    print(S.render(card))
    if a.strict and card.get('violations'): raise SystemExit(1)
    return card


if __name__ == '__main__':
    main()
