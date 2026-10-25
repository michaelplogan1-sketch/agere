"""Offline replay of RECORDED semantic-analyst answers through the acceptance checks the live cascade applies to a semantic_analyst proposal.

No model call, no key, no network: the input is a saved analyst run (tools/dev_set/analyst_calib.py output: rows with outcome correct / wrong /
declined and the analyst's answer incl. its final sql and, for a share, numerator_sql / denominator_sql). Per row the harness builds the
graph + receipt the cascade would build (legacy_proposers.analyst_graph when present, else the pre-fix construction), then runs, over the same
stack, the same checks proposers.Cascade.graph runs, each on its own so a rejection by one does not hide the others:

    literal      literal_check.missing over the executable text of the plan                      (plan time)
    ledger       requirement_ledger.gaps (comparator direction, AND/OR, literal gaps)             (plan time)
    contract     acceptance_contract.evaluate(phase='plan') unmet parts, by kind                  (plan time; what Cascade.contract_gaps enforces)
    fence        plan_fence.validate (applies only to the saved-work tiers: never to the analyst)
    execute      stack.run_graph on the read-only synthetic semantic layer (an execution failure rejects)
    shape        literal_check.shape_misfit on the executed answer
    gate         answer_checks.check (live-only parts off: no ledger, no provider), executed-phase acceptance contract included
    cascade      the real Cascade.graph('semantic_analyst', ...) end to end: accepted or the first rejection

and reports, per check, how many CORRECT and how many WRONG recorded answers it rejects. Rows the analyst or its definitions audit declined
never become a proposal and are counted apart.

    .venv/bin/python tools/dev_set/analyst_replay.py --run /tmp/acal_dev3_haiku.json --run bench/results/dev_set/analyst-calib-haiku-dev-20261024.json
    .venv/bin/python tools/dev_set/analyst_replay.py --run X.json --mode before     # the pre-fix construction (final sql only), for the comparison
    .venv/bin/python tools/dev_set/analyst_replay.py --run X.json --detail          # every rejection reason per row

The dev3 run must hold the BUILD half only (this harness reads no question file: the question text is on each recorded row).
"""
import argparse
import json
import os
import re
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CHECKS = ('literal', 'ledger', 'contract', 'fence', 'execute', 'shape', 'gate', 'cascade')


def _env():
    os.environ.update(AGERE_EVALUATION='1', AGERE_RESULT_CACHE='off', AGERE_CLAUDE='off', AGERE_LOCAL_TYPED='off', AGERE_LOCAL_NORMALIZER='off',
                      AGERE_LOCAL_QWEN='off', AGERE_LOCAL_MODEL='off', AGERE_LOCAL_SQL='off', AGERE_BOTTLED='off', AGERE_BOTTLED_COHORT='off',
                      AGERE_LOCAL_CANDIDATE='off', AGERE_SEMANTIC_ANALYST='off')
    for k in ('ANTHROPIC_API_KEY', 'TYPESAFE_API_KEY', 'PERPLEXITY_API_KEY'):
        os.environ.pop(k, None)


def open_stack():
    """A Stack over a cold copy of the synthetic build (symlinks to every file except the stores that remember earlier questions), not live."""
    _env()
    from tools.dev_set import run_inprocess as RI
    from clinical_core.agere.clinical.stack_runtime import Stack
    return Stack(str(RI.cold_stack(tempfile.mkdtemp(prefix='analyst-replay-'))), allow_live=False), RI.STACK


def recorded_calls(response):
    """The analyst's recorded answer as (answer object, [(role, sql)]) in the order the live run holds them: its own query, then the audit
    queries finalize() appended (numerator, denominator)."""
    answer = (response or {}).get('answer') or {}
    ratio = answer.get('ratio') if isinstance(answer.get('ratio'), dict) else None
    calls = []
    if answer.get('sql'):
        calls.append({'sql': answer['sql'], 'result': {'rows': 1}})
    if ratio:
        for role in ('numerator', 'denominator'):
            if ratio.get(f'{role}_sql'):
                calls.append({'sql': ratio[f'{role}_sql'], 'result': {'value': ratio.get(role)}, 'audit': role})
    return answer, calls


