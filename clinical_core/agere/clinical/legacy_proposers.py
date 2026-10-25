"""Planner components that fell off the question path, brought back as CHECKED proposers and helpers.

Every *_tier function follows the Ladder rung contract: -> (graph, receipt) or None. The graph is an analysis_graph
plan that stack.run_graph executes; the receipt carries 'route', 'new_paid_api_calls' and cost 'steps' that
economical_planner.economics prices. Nothing here answers on its own: the cascade (proposers.Cascade.graph) literal-checks
the plan, executes it read-only and checks the requested output, so a wrong proposal is rejected and the next proposer
tries (heal, don't block).
  * semantic_analyst_tier: the single Haiku semantic analyst (tool loop over the semantic layer) with the Haiku reconcile (Sonnet on the escalation rung)
    turn when its stated answer disagrees with what its own SQL returned, or with a second candidate; paid.
  * scoped_review_tier: the exact documentary source-review command grammar (scoped_review_compiler); free.
  * stack_planner_tier: the older registry plan proposer (stack_planner.propose, one Haiku call; Sonnet on the escalation rung), converted to a graph.
  * legacy_ladder_tier: economical_planner.legacy_plan end to end with the legacy together semantics (fast path deferred
    to the analyst, analyst vs fast comparison, Haiku reconcile on disagreement); last resort.
Helpers: learned_router_hint / router_order (MiniLM logistic engine lean) and vbc_context (VBC knowledge snippets for
the hosted interpreter's planning context).
"""
import os
import re
import sqlite3
import time
from pathlib import Path

VERSION = 'legacy-proposers/1'
ROUTER_TIERS = ('metric_router', 'operator_router')


def _as_of(stack):
    return stack.retriever.meta['snapshot']['as_of']


def _semantic_path(stack):
    return Path(stack.path)/'semantic.sqlite3'


def _live(stack, ledger):
    return bool(getattr(stack, 'allow_live', False)) and ledger is not None


def _decline_graph(question, missing, as_of):
    from .analysis_graph import validate
    return validate({'interpretation': f'Unsupported by received data: {question}'[:2000], 'coverage': 'unsupported', 'nodes': [],
                     'unmet_requirements': [str(m)[:1000] for m in (missing or ['Missing input'])][:20]}, as_of)


def _sql_graph(question, sql, returns, as_of, purpose):
    """One executable sql node. The analyst binds :as_of; the graph executor binds only :population, so it is inlined."""
    from .analysis_graph import validate
    query = re.sub(r':as_of\b', "'" + str(as_of).replace("'", '') + "'", sql.strip().rstrip(';'))
    return validate({'interpretation': question[:2000], 'coverage': 'supported_observation', 'unmet_requirements': [],
                     'nodes': [{'id': 'answer', 'operation': 'sql', 'inputs': [],
                                'parameters': {'query': query, 'returns': returns}, 'purpose': purpose}]}, as_of)


def _returns(answer_type, question):
    from .sql_query import RETURNS
    from .analysis_graph import requested_shape
    return answer_type if answer_type in RETURNS else (requested_shape(question) or 'table')


def _metered_step(stage, receipts, usd):
    """A cost step economics() prices: the ledgered receipts, else the analyst's own metered total, else unknown."""
    stages = list(receipts or [])
    if not stages and usd is not None:
        stages = [{'source': 'metered_total', 'cost_estimate': {'estimated_usd': usd}}]
    step = {'stage': stage, 'receipt': {'stages': stages}}
    if usd is None or not stages: step['cost_unknown'] = True
    return step


def _answering_sql(run):
    """The last SQL the analyst ran successfully, with its rows (the query its answer was read from)."""
    for call in reversed(run.get('tool_calls') or []):
        out = call.get('result')
        if call.get('sql') and isinstance(out, dict) and 'error' not in out: return call['sql'], out
    return None, None


_SCALE_DECIMALS = {'percent': 2, 'per_100': 2, 'mean': 2, 'average': 2}


def _plain_sql(sql):
    """One analyst SELECT as a bare statement: comments and the closing semicolon removed (it is embedded in another query)."""
    return re.sub(r'\s*;\s*$', '', re.sub(r'--[^\n]*', ' ', str(sql or '')).strip()).strip()


