"""Which components actually run on real questions — not just whether they are wired.

test_component_reachability proves every ledger component CAN be reached. This tracer measures what DOES run: it wraps
every ledger component's callable (module + symbol from config/agere/component_ledger.json) with a counter, sends real
questions through conversation.handle_compose (the /api/stack/compose handler) on the active warehouse, and reports per
component: questions it ran on, questions it answered (accepting cascade tier), and errors. Live providers use the same
keys the server loads (run_integrated_stack.keys); nothing is printed about them. Every row also carries the answer's
cost_telemetry, so the same run feeds the token-economics scorecard (tools/token_scorecard.py --usage-run DIR).

check(report) is the usage gate (test_component_usage): every instrumented ledger component must have run on at least
one question of the set; the only allowlist is build-time-only components (ledger current_wiring 'build_time_only' —
training, offline loops, ingestion), which never run on a question by design.

Usage: python tools/component_usage.py --questions FILE.jsonl --out DIR [--limit N]
       python tools/component_usage.py --check DIR   (exit 1 when a wired component never ran)
FILE rows: {"id", "question", "follow_up_of"?}. Writes DIR/usage.json and DIR/per_question.jsonl.
Default question set: tools/usage/questions.jsonl (fixed, realistic analyst wording).
"""
import argparse
import functools
import importlib
import inspect
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CALLS = Counter()
QUESTIONS = ROOT / 'tools' / 'usage' / 'questions.jsonl'
LEDGER = ROOT / 'config' / 'agere' / 'component_ledger.json'
BUILD_TIME = 'build_time_only'


def ledger_components(path=LEDGER):
    return json.loads(Path(path).read_text())['components']


def wrap(component_id, owner, name):
    target = getattr(owner, name)
    if getattr(target, '__component_usage__', None): return True
    if inspect.isclass(target):
        init = target.__init__
        @functools.wraps(init)
        def counted_init(self, *a, **k):
            CALLS[component_id] += 1
            return init(self, *a, **k)
        counted_init.__component_usage__ = component_id
        try:
            target.__init__ = counted_init
            return True
        except TypeError:
            return False
    if not callable(target): return False
    @functools.wraps(target)
    def counted(*a, **k):
        CALLS[component_id] += 1
        return target(*a, **k)
    counted.__component_usage__ = component_id
    setattr(owner, name, counted)
    return True


def instrumentable(c):
    return bool(c.get('module') and c.get('symbol') and not c['module'].startswith('tools.'))


def instrument(components):
    wrapped, skipped = [], []
    for c in components:
        if not instrumentable(c):
            skipped.append(c['id']); continue
        try:
            owner = importlib.import_module(c['module'])
            parts = c['symbol'].split('.')
            for part in parts[:-1]: owner = getattr(owner, part)
            (wrapped if wrap(c['id'], owner, parts[-1]) else skipped).append(c['id'])
        except Exception:  # noqa: BLE001 - a component that cannot be wrapped is reported, never fatal
            skipped.append(c['id'])
    return wrapped, skipped


def load_questions(path=QUESTIONS, limit=None):
    rows = [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]
    for r in rows:  # a factory row may carry {'text', 'styles'}: the question is its text
        if isinstance(r.get('question'), dict): r['question'] = r['question'].get('text')
    return rows[:limit]