def build(question, answer, calls, as_of, wh, mode):
    """-> (graph, receipt) the cascade would check, or (None, reason) when the analyst / its audit never makes a proposal."""
    from clinical_core.agere.clinical import legacy_proposers as L, together, analyst_definitions, acceptance_contract as AC
    contract = together._contract(answer)
    if not contract or contract.get('status') not in ('answered', 'partial'):
        return None, f'status {answer.get("status")}'
    receipt = {'route': 'semantic_analyst', 'new_paid_api_calls': 0, 'frontier_called': False, 'steps': [],
               'definitions_audit': {'version': analyst_definitions.VERSION, 'action': 'ok', 'rewrites': [], 'arithmetic': None}}
    if mode == 'after' and hasattr(L, 'analyst_graph'):
        out = L.analyst_graph(question, answer, calls, contract, as_of, wh.columns, receipt)
        return out
    sql, _ = L._answering_sql({'tool_calls': calls})
    if not sql:
        return None, 'no sql'
    returns = L._returns(contract.get('answer_type'), question)
    graph = L._sql_graph(question, sql, returns, as_of, 'Semantic analyst query')
    final = analyst_definitions.audit_sql(question, as_of, sql, wh.columns, 'patient_set' if contract.get('answer_type') == 'patient_set' else None)
    receipt['definitions_audit'].update(final_sql_digest=AC.sql_digest(graph), final_sql_findings=[f['rule'] for f in final])
    if final:
        return None, 'final audit: ' + '; '.join(f['rule'] for f in final)
    return graph, receipt


def classify(reason):
    r = reason.lower()
    if r.startswith('number(s)'): return 'number'
    if 'negat' in r: return 'negation'
    if r.startswith('month '): return 'month'
    if 'from the question is not in the plan' in r: return 'concept'
    if 'ranking' in r or 'ordering' in r or 'top ' in r or 'limit' in r: return 'ranking'
    if 'breakdown' in r or 'grouping' in r or 'group' in r: return 'grouping'
    if 'denominator' in r or 'rate or share' in r: return 'share'
    if 'average' in r: return 'average'
    if 'window' in r or 'calendar year' in r or '12 months' in r: return 'window'
    if 'asks for' in r and ('plan applies' in r): return 'threshold'
    if 'disjunction' in r or 'requires both' in r: return 'boolean'
    if 'shape' in r or 'plan returned' in r or 'asks for a' in r: return 'shape'
    if 'decision context' in r: return 'decision_context'
    return 'other'


def evaluate_row(stack, as_of, row, mode):
    from clinical_core.agere.clinical import (literal_check, requirement_ledger, acceptance_contract as AC, decision_context as DC, answer_checks, proposers as P,
                                              plan_fence)
    question = row['asked']
    answer, calls = recorded_calls(row.get('response'))
    wh = stack._replay_wh
    graph, receipt = build(question, answer, calls, as_of, wh, mode)
    out = {'id': row['id'], 'outcome': row['outcome'], 'bucket': row.get('bucket'), 'question': question}
    if graph is None:
        out['no_proposal'] = receipt
        return out
    rej = {}
    with DC.scope(question, as_of):
        casc = P.Cascade(stack, SimpleNamespace(identity={'as_of': as_of}), question, ledger=SimpleNamespace(close=lambda: None))
        extra = getattr(AC, 'analyst_text', None)
        extra_text = extra(receipt, graph) if (extra and mode == 'after') else ''
        text = AC.evidence(graph=graph) + (('\n' + extra_text) if extra_text else '')
        rej['literal'] = literal_check.missing(question, text.lower() if False else text)
        rej['ledger'] = requirement_ledger.gaps(question, text)
        plan = AC.evaluate(question, graph=graph, as_of=as_of, phase='plan', extra_text=extra_text)
        rej['contract'] = list(plan['unmet'])
        rej['fence'] = [] if 'semantic_analyst' not in plan_fence.FENCED else ['fenced']
        result = None
        try:
            result = stack.run_graph(graph, question=question)
            rej['execute'] = []
        except (ValueError, KeyError, TypeError, RuntimeError) as exc:
            rej['execute'] = [f'execution failed: {type(exc).__name__}: {exc}'[:200]]
        rej['shape'], rej['gate'] = [], []
        if result is not None:
            from clinical_core.agere.clinical.answer_shape import final
            try:
                result = final(question, graph, receipt, result)
            except (ValueError, KeyError, TypeError):
                pass
            m = literal_check.shape_misfit(question, result.get('answer'), **({'analyst': True} if mode == 'after' else {}))
            rej['shape'] = [m] if m else []
            out['executed'] = {k: (result.get('answer') or {}).get(k) for k in ('answer_type', 'value', 'count')}
            verdict = answer_checks.check(stack, question, graph, json.loads(json.dumps(receipt, default=str)), result, tier='semantic_analyst')
            rej['gate'] = [] if verdict['accept'] else list(verdict['reasons'])
        # the cascade end to end, on a fresh copy of the receipt
        casc = P.Cascade(stack, SimpleNamespace(identity={'as_of': as_of}), question, ledger=SimpleNamespace(close=lambda: None))
        got = casc.graph('semantic_analyst', (graph, json.loads(json.dumps(receipt, default=str))))
        rej['cascade'] = [] if got is not None else [e['detail'] for e in casc.log if e['outcome'] == 'rejected'][-1:] or ['rejected']
    out['rejections'] = rej
    return out