def ratio_sql(ratio):
    """The ONE query whose single cell is the analyst's share / rate / average: its numerator and denominator queries, divided in SQL exactly as the
    code computes the stated value (analyst_definitions.compute_ratio / SCALES; two decimals for percent and means, four otherwise). None when the
    ratio does not carry both queries. The graph executes this, so the answer the cascade checks is the share, not one of its two parts."""
    from . import analyst_definitions as D
    if not isinstance(ratio, dict): return None
    num, den = _plain_sql(ratio.get('numerator_sql')), _plain_sql(ratio.get('denominator_sql'))
    scale = str(ratio.get('scale') or 'fraction').lower()
    if not num or not den or scale not in D.SCALES: return None
    scaled = f'({num}) * {float(D.SCALES[scale])} / NULLIF(({den}), 0)'
    return f'SELECT ROUND({scaled}, {_SCALE_DECIMALS.get(scale, 4)}) AS value'


def analyst_queries(answer, calls):
    """The SQL one analyst answer rests on: {'final', 'numerator', 'denominator', 'composed', 'queries'}.
    final: the query its answer was read from (an audit-rewritten answer query first, then the last successful non-audit call, then the declared
    sql); numerator / denominator: the audited (possibly rewritten) queries of a share, else the declared ones. composed: the single query the
    graph executes (the share built from numerator and denominator, else final). queries: every query of composed, for the checks."""
    answer = answer if isinstance(answer, dict) else {}
    calls = [c for c in calls or [] if isinstance(c, dict) and c.get('sql') and not (isinstance(c.get('result'), dict) and 'error' in c['result'])]
    audited = {c['audit']: c['sql'] for c in calls if c.get('audit')}
    ratio = answer.get('ratio') if isinstance(answer.get('ratio'), dict) else None
    plain = [c['sql'] for c in calls if not c.get('audit')]
    declared = answer.get('sql') if isinstance(answer.get('sql'), str) and answer['sql'].strip() else None
    final = audited.get('answer') or (plain[-1] if plain else None) or declared
    out = {'final': final, 'numerator': None, 'denominator': None, 'composed': None, 'queries': []}
    if ratio:
        out['numerator'] = audited.get('numerator') or ratio.get('numerator_sql')
        out['denominator'] = audited.get('denominator') or ratio.get('denominator_sql')
        both = ratio_sql({**ratio, 'numerator_sql': out['numerator'], 'denominator_sql': out['denominator']})
        if both:
            out['composed'], out['queries'] = both, [out['numerator'], out['denominator']]
            return out
    out['composed'] = final
    out['queries'] = [final] if final else []
    return out


def analyst_views(queries):
    """The definition views (analyst_definitions.VIEW_NAMES) the queries read."""
    from . import analyst_definitions
    text = ' '.join(re.sub(r"'[^']*'", "''", re.sub(r'--[^\n]*', ' ', q or '')) for q in queries or []).lower()
    return {v for v in analyst_definitions.VIEW_NAMES if re.search(rf'\b{v}\b', text)}


def analyst_graph(question, answer, calls, contract, as_of, columns, receipt, *, rows=None, rec=False):
    """-> (graph, receipt) for a recorded or live analyst answer, or (None, reason) when it makes no proposal. The graph executes
    analyst_queries()['composed'] (a share is computed from its numerator and denominator, not read from one of them); the receipt carries
    analyst_evidence (every query of the proposal) for the acceptance checks, and the definitions audit of EACH query is bound to the graph's digest
    (acceptance_contract.audit_bound)."""
    from . import analyst_definitions, acceptance_contract as AC
    found = analyst_queries(answer, calls)
    if not found['composed']: return None, 'no sql'
    composed = found['composed'] is not found['final'] and bool(found['numerator'])
    returns = _returns((contract or {}).get('answer_type'), question)
    shown = (rows or {}).get('rows') if isinstance(rows, dict) else None
    if composed: returns = 'value'
    elif returns in ('count', 'value') and shown is not None and (len(shown) != 1 or len(shown[0]) != 1):
        returns = 'table'  # the answering query returns several outputs: a table
    graph = _sql_graph(question, found['composed'], returns, as_of, 'Semantic analyst query (reconciled by Claude)' if rec else 'Semantic analyst query')
    receipt['analyst_evidence'] = {'queries': [q for q in found['queries'] if q], 'composed': composed, 'views': sorted(analyst_views(found['queries']))}
    if isinstance(receipt.get('definitions_audit'), dict):
        patient_set = 'patient_set' if (contract or {}).get('answer_type') == 'patient_set' else None
        findings = []
        for q in receipt['analyst_evidence']['queries']:
            try:
                findings += analyst_definitions.audit_sql(question, as_of, q, columns, patient_set if not composed else None)
            except Exception as exc:  # noqa: BLE001 - an audit that cannot run does not authorize
                findings.append({'rule': 'audit_error', 'detail': f'{type(exc).__name__}: {exc}'[:200], 'mechanical': False})
        receipt['definitions_audit']['final_sql_digest'] = AC.sql_digest(graph)
        receipt['definitions_audit']['final_sql_findings'] = [f['rule'] for f in findings]
        if findings:
            receipt['definitions_audit'].update(action='declined', reason='the final sql departs from the decision-context definitions: '
                                                + '; '.join(f['detail'] for f in findings)[:300])
            return None, receipt['definitions_audit']['reason']
    return graph, receipt