def ask(stack, conversation, row, conv):
    """One question through handle_compose -> per-question log row (components_run filled by the caller). failed_stage /
    stage_diagnostics: which stage (retrieval / rerank / reading / planning / execution / check) each failure happened in."""
    from clinical_core.agere.clinical import stage_diagnostics
    body = {'question': row['question']}
    body['conversation_id'] = conv.get(row['follow_up_of'], row['follow_up_of']) if row.get('follow_up_of') else f"usage-{row['id']}"
    conv[row['id']] = body['conversation_id']
    telemetry = None
    try:
        result = conversation.handle_compose(stack, body)
        answer = result.get('answer') or {}
        interp = (result.get('planner_receipt') or {}).get('interpretation') or {}
        status, by = answer.get('status'), interp.get('interpreter') or (result.get('planner_receipt') or {}).get('route')
        tried = [(e['proposer'], e['outcome']) for e in interp.get('proposers') or []]
        paid, telemetry = result.get('new_paid_api_calls'), result.get('cost_telemetry')
        stages = stage_diagnostics.from_result(result)
    except Exception as exc:  # noqa: BLE001 - a failed question is data
        status, by, paid = f'error:{type(exc).__name__}', None, None
        tried = [(e['proposer'], e['outcome']) for e in (getattr(exc, 'planner_receipt', {}) or {}).get('proposers', [])]
        answer, telemetry = {'error': str(exc)[:300]}, getattr(exc, 'cost_telemetry', None)
        stages = stage_diagnostics.from_result(exc=exc)
    return {'id': row['id'], 'question': row['question'], 'family': row.get('family'), 'status': status, 'answered_by': by,
            'cascade': tried, 'paid_calls': paid, 'headline': answer.get('headline') or answer.get('value') or answer.get('error'),
            'cost_telemetry': telemetry, 'failed_stage': stages.get('failed_stage'), 'stage_diagnostics': stages}


def report(ledger, wrapped, skipped, ran, statuses, answered_by, total, question_set=None):
    return {'questions': total, 'question_set': question_set, 'statuses': dict(statuses), 'answered_by': dict(answered_by.most_common()),
            'components': {c['id']: {'family': c['family'], 'wiring': c['current_wiring'],
                                     'ran_on': len(ran.get(c['id'], ())), 'share': round(len(ran.get(c['id'], ())) / max(total, 1), 3),
                                     'instrumented': c['id'] in wrapped} for c in ledger},
            'never_ran': sorted(c['id'] for c in ledger if c['id'] in wrapped and not ran.get(c['id'])),
            'not_instrumented': sorted(skipped)}


def run(questions=QUESTIONS, out=None, limit=None, stack=None):
    from run_integrated_stack import keys
    keys()
    ledger = ledger_components()
    wrapped, skipped = instrument(ledger)
    from clinical_core.agere.clinical import conversation
    if stack is None:
        from clinical_core.agere.clinical.stack_runtime import Stack
        build = Path(json.loads((ROOT/'config/agere/active_warehouse.json').read_text())['build'])
        stack = Stack(build/'stack', allow_live=True)
    rows = load_questions(questions, limit)
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    ran, answered_by, statuses, conv = defaultdict(set), Counter(), Counter(), {}
    with (out/'per_question.jsonl').open('w') as log:
        for r in rows:
            CALLS.clear(); started = time.perf_counter()
            row = ask(stack, conversation, r, conv)
            for cid in CALLS: ran[cid].add(r['id'])
            statuses[row['status']] += 1
            if row['answered_by']: answered_by[row['answered_by']] += 1
            log.write(json.dumps({**row, 'components_run': sorted(CALLS), 'seconds': round(time.perf_counter() - started, 1)},
                                 default=str) + '\n')
            log.flush()
    rep = report(ledger, wrapped, skipped, ran, statuses, answered_by, len(rows), str(questions))
    from clinical_core.agere.clinical import stage_diagnostics
    rep['stages'] = stage_diagnostics.summarize(map(json.loads, (out/'per_question.jsonl').read_text().splitlines()))
    (out/'usage.json').write_text(json.dumps(rep, indent=1))
    return rep