def summarize(rows, title, detail=False):
    scored = [r for r in rows if 'rejections' in r]
    lines = [f'== {title}: {len(rows)} rows; proposals {len(scored)}; no proposal {sum("no_proposal" in r for r in rows)} '
             f'(' + ', '.join(f'{k} {v}' for k, v in Counter(r['outcome'] for r in rows if 'no_proposal' in r).items()) + ')']
    correct = [r for r in scored if r['outcome'] == 'correct']
    wrong = [r for r in scored if r['outcome'] == 'wrong']
    lines.append(f'   proposals: correct {len(correct)}, wrong {len(wrong)}')
    lines.append(f'   {"check":10s} {"correct rejected":>17s} {"wrong rejected":>15s}')
    for check in CHECKS:
        c = sum(bool(r['rejections'][check]) for r in correct)
        w = sum(bool(r['rejections'][check]) for r in wrong)
        lines.append(f'   {check:10s} {c:>10d}/{len(correct):<6d} {w:>8d}/{len(wrong):<6d}')
    kinds = defaultdict(lambda: [0, 0])
    for r in scored:
        for check in ('contract', 'gate'):
            seen = {classify(x) for x in r['rejections'][check]}
            for k in seen:
                kinds[f'{check}:{k}'][0 if r['outcome'] == 'correct' else 1] += 1
    lines.append('   by kind (rows touched)      correct  wrong')
    for k, (c, w) in sorted(kinds.items()):
        lines.append(f'     {k:26s} {c:5d}  {w:5d}')
    if detail:
        for r in scored:
            if r['rejections']['cascade']:
                lines.append(f'   [{r["outcome"]:7s}] {r["id"]}: ' + ' | '.join(r['rejections']['cascade'])[:600])
    return '\n'.join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--run', action='append', required=True, help='a recorded analyst run (analyst_calib.py output); repeatable')
    ap.add_argument('--mode', choices=('before', 'after'), default='after')
    ap.add_argument('--detail', action='store_true')
    ap.add_argument('--out', help='write the per-row results as JSON')
    args = ap.parse_args(argv)
    stack, built = open_stack()
    from clinical_core.agere.clinical import semantic_analyst
    stack._replay_wh = semantic_analyst.SemanticWarehouse(Path(stack.path) / 'semantic.sqlite3')
    as_of = stack.retriever.meta['snapshot']['as_of']
    everything = {}
    for path in args.run:
        data = json.loads(Path(path).read_text())
        rows = [evaluate_row(stack, as_of, r, args.mode) for r in data['rows']]
        everything[path] = rows
        print(summarize(rows, f'{Path(path).name} [{args.mode}]', args.detail))
    if args.out:
        Path(args.out).write_text(json.dumps(everything, default=str, indent=1))


if __name__ == '__main__':
    main()