def _self_check(answer, result):
    """None when the stated count/value matches the single cell the answering SQL returned, else the executed reading."""
    if not answer or answer.get('answer_type') not in ('count', 'value') or not result: return None
    rows = result.get('rows') or []
    if len(rows) != 1 or len(rows[0]) != 1: return None
    cell = next(iter(rows[0].values()))
    stated = answer.get(answer['answer_type'])
    try:
        if stated is not None and cell is not None and abs(float(stated) - float(cell)) <= 1e-6 * max(1.0, abs(float(cell))): return None
    except (TypeError, ValueError):
        pass
    return {**answer, answer['answer_type']: cell}


def _send(stack, ledger, receipts):
    from .together import _ledgered_send
    return _ledgered_send(stack, ledger, receipts)


def _analyst(stack, question, ledger, against=None):
    """-> {'graph', 'receipt', 'picked'} or None. picked: 'analyst' | 'against' | 'neither' | 'abstain' | None."""
    if not _live(stack, ledger) or os.environ.get('AGERE_SEMANTIC_ANALYST', 'on') == 'off': return None
    path = _semantic_path(stack)
    if not path.is_file(): return None
    from . import semantic_analyst, together
    started, receipts = time.perf_counter(), []
    wh = together.warehouse(path)
    as_of = together._snapshot_as_of(stack) or wh.as_of
    model = together.semantic_model()
    own = getattr(stack, 'send_factory', None)  # a workspace stack's own sender
    if own is not None:
        with own() as send: run = semantic_analyst.analyze(question, as_of, wh, send=send, model=model, format_retries=1)
    else:
        run = semantic_analyst.analyze(question, as_of, wh, send=_send(stack, ledger, receipts), model=model, format_retries=1)
    steps = [_metered_step('semantic_analyst', receipts, run.get('estimated_cost_usd'))]
    paid = run.get('new_paid_api_calls') or 0
    answer = together._contract(run.get('answer'))
    sql, rows = _answering_sql(run)
    row = {'answer': answer, 'tool_calls': run.get('tool_calls') or []}
    # The reconcile turn: a second candidate that disagrees, else the analyst's own executed SQL when its stated answer differs.
    second, labels = None, ('analyst', 'against')
    if against is not None and (against.get('answer') or {}).get('status'):
        if semantic_analyst.canonical(answer) != semantic_analyst.canonical(against.get('answer')): second = against
    elif sql and answer and answer.get('status') == 'answered':
        executed = _self_check(answer, rows)
        if executed is not None: second, labels = {'answer': executed, 'tool_calls': [{'sql': sql}]}, ('analyst', 'executed')
    picked, rec = ('analyst' if answer else None), None
    if second is not None:
        rec_receipts = []
        rec = semantic_analyst.reconcile(question, as_of, row, second, wh, send=_send(stack, ledger, rec_receipts),
                                         model=together.reconcile_model(), seed_key=f'legacy:{question}', labels=labels)
        r = rec.get('result') or {}
        steps.append(_metered_step('semantic_reconcile', rec_receipts, rec.get('reconcile_cost_usd')))
        paid += r.get('new_paid_api_calls') or 0
        picked = {'executed': 'analyst'}.get(rec['picked'], rec['picked'])
        if picked == 'neither':
            answer = together._contract(rec.get('answer'))
            ok = [c.get('sql') for c in r.get('tool_calls') or [] if c.get('sql') and 'error' not in (c.get('result') or {})]
            sql = ok[-1] if ok else None
    receipt = {'route': 'semantic_analyst', 'model': run.get('model') or model, 'new_paid_api_calls': paid,
               'frontier_called': False, 'steps': steps, 'analyst_sql': [c.get('sql') for c in run.get('tool_calls') or []][-8:],
               'analyst_error': run.get('error'),
               'reconcile': ({k: rec.get(k) for k in ('picked', 'reason', 'reconcile_error', 'reconcile_sql')} if rec else None),
               'version': VERSION, 'elapsed_seconds': round(time.perf_counter() - started, 3)}
    audit = run.get('definitions_audit') if isinstance(run.get('definitions_audit'), dict) else None
    if audit: receipt['definitions_audit'] = {k: audit.get(k) for k in ('version', 'action', 'rewrites', 'arithmetic')}
    if picked in ('against', 'abstain'): return {'graph': None, 'receipt': receipt, 'picked': picked}
    if audit and audit.get('action') == 'declined':  # a failed definitions audit passes the question on; it is not a "data absent" answer
        return {'graph': None, 'receipt': receipt, 'picked': None}
    if answer and answer.get('status') == 'unsupported':
        return {'graph': _decline_graph(question, answer.get('missing'), _as_of(stack)), 'receipt': receipt, 'picked': picked}
    if not (answer and answer.get('status') in ('answered', 'partial') and sql):
        return {'graph': None, 'receipt': receipt, 'picked': None}
    # What the checks and the executor see is ALL the SQL the answer rests on (analyst_graph): a share's numerator and denominator, not whichever query
    # ran last (that was the denominator's audit query, so the question's numbers, concepts, window and ordering all looked "missing" from the plan).
    if rec and picked == 'neither':
        raw, tool_calls = rec.get('answer'), (rec.get('result') or {}).get('tool_calls')
        rows = _answering_sql(rec.get('result') or {})[1]
    else:
        raw, tool_calls = run.get('answer'), run.get('tool_calls')
    graph, built = analyst_graph(question, raw if isinstance(raw, dict) else {}, tool_calls, answer, _as_of(stack), wh.columns, receipt, rows=rows, rec=bool(rec))
    if graph is None:
        return {'graph': None, 'receipt': receipt, 'picked': None}
    return {'graph': graph, 'receipt': receipt, 'picked': picked}


