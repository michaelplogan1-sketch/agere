"""Every question-answering component as a CHECKED proposer for the shared planner.

The shared planner first tries its exact tiers (contract cache, learned shapes, fully consumed grammar). When they
decline, this cascade asks the other components one at a time, cheapest and best-calibrated first. A component only
proposes; nothing it proposes executes or answers until it passes:
  * typed proposals (rewordings, local/hosted typed interpreters): the shared compiler/validator recompiles them against
    the ORIGINAL question, quote binding and the model audit apply as before, and the literal-question check reads the
    compiled SQL and parameters;
  * graph/SQL proposals (rungs of the former cost ladder, routers, hosted SQL, frontier graph planner): analysis_graph
    validation, the literal-question check (literal_check.missing) over the executable plan, then read-only execution and
    the requested-output check (literal_check.shape_misfit);
  * executed analyst answers (the three-analyst hybrid core): the literal check over every SQL it ran plus the output check.
Tiers that failed held-out calibration (local compiler 19/49 wrong, metric router 13/13, operator router 19/19, free-form
Qwen SQL) run last and only propose. When every proposal fails, the question is not understood and the receipt names
what each proposer would have dropped; a reduced or substituted question is never executed (2026-10-19 protection).
Honest declines naming missing data (undelivered input, missing-data claims) stand only if nothing later answers.
"""
import json
import os
import time
from pathlib import Path

from . import request_plan
from .literal_check import VERSION as CHECK_VERSION, executable_text, missing, shape_misfit
from .question_input import QuestionNotUnderstood

VERSION = 'checked-proposer-cascade/1'

# name -> (cost class, calibration evidence from the 2026-10-21 component inventory, what it proposes)
TIERS = {
    'exact_local_plan': ('free', 'exact authored plans and recipes; exact normalized-question match', 'graph'),
    'specialist_families_early': ('free', 'DQ/VBC families certified, bounds 2.45% / 2.5%', 'graph'),
    'cohort_family_early': ('free (Haiku slot filler only when slots are missing)', 'certified 0.71, 752 cases / 0 errors', 'graph'),
    'undelivered_input': ('free', 'deterministic delivered-field check; declines only', 'decline'),
    'paraphrase_reuse': ('free (MiniLM)', 'verified interpretation of a slot-identical rewording', 'typed'),
    'semantic_cache': ('free (MiniLM)', 'certified, 0/94 test errors', 'graph'),
    'learned_verified_sql': ('free', 'verified hosted answers, n=3, uncalibrated', 'graph'),
    'specialist_families_late': ('free', 'DQ/VBC families with recorded convention defaults', 'graph'),
    'cohort_family_late': ('free (Haiku slot filler only when slots are missing)', 'certified 0.71', 'graph'),
    'local_normalizer': ('free (Qwen3-4B LoRA)', 'rewording guarded, then fully consumed grammar', 'typed'),
    'qwen_rewrite_compiler': ('free (Qwen3-4B)', 'dropped a statin constraint once; rewrite_dropped + literal check', 'graph'),
    'bottled_sql': ('free (Qwen3-4B LoRA, k-agree)', 'distilled from verified answers; lint + sanity + agreement', 'graph'),
    'qwen_typed_interpreter': ('free (Qwen3-4B, isolated worker)', 'unqualified; compiler + literal check', 'typed'),
    'typed_decider': ('Perplexity, ~$0.000006/call', 'curated unfiltered recipes only, 0.95 threshold', 'graph'),
    'hosted_typed_interpreter': ('Opus 5.5, ~$0.27/question', '20-question dev set; compiler + quote binding + audit', 'typed'),
    'hosted_verified_sql': ('Haiku -> Sonnet -> Opus judge + Perplexity decider', 'two-model agreement + oracles; uncalibrated', 'graph'),
    'frontier_graph_planner': ('Opus 5.5', 'full capability catalog; uncalibrated', 'graph'),
    'hybrid_analysts': ('3x Opus 5.5 + vote, ~$0.14/question', '18/20 dev, 7/10 held-out, pilot 75/100', 'answer'),
    'local_compiler': ('free', 'FAILED calibration: held-out 19/49 wrong; proposes only', 'graph'),
    'metric_router': ('Haiku', 'FAILED calibration: held-out 13/13 wrong; proposes only', 'graph'),
    'operator_router': ('Haiku', 'FAILED calibration: held-out 19/19 wrong; proposes only', 'graph'),
    'local_free_sql': ('free (Qwen3-4B)', 'FAILED calibration: precision too low; proposes only', 'graph'),
}
ORDER = tuple(TIERS)
PAID = {'typed_decider', 'hosted_typed_interpreter', 'hosted_verified_sql', 'frontier_graph_planner', 'hybrid_analysts',
        'metric_router', 'operator_router'}