# ---------------------------------------------------------------------------------------------------- the usage gate
def from_per_question(path, ledger=None):
    """A usage report rebuilt from a (possibly still running) per_question.jsonl: instrumentable ledger components that
    import, and the questions each one ran on."""
    ledger = ledger or ledger_components()
    rows = [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]
    ran, statuses, answered_by = defaultdict(set), Counter(), Counter()
    for r in rows:
        for cid in r.get('components_run') or []: ran[cid].add(r['id'])
        statuses[r.get('status')] += 1
        if r.get('answered_by'): answered_by[r['answered_by']] += 1
    wrapped, skipped = [], []
    for c in ledger:
        ok = False
        if instrumentable(c):
            try:
                owner = importlib.import_module(c['module'])
                for part in c['symbol'].split('.'): owner = getattr(owner, part)
                ok = callable(owner)
            except Exception:  # noqa: BLE001
                ok = False
        (wrapped if ok else skipped).append(c['id'])
    rep = report(ledger, wrapped, skipped, ran, statuses, answered_by, len(rows), str(path))
    from clinical_core.agere.clinical import stage_diagnostics
    rep['stages'] = stage_diagnostics.summarize(rows)
    return rep


def usage_root():
    """AGERE_USAGE_DIR, else the nearest artifacts/usage (a worktree reads its main checkout's runs)."""
    import os
    if os.environ.get('AGERE_USAGE_DIR'): return Path(os.environ['AGERE_USAGE_DIR'])
    return next((p / 'artifacts' / 'usage' for p in (ROOT, *ROOT.parents) if (p / 'artifacts' / 'usage').exists()), ROOT / 'artifacts' / 'usage')


def latest_report(root=None):
    """Newest usage.json, else the newest per_question.jsonl rebuilt; None when no run exists."""
    root = Path(root or usage_root())
    found = sorted((p for p in root.glob('*/usage.json')), key=lambda p: p.stat().st_mtime)
    logs = sorted((p for p in root.glob('*/per_question.jsonl')), key=lambda p: p.stat().st_mtime)
    if logs and (not found or logs[-1].stat().st_mtime > found[-1].stat().st_mtime + 60):
        return from_per_question(logs[-1]), str(logs[-1])
    if found: return json.loads(found[-1].read_text()), str(found[-1])
    return None, None


def check(rep, ledger=None):
    """-> {'never_ran': [ids of wired, instrumented components that ran on no question], 'not_instrumented': [non-build-time
    components the tracer cannot wrap], 'allowlisted': [build-time-only ids]}. Passing = both lists empty."""
    ledger = {c['id']: c for c in (ledger or ledger_components())}
    build_time = sorted(i for i, c in ledger.items() if c.get('current_wiring') == BUILD_TIME)
    comps = rep.get('components') or {}
    never = sorted(i for i, c in ledger.items() if i not in build_time and (comps.get(i) or {}).get('instrumented')
                   and not (comps.get(i) or {}).get('ran_on'))
    unmeasured = sorted(i for i in ledger if i not in build_time and i not in comps)  # ledger grew after the run
    blind = sorted(i for i in rep.get('not_instrumented') or [] if i in ledger and i not in build_time)
    return {'never_ran': never, 'not_measured_in_run': unmeasured, 'not_instrumented': blind, 'allowlisted': build_time,
            'questions': rep.get('questions'), 'question_set': rep.get('question_set')}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--questions', type=Path, default=QUESTIONS)
    p.add_argument('--out', type=Path)
    p.add_argument('--limit', type=int)
    p.add_argument('--check', type=Path, help='usage run directory (or usage.json / per_question.jsonl) to gate')
    a = p.parse_args()
    if a.check:
        target = a.check
        rep = (json.loads((target/'usage.json').read_text()) if (target/'usage.json').exists() else from_per_question(target/'per_question.jsonl')) \
            if target.is_dir() else json.loads(target.read_text()) if target.suffix == '.json' else from_per_question(target)
        verdict = check(rep)
        print(json.dumps({k: v for k, v in verdict.items() if k != 'allowlisted'}, indent=1))
        raise SystemExit(1 if verdict['never_ran'] or verdict['not_instrumented'] else 0)
    rep = run(a.questions, a.out, a.limit)
    print(json.dumps({k: rep[k] for k in ('questions', 'statuses', 'answered_by')}, indent=1))
    print('instrumented', sum(1 for c in rep['components'].values() if c['instrumented']), 'never ran', len(rep['never_ran']))


if __name__ == '__main__':
    main()