def semantic_analyst_tier(stack, question, ledger, against=None):
    """The legacy single semantic analyst (Haiku tool loop) with the reconcile turn (Haiku; Sonnet on the escalation rung). -> (graph, receipt) or None.
    against: an optional second candidate row ({'answer', 'tool_calls'}); a disagreement goes to the reconciler."""
    out = _analyst(stack, question, ledger, against)
    if not out or out['graph'] is None: return None
    return out['graph'], out['receipt']


def scoped_review_tier(stack, question):
    """Exact documentary source-review commands ('Review respiratory sources for patient P-1002'). Free; abstains otherwise."""
    from .scoped_review_compiler import compile_question
    try:
        with sqlite3.connect(Path(stack.path)/'stack.sqlite3') as db:
            roster = {row[0] for row in db.execute('SELECT id FROM patients')}
        graph, receipt, seed = compile_question(question, as_of=_as_of(stack), known_patient_ids=roster)
    except (ValueError, sqlite3.Error):
        return None
    return graph, {**receipt, 'model': None, 'frontier_called': False, 'decision_seed': seed, 'steps': []}


def plan_to_graph(plan, as_of):
    """A validated stack_planner plan (cohort + up to four registry steps) as an analysis graph."""
    from .analysis_graph import validate
    from . import cohorts
    nodes, scoped = [{'id': 'population', 'operation': 'cohort', 'inputs': [], 'parameters': {'specification': plan['cohort']},
                      'purpose': 'Requested population'}], {}
    for i, step in enumerate(plan['steps']):
        source = 'population'
        if step['patient_id']:
            source = scoped.get(step['patient_id'])
            if source is None:
                source = scoped[step['patient_id']] = f'patient_{len(scoped)}'
                spec = {k: list(v) for k, v in plan['cohort'].items()} if isinstance(plan['cohort'], dict) else cohorts.empty()
                spec['all'] = [*spec.get('all', []), {'kind': 'patient', 'field': 'patient_id', 'op': 'eq', 'value': step['patient_id']}]
                nodes.append({'id': source, 'operation': 'cohort', 'inputs': [], 'parameters': {'specification': spec},
                              'purpose': f"Patient {step['patient_id']} named in the question"})
        if step['operation'] == 'evidence_search':
            nodes.append({'id': f'step_{i}', 'operation': 'retrieve', 'inputs': [source],
                          'parameters': {'query': step['query'], 'analysis_window': plan['analysis_window']}, 'purpose': step['purpose'] or 'Evidence search'})
        else:
            nodes.append({'id': f'step_{i}', 'operation': 'analyze', 'inputs': [source],
                          'parameters': {'operator': step['operation'], 'drug_class': step['drug_class'], 'view': step['view'],
                                         'analysis_window': plan['analysis_window']}, 'purpose': step['purpose'] or step['operation']})
    unsupported = plan['coverage'] == 'unsupported' or not plan['steps']
    return validate({'interpretation': plan['interpretation'], 'coverage': 'unsupported' if unsupported else plan['coverage'],
                     'nodes': [] if unsupported else nodes,
                     'unmet_requirements': (plan['unmet_requirements'] or ['No registered operation fits']) if unsupported
                     else plan['unmet_requirements']}, as_of)