TYPED_WITH_POPULATION = {'qwen_typed_interpreter', 'hosted_typed_interpreter'}  # the only tiers that honour a saved cohort

# Components that are not proposers, and why (stack_runtime.model_roles reports these).
NOT_PROPOSERS = {
    'answer_shape': 'post-execution: answer_shape.final reshapes unambiguous outputs before the requested-output check',
    'verify lint/sanity': 'inside the SQL rungs: every reused/bottled/hosted SQL is linted (and sanity-checked) before proposal',
    'verification_v2 / oracles': 'inside hosted_verified_sql: independent candidates and oracles decide agreement',
    'gate_policy / cascade certificates': 'inside the families, semantic cache and typed decider thresholds',
    'learned_router': 'orders the Haiku routers (engine lean); proposes nothing itself (12.7% wrong as a router)',
    'answer_guard cross-check': 'stays out: 4/22 caught for $3.19 and its re-plan would bypass these checks; AGERE_CROSS_CHECK remains off',
    'Jev / Perplexity source tools': 'patient-scoped evidence review (Stack.review), not question interpretation; unchanged',
    'retrieval, rerankers, readers, engines, governed metrics': 'used by the proposers above when their plans name them',
}


def enabled(name, stack):
    skip = {s.strip() for s in os.environ.get('AGERE_PROPOSER_SKIP', '').split(',') if s.strip()}
    if name in skip or os.environ.get('AGERE_PROPOSERS', 'on') == 'off': return False
    if name in PAID and not getattr(stack, 'allow_live', False): return False
    return True


def evidence_of(contract):
    """Executable text of a typed contract: compiled SQL, predicates, outputs and engine parameters, never quotes."""
    return executable_text({k: v for k, v in contract.items()
                            if k in ('query', 'membership_query', 'request', 'analysis', 'program', 'nodes', 'returns')})


class Cascade:
    def __init__(self, stack, catalog, question, *, population=None, ledger=None, hosted_model=None, memory_identity=None):
        self.stack, self.catalog, self.question, self.population = stack, catalog, question, population
        self.hosted_model, self.memory_identity = hosted_model, memory_identity
        self._ledger, self._owned = ledger, False
        self._ladder = None
        self.log, self.usd, self.calls, self.cost_known = [], 0.0, 0, True
        self.held_decline = None

    # -- shared resources -------------------------------------------------------------------------------------------
    @property
    def ledger(self):
        if self._ledger is None:
            from ..gateway import BudgetLedger, default_ledger_path
            self._ledger, self._owned = BudgetLedger(default_ledger_path()), True
        return self._ledger

    def close(self):
        if self._owned: self._ledger.close()

    @property
    def ladder(self):
        if self._ladder is None:
            from .economical_planner import Ladder
            frontier = None
            if getattr(self.stack, 'allow_live', False):
                def frontier(capabilities, feedback=None):
                    from .graph_planner import propose
                    from .source_profile import load as load_profile
                    from .semantic_layer import describe
                    path = Path(self.stack.path)
                    values = json.loads((path/'cohort-values.json').read_text()) if (path/'cohort-values.json').exists() else None
                    return propose(self.question, self.stack.receipts, self.ledger, snapshot=self.stack.retriever.meta['snapshot'],
                                   graph_catalog=capabilities, observed_values=values, profile=load_profile(path),
                                   semantic=describe(path), feedback=feedback)
            ledger = self.ledger if getattr(self.stack, 'allow_live', False) else None
            self._ladder = Ladder(self.stack, self.question, ledger, frontier)
        return self._ladder

    def spend(self, calls, usd, known=True):
        self.calls += calls or 0
        self.usd += usd or 0.0
        self.cost_known = self.cost_known and known

    # -- the cascade --------------------------------------------------------------------------------------------------
    def run(self):
        try:
            for name in ORDER:
                if self.population is not None and name not in TYPED_WITH_POPULATION: continue
                if not enabled(name, self.stack): continue
                started = time.perf_counter()
                try:
                    found = getattr(self, 'tier_' + name)()
                except QuestionNotUnderstood as exc:
                    self.note(name, 'declined', str(exc), started); continue
                except (ValueError, KeyError, TypeError, RuntimeError, OSError, AttributeError) as exc:
                    self.note(name, 'error', f'{type(exc).__name__}: {exc}', started); continue
                if found is None:
                    self.note(name, 'no proposal', None, started); continue
                if found == 'held':
                    self.note(name, 'decline held', None, started); continue
                self.note(name, 'accepted', None, started)
                contract, interpretation = found
                return contract, self.finish(interpretation, name)
            if self.held_decline:
                contract, interpretation = self.held_decline
                return contract, self.finish(interpretation, interpretation['interpreter'])
            reasons = [f"{e['proposer']}: {e['detail']}" for e in self.log if e['outcome'] == 'rejected']
            exc = QuestionNotUnderstood(
                'The original question was preserved; no proposer produced a plan that keeps all of it, so nothing was '
                'executed.' + (' Closest proposals and what they dropped: ' + ' | '.join(reasons[:4]) if reasons else ''))
            exc.planner_receipt = self.usage()
            exc.planner_receipt['proposers'] = self.log
            raise exc
        finally:
            self.close()

    def note(self, name, outcome, detail, started):
        self.log.append({'proposer': name, 'outcome': outcome, 'detail': detail,
                         'seconds': round(time.perf_counter() - started, 3)})

    def reject(self, name, reasons):
        self.log.append({'proposer': name, 'outcome': 'rejected', 'detail': '; '.join(reasons), 'seconds': None})
        return None

    def usage(self):
        return {'new_paid_api_calls': self.calls, 'known_api_estimate_usd': round(self.usd, 6), 'api_cost_known': self.cost_known}

    def finish(self, interpretation, name):
        cost, calibration, kind = TIERS[name]
        return {**interpretation, 'interpreter': interpretation.get('interpreter') or name,
                'proposer': {'name': name, 'cost': cost, 'calibration': calibration, 'kind': kind,
                             'check': CHECK_VERSION, 'cascade': VERSION},
                'proposers': self.log, 'cascade_usage': self.usage()}

    # -- typed proposals ------------------------------------------------------------------------------------------------
    def typed(self, name, found):
        """Accept a compiled typed contract only when the literal check passes over its executable parts."""
        if not found: return None
        contract, interpretation = found
        gaps = missing(self.question, evidence_of(contract))
        if gaps: return self.reject(name, gaps)
        return contract, {**interpretation, 'literal_check': 'passed'}

    def tier_paraphrase_reuse(self):
        from .shared_planner import reuse_paraphrase
        return self.typed('paraphrase_reuse', reuse_paraphrase(self.stack, self.catalog, self.question, self.population,
                                                                self.memory_identity))

    def tier_local_normalizer(self):
        from .shared_planner import reuse_normalizer
        return self.typed('local_normalizer', reuse_normalizer(self.catalog, self.question, self.population, self.memory_identity))

    def tier_qwen_typed_interpreter(self):
        if os.environ.get('AGERE_LOCAL_TYPED', 'on') == 'off': return None
        from .nl2sql import MODEL_DIR
        if not MODEL_DIR.exists(): return None
        from .shared_planner import isolated_local
        contract, interpretation = isolated_local(self.catalog, self.question, self.population)
        unresolved = request_unresolved(contract)
        audit = interpretation.get('semantic_review') or {}
        if unresolved: return self.reject('qwen_typed_interpreter', ['unresolved: ' + '; '.join(map(str, unresolved))[:300]])
        if not audit.get('equivalent') or audit.get('issues'):
            return self.reject('qwen_typed_interpreter', ['local review: ' + '; '.join(audit.get('issues') or ['not equivalent'])[:300]])
        contract = recompile(self.catalog, self.question, contract, self.population)
        return self.typed('qwen_typed_interpreter', (contract, {**interpretation, 'interpreter': 'qwen_typed_interpreter',
                                                               'review': 'local_audit_compiler_literal_check',
                                                               'execution_accepted': True}))

    def tier_hosted_typed_interpreter(self):
        from .nl2sql import HostedModel
        from .shared_planner import checked_interpret, interpretation_window, REQUEST_MAX_BYTES
        hosted = HostedModel(self.ledger, self.stack.receipts, model=self.hosted_model, request_max_bytes=REQUEST_MAX_BYTES)
        try:
            with interpretation_window(self.stack):
                contract, interpretation = checked_interpret(self.catalog, self.question, population=self.population, model=hosted)
        except Exception as exc:
            spent = getattr(exc, 'planner_receipt', None) or {}
            self.spend(spent.get('new_paid_api_calls', 0), spent.get('known_api_estimate_usd', 0), spent.get('api_cost_known', True))
            raise
        from .shared_planner import attempt_receipt
        spent = attempt_receipt(interpretation.get('attempts', []))
        self.spend(spent['new_paid_api_calls'], spent['known_api_estimate_usd'], spent['api_cost_known'])
        audit = interpretation.get('semantic_review')
        unresolved = request_unresolved(contract)
        nodes_verified = (contract.get('kind') != 'request_program' or
                          all(n['equivalent'] and not n['issues'] for n in (audit or {}).get('nodes', [])))
        if not (audit and audit['equivalent'] and not audit['issues'] and not unresolved and nodes_verified):
            # A partial with its missing piece named is an answer to the asked question, not a substitute.
            if unresolved and audit and audit['equivalent']:
                interpretation['hosted_dispatch_authority'] = 'server_allow_live_after_deterministic_decline'
                self.held_decline = self.held_decline or (contract, {**interpretation, 'interpreter': 'hosted_typed_interpreter'})
                return 'held'
            return self.reject('hosted_typed_interpreter', (audit or {}).get('issues') or ['semantic review did not pass'])
        interpretation.update(execution_accepted=True, review='checked_hosted_interpretation',
                              hosted_dispatch_authority='server_allow_live_after_deterministic_decline',
                              acceptance_scope='Compiled and reviewed synthetic observation; not clinical qualification')
        return self.typed('hosted_typed_interpreter', (contract, interpretation))

    # -- graph proposals (former ladder rungs) ------------------------------------------------------------------------
    def graph(self, name, found):
        """Validate (done by the rung), literal-check, execute read-only, then check the output shape."""
        if not found: return None
        graph, receipt = found
        from .economical_planner import economics
        econ = economics(receipt)
        self.spend(receipt.get('new_paid_api_calls', 0), econ['known_api_estimate_usd'], not econ['failed_or_unpriced_api_cost'])
        if graph.get('coverage') == 'unsupported' and not graph.get('nodes'):
            decline = self.declined(name, graph, receipt)
            if name == 'undelivered_input': return decline
            self.held_decline = self.held_decline or decline
            return 'held'
        gaps = missing(self.question, executable_text([n.get('parameters') for n in graph.get('nodes', [])]))
        if gaps: return self.reject(name, gaps)
        try:
            result = self.stack.run_graph(graph, question=self.question)
        except (ValueError, KeyError, TypeError, RuntimeError) as exc:
            return self.reject(name, [f'execution failed: {type(exc).__name__}: {exc}'[:300]])
        from .answer_shape import final
        try:
            result = final(self.question, graph, receipt, result)
        except (ValueError, KeyError, TypeError):
            pass  # reshaping is optional; the output check below decides
        misfit = shape_misfit(self.question, result.get('answer'))
        if misfit: return self.reject(name, [misfit])
        return self.executed(name, graph, receipt, result, paid_execution=result.get('new_paid_api_calls', 0))

    def declined(self, name, graph, receipt):
        answer = {'status': 'not_available', 'answer_type': 'unsupported', 'headline': 'Not available: ' + '; '.join(graph['unmet_requirements'])[:300],
                  'missing': graph['unmet_requirements'], 'patient_ids': [], 'actionable_cohort': False,
                  'data_absence_established': False, 'limitations': [f'Declined by {name}; no substitute question was run.']}
        return self.executed(name, graph, receipt, {'answer': answer, 'results': []})

    def executed(self, name, graph, receipt, result, paid_execution=0):
        if paid_execution: self.spend(paid_execution, 0, False)
        contract = {'kind': 'checked_graph', 'graph': graph, 'legacy_receipt': receipt, 'executed': result,
                    'source_identity': self.catalog.identity,
                    'id': request_plan.fingerprint({'question': self.question, 'graph': graph})}
        return contract, {'interpreter': name, 'review': 'literal_check_then_output_check', 'execution_accepted': True,
                          'legacy_route': receipt.get('route'), 'attempts': []}

    def tier_exact_local_plan(self): return self.graph('exact_local_plan', self.ladder.exact_local())
    def tier_specialist_families_early(self): return self.graph('specialist_families_early', self.ladder.specialist_families(True))
    def tier_cohort_family_early(self): return self.graph('cohort_family_early', self.ladder.cohort_family(True))
    def tier_undelivered_input(self): return self.graph('undelivered_input', self.ladder.undelivered())
    def tier_semantic_cache(self): return self.graph('semantic_cache', self.ladder.semantic_cache())
    def tier_learned_verified_sql(self): return self.graph('learned_verified_sql', self.ladder.learned_sql())
    def tier_specialist_families_late(self): return self.graph('specialist_families_late', self.ladder.specialist_families(False))
    def tier_cohort_family_late(self): return self.graph('cohort_family_late', self.ladder.cohort_family(False))
    def tier_qwen_rewrite_compiler(self): return self.graph('qwen_rewrite_compiler', self.ladder.qwen_rewrite(default='on'))
    def tier_bottled_sql(self):
        return self.graph('bottled_sql', self.ladder.bottled_sql(enabled=os.environ.get('AGERE_BOTTLED', 'on') != 'off'))
    def tier_typed_decider(self): return self.graph('typed_decider', self.ladder.typed_decider())
    def tier_hosted_verified_sql(self): return self.graph('hosted_verified_sql', self.ladder.hosted_sql())
    def tier_frontier_graph_planner(self):
        return self.graph('frontier_graph_planner', self.ladder.frontier_planner(decider=False))
    def tier_local_compiler(self): return self.graph('local_compiler', self.ladder.local_compiler())
    def tier_metric_router(self): return self.graph('metric_router', self.ladder.metric_router())
    def tier_operator_router(self): return self.graph('operator_router', self.ladder.operator_router())
    def tier_local_free_sql(self): return self.graph('local_free_sql', self.ladder.local_sql(default='on'))

    # -- executed analyst answers ---------------------------------------------------------------------------------------
    def tier_hybrid_analysts(self):
        if os.environ.get('AGERE_HYBRID', 'on') == 'off': return None
        from .together import _run_hybrid, _snapshot_as_of
        from .analysis_graph import requested_shape
        run = _run_hybrid(self.stack, self.question, _snapshot_as_of(self.stack), Path(self.stack.path)/'semantic.sqlite3',
                          requested_shape(self.question))
        self.spend(run.get('new_paid_api_calls', 0), run.get('estimated_cost_usd', 0))
        answer = run.get('answer')
        if answer is None: return self.reject('hybrid_analysts', [str(run.get('error'))[:300]])
        graph = {'interpretation': self.question, 'coverage': 'supported_observation', 'unmet_requirements': [],
                 'nodes': [{'id': f'analyst_{i}', 'operation': 'sql', 'inputs': [],
                            'parameters': {'query': c['sql'], 'returns': answer.get('answer_type')},
                            'purpose': 'Independent analyst query (executed and voted)'} for i, c in enumerate(run.get('tool_calls') or [])]}
        receipt = {'route': 'hybrid_analysts', 'model': run.get('model'), 'hybrid': run.get('hybrid'),
                   'new_paid_api_calls': run.get('new_paid_api_calls', 0)}
        if answer['status'] == 'unsupported':
            self.held_decline = self.held_decline or self.declined('hybrid_analysts', {**graph, 'nodes': [], 'unmet_requirements': answer['missing'] or ['Missing input']}, receipt)
            return 'held'
        if answer['status'] != 'answered':
            return self.reject('hybrid_analysts', ['the analysts found more than one reading: ' + json.dumps(answer.get('readings'), default=str)[:300]])
        gaps = [g for n in graph['nodes'] for g in missing(self.question, executable_text(n['parameters']))]
        if not graph['nodes'] or gaps: return self.reject('hybrid_analysts', gaps or ['no executed query to check'])
        misfit = shape_misfit(self.question, answer)
        if misfit: return self.reject('hybrid_analysts', [misfit])
        headline = answer.get('count', answer.get('value'))
        answer = {**answer, 'headline': f'{headline}' if headline is not None else f"{len(answer.get('table') or [])} rows",
                  'limitations': ['Three independent analyst queries were executed and voted; the literal check passed on each.']}
        return self.executed('hybrid_analysts', graph, receipt, {'answer': answer, 'results': [], 'new_paid_api_calls': 0})