def stack_planner_tier(stack, question, ledger):
    """The older registry plan proposer (stack_planner.propose, one Haiku call; Sonnet on the escalation rung). -> (graph, receipt) or None."""
    if not _live(stack, ledger): return None
    from .stack_planner import propose
    import json
    started = time.perf_counter()
    path = Path(stack.path)
    values = json.loads((path/'cohort-values.json').read_text()) if (path/'cohort-values.json').exists() else None
    plan, receipt = propose(question, stack.receipts, ledger, snapshot=stack.retriever.meta['snapshot'], observed_values=values)
    graph = plan_to_graph(plan, _as_of(stack))
    from .. import model_policy
    return graph, {'route': 'stack_planner', 'model': model_policy.model_for('planner'),
                   'new_paid_api_calls': 0 if receipt.get('cache_hit') else 1, 'frontier_called': False,
                   'stack_plan': {k: plan[k] for k in ('interpretation', 'coverage', 'unmet_requirements')},
                   'steps': [{'stage': 'stack_planner', 'receipt': receipt}], 'version': VERSION,
                   'elapsed_seconds': round(time.perf_counter() - started, 3)}


def _no_frontier(capabilities, feedback=None):
    from .question_input import QuestionNotUnderstood
    raise QuestionNotUnderstood('Hosted frontier planning is not available on this server.')


def _merge(receipt, extra_steps, extra_calls, **detail):
    return {**receipt, 'route': 'legacy_cost_ladder:' + str(receipt.get('route')),
            'steps': [*(receipt.get('steps') or []), *extra_steps],
            'new_paid_api_calls': (receipt.get('new_paid_api_calls') or 0) + extra_calls, 'legacy': detail, 'version': VERSION}