def request_unresolved(contract):
    kind = contract.get('kind')
    if kind == 'general_analysis': return contract['analysis'].get('unresolved', [])
    if kind == 'request_program':
        return contract['program'].get('unresolved', []) + [u for n in contract['nodes'] for u in n['unresolved']]
    return contract['request'].get('unresolved', [])


def recompile(catalog, question, contract, population):
    """Rebuild a contract that crossed a process boundary under this process's compiler (never trust it as sealed)."""
    from . import request_program, general_analysis
    kind = contract.get('kind')
    if kind == 'request_program': return request_program.prepare(catalog, question, contract['program'], population=population)
    if kind == 'general_analysis': return general_analysis.prepare(catalog, question, contract['analysis'], population=population)
    return request_plan.compile_request(catalog, question, contract['request'], population=population)


def execute(stack, question, graph, receipt, *, started):
    """The checked proposal was executed inside the cascade; present that exact result."""
    contract = receipt['request_contract']
    result = dict(contract['executed'])
    answer = dict(result.get('answer') or {})
    answer.setdefault('missing', [])
    answer['limitations'] = list(dict.fromkeys([*(answer.get('limitations') or []),
        f"Proposed by {receipt['interpretation']['interpreter']} ({receipt['interpretation']['proposer']['calibration']}); "
        'accepted after the literal-question and output checks. Not clinical qualification.']))
    if answer.get('headline') is None:
        value = answer.get('count', answer.get('value'))
        answer['headline'] = str(value) if value is not None else f"{len(answer.get('table') or [])} rows"
    from .shared_planner import economics
    return {**result, 'schema': 'shared-warehouse-planner/5', 'synthetic': True, 'question': question,
            'route': 'shared_planner', 'graph': graph, 'answer': answer, 'request_contract': {k: v for k, v in contract.items() if k != 'executed'},
            'planner_receipt': {k: v for k, v in receipt.items() if k != 'request_contract'},
            'new_paid_api_calls': receipt['new_paid_api_calls'], 'planning_economics': economics(receipt),
            'elapsed_seconds': time.perf_counter() - started, 'clinical_execution_authorized': False}