def legacy_ladder_tier(stack, question, ledger, frontier):
    """economical_planner.legacy_plan end to end (the pre-2026-10-19 ladder order, first plan wins) under the legacy
    together semantics: with a semantic layer and live models the fast path defers its paid rungs to the analyst; the
    analyst's answer is compared with the fast plan's executed answer and Haiku reconciles a disagreement; a deferred fast
    path whose analyst produced nothing re-runs the full ladder. -> (graph, receipt) or None."""
    from .economical_planner import legacy_plan, defer_to_analyst
    from .question_input import QuestionNotUnderstood
    from . import together
    frontier = frontier or _no_frontier
    analysts = _live(stack, ledger) and _semantic_path(stack).is_file() and os.environ.get('AGERE_SEMANTIC_ANALYST', 'on') != 'off'

    def ladder(defer):
        try:
            with defer_to_analyst(defer): return legacy_plan(stack, question, ledger, frontier)
        except QuestionNotUnderstood:
            return None

    fast = ladder(analysts and together.deferral_on())
    if not analysts: return fast
    deferred = fast is not None and fast[1].get('route') == 'deferred_to_analyst'
    fast_row = None
    if fast is not None and not deferred and fast[0].get('nodes'):
        try:
            executed = stack.run_graph(fast[0], question=question)
            fast_row = together._fast_row({**executed, 'planner_receipt': fast[1]})
        except (ValueError, KeyError, TypeError, RuntimeError, sqlite3.Error):
            fast_row = None
    out = _analyst(stack, question, ledger, against=fast_row)
    steps, calls = (out['receipt']['steps'], out['receipt']['new_paid_api_calls']) if out else ([], 0)
    detail = {'analyst': (out or {}).get('receipt', {}).get('route'), 'picked': (out or {}).get('picked'),
              'reconcile': (out or {}).get('receipt', {}).get('reconcile'), 'deferred': deferred}
    reconciled = bool(out and out['receipt'].get('reconcile'))
    if out and out['graph'] is not None and (fast_row is None or (reconciled and out['picked'] in ('analyst', 'neither'))):
        graph, receipt = out['graph'], out['receipt']
        return graph, _merge(receipt, [s for s in (fast[1].get('steps') or [])] if fast else [],
                             fast[1].get('new_paid_api_calls', 0) if fast else 0, **detail)
    if fast is not None and not deferred:
        return fast[0], _merge(fast[1], steps, calls, **detail)
    # Deferred, and the analyst produced nothing usable: the full fast path after all (hosted ladder, frontier).
    full = ladder(False)
    if full is None: return None
    return full[0], _merge(full[1], [*steps, *((fast or [None, {}])[1].get('steps') or [])],
                           calls + ((fast or [None, {}])[1].get('new_paid_api_calls') or 0), fallback=True, **detail)


# -- helpers ---------------------------------------------------------------------------------------------------------------
def learned_router_hint(stack, question):
    """Engine names ranked by the learned router (MiniLM logistic head); [] when no encoder/model or AGERE_ENGINE_LEAN=off."""
    encoder = getattr(getattr(stack, 'retriever', None), 'encoder', None)
    if encoder is None or os.environ.get('AGERE_ENGINE_LEAN', 'on') == 'off': return []
    try:
        from . import learned_router
        model = learned_router.load()
        if model is None or getattr(encoder, 'identity', None) != model['encoder_identity']: return []
        p = learned_router.probabilities(model, learned_router.features([question], encoder))[0]
    except (ValueError, RuntimeError, OSError, KeyError, ImportError):
        return []
    from .capability_registry import PRODUCT
    return [learned_router.LABELS[i] for i in sorted(range(len(p)), key=lambda i: -p[i]) if learned_router.LABELS[i] in PRODUCT]


def router_order(stack, question, order):
    """The cascade order with operator_router ahead of metric_router when the learned router leans to an analytic engine
    (Ladder.engine_lean semantics, ENGINE_LEAN probability). -> (order tuple, lean receipt or None)."""
    from .economical_planner import _engine_lean
    lean = _engine_lean(stack, question)
    order = tuple(order)
    if not (lean and lean['lean']): return order, lean
    swapped = list(order)
    for metric, operator in (('metric_router', 'operator_router'), ('s1_metric_router', 's1_operator_router')):  # Claude pair, System One pair
        if metric in swapped and operator in swapped and swapped.index(operator) > swapped.index(metric):
            i, j = swapped.index(metric), swapped.index(operator)
            swapped[i], swapped[j] = operator, metric
    return tuple(swapped), lean


def vbc_context(question, encoder=None):
    """VBC knowledge entries confidently matching the question (vbc_knowledge.context_for), as planning-context text; ''."""
    try:
        from .vbc_knowledge import context_for
        hits = context_for(question, encoder=encoder)
    except Exception:  # noqa: BLE001 - background knowledge never blocks planning
        return ''
    if not hits: return ''
    lines = ['Value-based-care background (cited knowledge entries; definitions and program context, not warehouse data):']
    for h in hits:
        cites = '; '.join(f"{c.get('publisher') or c.get('title')}: {c.get('url')}" for c in h.get('citations') or [] if isinstance(c, dict))
        lines.append(f"- {h['title']} [{h['id']}]" + (f' ({cites})' if cites else ''))
    return '\n'.join(lines)
