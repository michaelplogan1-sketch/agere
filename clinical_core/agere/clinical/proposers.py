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
import sqlite3
import sys
import threading
import time
from pathlib import Path

from . import request_plan
from .literal_check import VERSION as CHECK_VERSION, executable_text, missing, shape_misfit
from .question_input import QuestionNotUnderstood
from .answer_checks import gate as answer_gate
from .system_one_gate import CLAUDE_TIERS, outage
from . import requirement_ledger, plan_fence, claim_verification, stage_diagnostics
from .answer_budget import cancelled

VERSION = 'checked-proposer-cascade/1'

# name -> (cost class, calibration evidence from the 2026-10-21 component inventory, what it proposes)
TIERS = {
    'exact_local_plan': ('free', 'exact authored plans and recipes; exact normalized-question match', 'graph'),
    'scoped_review_compiler': ('free', 'exact source-review command grammar; abstains otherwise; uncalibrated', 'graph'),
    'specialist_families_early': ('free', 'DQ/VBC families certified, bounds 2.45% / 2.5%', 'graph'),
    'cohort_family_early': ('free (Haiku slot filler only when slots are missing)', 'certified 0.71, 752 cases / 0 errors', 'graph'),
    'undelivered_input': ('free', 'deterministic delivered-field check; declines only', 'decline'),
    'paraphrase_reuse': ('free (MiniLM)', 'verified interpretation of a slot-identical rewording', 'typed'),
    'terminology_mapping': ('free (saved/deterministic); Jev/Perplexity ~$0.0002 for an unmapped term, live only', 'retrieve-then-classify clinical term (System One p>=0.8 or abstain), then the fully consumed grammar; uncalibrated', 'typed'),
    'semantic_cache': ('free (MiniLM)', 'certified, 0/94 test errors', 'graph'),
    'template_decoding': ('free (MiniLM; local model only to fill slots)', 'TeCoD: verified query structure kept, only typed value slots refilled; literal-bound; uncalibrated', 'graph'),
    'evidence_retrieval': ('free (FTS5+MiniLM+BGE; Granite fallback)', 'ranked passages; literal + output check; uncalibrated', 'graph'),
    'learned_verified_sql': ('free', 'verified hosted answers, n=3, uncalibrated', 'graph'),
    'evidence_review': ('free readers first; Jev/Perplexity only with allow_live', 'students/windowed/Kev/d1 then typed providers; uncalibrated', 'graph'),
    'atomic_system_one': ('Jev/Perplexity ~$0.0002 per patient', 'atomic typed sub-questions per patient, aggregated in code; literal + output check; uncalibrated', 'graph'),
    'hcc_suspecting': ('free retrieval/ranking; Jev/Perplexity ~$0.0002 per confirmation', 'retrieve-then-rank ICD-10/HCC suspects, System One confirms; uncalibrated', 'graph'),
    'specialist_families_late': ('free', 'DQ/VBC families with recorded convention defaults', 'graph'),
    'cohort_family_late': ('free (Haiku slot filler only when slots are missing)', 'certified 0.71', 'graph'),
    'local_normalizer': ('free (Qwen3-4B LoRA)', 'rewording guarded, then fully consumed grammar', 'typed'),
    'qwen_rewrite_compiler': ('free (Qwen3-4B)', 'dropped a statin constraint once; rewrite_dropped + literal check', 'graph'),
    'bottled_sql': ('free (Qwen3-4B LoRA, k-agree)', 'distilled from verified answers; lint + sanity + agreement', 'graph'),
    'qwen_typed_interpreter': ('free (Qwen3-4B, isolated worker)', 'unqualified; compiler + literal check', 'typed'),
    'typed_decider': ('Perplexity, ~$0.000006/call', 'curated unfiltered recipes only, 0.95 threshold', 'graph'),
    'hosted_typed_interpreter': ('Opus 5.5, ~$0.27/question', '20-question dev set; compiler + quote binding + audit', 'typed'),
    'hosted_verified_sql': ('Haiku -> Sonnet -> Opus judge + Perplexity decider', 'two-model agreement + oracles; uncalibrated', 'graph'),
    'semantic_analyst': ('Haiku tool loop + Opus reconcile on disagreement', 'legacy single analyst; uncalibrated', 'graph'),
    'frontier_graph_planner': ('Opus 5.5', 'full capability catalog; uncalibrated', 'graph'),
    'stack_planner_propose': ('Opus 5.5, one call', 'older registry plan proposer; uncalibrated', 'graph'),
    'hybrid_analysts': ('3x Opus 5.5 + vote, ~$0.14/question', '18/20 dev, 7/10 held-out, pilot 75/100', 'answer'),
    'local_compiler': ('free', 'FAILED calibration: held-out 19/49 wrong; proposes only', 'graph'),
    'metric_router': ('Haiku', 'FAILED calibration: held-out 13/13 wrong; proposes only', 'graph'),
    'operator_router': ('Haiku', 'FAILED calibration: held-out 19/19 wrong; proposes only', 'graph'),
    'local_free_sql': ('free (Qwen3-4B)', 'FAILED calibration: precision too low; proposes only', 'graph'),
    'legacy_cost_ladder': ('free locally; its paid rungs only when live', 'pre-2026-10-19 ladder + together semantics, last resort; uncalibrated', 'graph'),
}
ORDER = tuple(TIERS)
PAID = {'typed_decider', 'hosted_typed_interpreter', 'hosted_verified_sql', 'frontier_graph_planner', 'hybrid_analysts',
        'metric_router', 'operator_router', 'semantic_analyst', 'stack_planner_propose', 'atomic_system_one'}
# Tiers whose cost is minutes, not seconds: the free local compiler (when its plan already passes the literal check) is tried before them, and
# each tier may use only a share of the answer budget (a tier that runs out yields like a decline; the cascade continues).
HEAVY = ('atomic_system_one', 'evidence_retrieval', 'evidence_review', 'qwen_rewrite_compiler', 'qwen_typed_interpreter', 'bottled_sql')
TIER_SHARE = {'heavy': 0.25, 'other': 0.5}
# Tiers that run a local MLX model (in process or in the typed-worker subprocess). Questions run concurrently (no service-wide
# lock), but two local models on the GPU at once exhausted Metal memory and aborted the server: these tiers take turns.
LOCAL_MODEL_TIERS = frozenset({'local_normalizer', 'qwen_rewrite_compiler', 'bottled_sql', 'qwen_typed_interpreter', 'local_free_sql',
                               'evidence_retrieval', 'evidence_review', 'cohort_family_early', 'cohort_family_late'})
LOCAL_MODEL_LOCK = threading.Lock()  # fraction of the answer budget one tier may use (paid tiers count as heavy)
FEEDBACK_READERS = ('hosted_typed_interpreter',)  # proposers whose prompt includes recorded corrections
TYPED_WITH_POPULATION = {'terminology_mapping', 'qwen_typed_interpreter', 'hosted_typed_interpreter', 'evidence_retrieval', 'evidence_review', 'atomic_system_one', 'hcc_suspecting'}  # the only tiers that honour a saved cohort

# Components that are not proposers, and why (stack_runtime.model_roles reports these).
NOT_PROPOSERS = {
    'answer_shape': 'post-execution: answer_shape.final reshapes, then answer_checks.check reviews shape/fit on every executed proposal',
    'verify lint/sanity': 'inside the SQL rungs: every reused/bottled/hosted SQL is linted (and sanity-checked) before proposal',
    'verification_v2 / oracles': 'inside hosted_verified_sql: independent candidates and oracles decide agreement',
    'gate_policy / cascade certificates': 'inside the families, semantic cache and typed decider thresholds; certificate annotated by answer_checks.check',
    'learned_router': 'orders the cascade (operator_router before metric_router on an engine lean) and leans operator_router',
    'answer_guard + zero-result probe + cross-check': 'answer_checks.check after every executed proposal; cross-check paid, live only, on uncertified answers',
    'Jev / Perplexity, local readers, retrieval, rerankers': 'evidence_retrieval and evidence_review tiers (free readers first, paid only live)',
    'engines, governed metrics': 'used by the proposers above when their plans name them',
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
        self.provider_error = None
        self.claude_down = None  # outage kind once a Claude tier failed on the provider: Claude tiers are skipped after that
        self.candidates = []  # executed proposals a check rejected: the System One fallback chooses among them on an outage
        self.routing = None
        self.learned_routing = None

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
    def order(self):
        """A recorded correction is read only by the hosted interpreter, so it goes first for that question; every other
        proposer still follows in its usual order."""
        from .learning import correction_context
        try:
            corrected = bool(correction_context(self.stack.path, self.question))
        except Exception:  # noqa: BLE001 - no learning store (fixtures) means no correction
            corrected = False
        self.corrected = corrected
        order = ORDER
        try:  # System One (Jev, then Perplexity) classifies the question and moves the fitting tiers first; never removes one
            from .system_one_gate import route_receipt, ready
            if ready(self.stack, 'AGERE_S1_ROUTER'):
                order, self.routing = route_receipt(self.question, order, stack=self.stack, ledger=self.ledger)
            if self.routing: self.spend(self.routing.get('calls', 0), self.routing.get('cost_usd', 0.0))
        except Exception:  # noqa: BLE001 - routing is an optimization; keep the default order
            order = ORDER
        order = tuple(n for n in FEEDBACK_READERS if n in order) + tuple(n for n in order if n not in FEEDBACK_READERS) \
            if corrected else tuple(order)
        try:
            from .legacy_proposers import router_order
            order = tuple(router_order(self.stack, self.question, order)[0])
        except Exception:  # noqa: BLE001 - no encoder (fixtures): keep the default order
            pass
        try:  # learned cascade router (labelled by which tier's checked answer was right): a permutation, never fewer tiers
            from .cascade_router import reorder as learned_reorder
            front = None
            if (self.routing or {}).get('applied'):
                from .system_one_gate import PINNED_FIRST, ROUTES
                moved = set(PINNED_FIRST) | set(ROUTES.get(self.routing.get('label'), ()))
                front = next((i for i, t in enumerate(order) if t not in moved), len(order))
            order, self.learned_routing = learned_reorder(self.stack, self.question, order, front=front)
        except Exception:  # noqa: BLE001 - no model / no data: keep the order
            pass
        try:  # outcome-labelled tier router (trained from verified answers): the predicted tier first, never removes one
            from .tier_router import reorder
            order, self.tier_routing = reorder(self.stack, self.question, order)
        except Exception:  # noqa: BLE001 - no promoted model / encoder: keep the order
            pass
        return self.free_first(order)

    def free_first(self, order):
        """local_compiler (free; arbitrated engine route) ahead of the heavy tiers when its plan already passes the literal check.
        A permutation: nothing is dropped, and a plan that fails the check leaves the order as it was."""
        order = tuple(order)
        heavy = [i for i, t in enumerate(order) if t in HEAVY]
        if 'local_compiler' not in order or not heavy or order.index('local_compiler') < min(heavy): return order
        try:
            graph, _ = self.ladder.compiled()
            if not graph or not graph.get('nodes') or graph.get('coverage') == 'unsupported': return order
            if self.gaps(executable_text([n.get('parameters') for n in graph['nodes']])): return order
        except Exception:  # noqa: BLE001 - no profile / fixture stack: keep the order
            return order
        first = order[min(heavy)]
        rest = [t for t in order if t != 'local_compiler']
        i = rest.index(first)
        self.free_first_receipt = {'moved': 'local_compiler', 'before': first}
        return tuple(rest[:i] + ['local_compiler'] + rest[i:])

    def tier_seconds(self, name, budget):
        """This tier's share of the answer budget in seconds, or None when the budget is unlimited."""
        if budget.seconds == float('inf'): return None
        return budget.seconds * TIER_SHARE['heavy' if name in HEAVY or name in PAID else 'other']

    def call_tier(self, name, limit):
        """Run one tier on the cascade's own thread with a deadline (no concurrent tiers: in-process models and stack writes stay
        serial). The tier's loops and model calls stop at the deadline (answer_budget.cancelled / tier_remaining); a tier that
        reaches it without a proposal yields like a decline (QuestionNotUnderstood) and the cascade continues."""
        fn = getattr(self, 'tier_' + name)
        try:
            if name in LOCAL_MODEL_TIERS:
                with LOCAL_MODEL_LOCK:  # one local model on the GPU at a time across concurrent questions
                    return self._call_tier(fn, name, limit)
            return self._call_tier(fn, name, limit)
        finally:
            mx = sys.modules.get('mlx.core')
            if mx is not None: mx.clear_cache()  # freed GPU buffers go back to the system before the next tier loads a model

    def _call_tier(self, fn, name, limit):
        if limit is None: return fn()
        from .answer_budget import _cancel, _deadline
        tokens = (_cancel.set(threading.Event()), _deadline.set(time.monotonic() + limit))
        try:
            found = fn()
            over = time.monotonic() >= _deadline.get()
        finally:
            _deadline.reset(tokens[1]); _cancel.reset(tokens[0])
        if found is None and over:
            raise QuestionNotUnderstood(f'time share exhausted: {name} used its {limit:.0f}s of the answer budget without a proposal')
        return found

    def run(self):
        from .answer_budget import start as start_budget
        budget = self.budget = start_budget()
        try:
            order = self.order()
            for name in order:
                if budget.expired() and budget.best():  # out of time with an executed candidate in hand; never skip to a refusal
                    budget.skip(name); continue
                if self.population is not None and name not in TYPED_WITH_POPULATION: continue
                if not enabled(name, self.stack): continue
                started = time.perf_counter()
                if self.claude_down and name in CLAUDE_TIERS:  # one outage per question: no repeated failing Claude calls
                    self.note(name, 'skipped', f'Claude unavailable ({self.claude_down}) earlier in this question', started); continue
                try:
                    found = self.call_tier(name, self.tier_seconds(name, budget))
                except QuestionNotUnderstood as exc:
                    self.note(name, 'declined', str(exc), started)
                    cause = exc.__cause__ or exc.__context__  # a decline wrapping a provider outage still means Claude is down
                    if name in CLAUDE_TIERS and cause is not None and outage(cause): self.claude_down = self.claude_down or outage(cause)
                    continue
                except Exception as exc:  # noqa: BLE001 - one component failing never stops the others (heal, don't block)
                    self.note(name, 'error', f'{type(exc).__name__}: {exc}', started)
                    from ..fault_containment import on_tier_error  # a malformed state store is quarantined and recreated, not hit again
                    healed = on_tier_error(getattr(self.stack, 'path', '.'), exc)
                    if healed: self.log[-1]['healed_stores'] = healed
                    if name in PAID and (outage(exc) or (isinstance(exc, RuntimeError) and not isinstance(exc, (ValueError, KeyError, TypeError, AttributeError, sqlite3.Error)))):
                        self.provider_error = self.provider_error or exc
                        if name in CLAUDE_TIERS: self.claude_down = self.claude_down or outage(exc)  # only a real provider outage (billing, rate, overload)
                    continue
                if found is None:
                    self.note(name, 'no proposal', None, started); continue
                if found == 'held':
                    self.note(name, 'decline held', None, started); continue
                self.note(name, 'accepted', None, started)
                contract, interpretation = claim_verification.verify(self, order, name, found)  # uncertified: an independent proposer checks it
                name = interpretation.get('verified_proposer') or name
                interpretation = {**interpretation, 'requirement_ledger': requirement_ledger.summary(
                    requirement_ledger.check(self.question, self.evidence(contract)))}
                return contract, self.finish(interpretation, name)
            if budget.expired() and budget.best():
                contract, interpretation = budget.best()  # time ran out: the best executed candidate, marked partial
                return contract, budget.partial(self.finish(interpretation, interpretation['interpreter']))
            if self.held_decline:
                contract, interpretation = self.held_decline
                return contract, self.finish(interpretation, interpretation['interpreter'])
            if self.provider_error is not None:
                found = self.system_one_fallback()  # Claude out: System One picks among the executed candidates (partial)
                if found: return found
                # A provider outage is reported as an outage (unverified), never as a question nobody could read.
                self.provider_error.planner_receipt = {**self.usage(), 'proposers': self.log,
                                                       'stage_diagnostics': stage_diagnostics.attribute(self.log, error=self.provider_error)}
                raise self.provider_error
            reasons = [f"{e['proposer']}: {e['detail']}" for e in self.log if e['outcome'] == 'rejected']
            exc = QuestionNotUnderstood(
                'The original question was preserved; no proposer produced a plan that keeps all of it, so nothing was '
                'executed.' + (' Closest proposals and what they dropped: ' + ' | '.join(reasons[:4]) if reasons else ''))
            exc.planner_receipt = self.usage()
            exc.planner_receipt['proposers'] = self.log
            exc.planner_receipt['stage_diagnostics'] = stage_diagnostics.attribute(self.log)  # which stage failed
            raise exc
        finally:
            self.close()

    def gaps(self, text):
        """Every requirement of the original question the executable text drops (requirement_ledger: literal check, comparator
        direction, AND/OR structure)."""
        return requirement_ledger.gaps(self.question, text)

    def evidence(self, contract):
        if (contract or {}).get('kind') == 'checked_graph':
            return executable_text([n.get('parameters') for n in contract['graph'].get('nodes', [])])
        return evidence_of(contract or {})

    def note(self, name, outcome, detail, started):
        self.log.append({'proposer': name, 'outcome': outcome, 'detail': detail,
                         'seconds': round(time.perf_counter() - started, 3)})

    def reject(self, name, reasons):
        if cancelled(): return None  # an abandoned tier records nothing
        self.log.append({'proposer': name, 'outcome': 'rejected', 'detail': '; '.join(reasons), 'seconds': None})
        return None

    def keep(self, name, pair, reasons=None):
        """Remember an executed proposal a check rejected (for the System One fallback); reasons default to its last rejection."""
        if reasons is None:
            reasons = next((e['detail'] for e in reversed(self.log) if e['proposer'] == name and e['outcome'] == 'rejected'), None)
            reasons = [reasons] if reasons else []
        if cancelled(): return
        self.candidates.append({'name': name, 'pair': pair, 'reasons': list(reasons)})

    def system_one_fallback(self):
        """Claude unavailable: system_one.decide picks the best executed-but-rejected candidate (never one System One's own check
        judged a different question); returned marked partial with the reason. None when nothing to pick or System One can't."""
        from .system_one_gate import fallback, ready
        pool, seen = [], set()
        for c in self.candidates:
            contract = c['pair'][0]
            if contract['id'] in seen or any('System One check:' in r for r in c['reasons']): continue
            seen.add(contract['id']); pool.append(c)
        started = time.perf_counter()
        if not pool: return None
        try:  # learned reward model: best-reward candidate first (System One still decides; nothing is dropped)
            from .candidate_selector import order as _reward_order
            plans = lambda c: ' '.join(str((n.get('parameters') or {}).get('query') or '') for n in (c['pair'][0]['graph'].get('nodes') or []))
            pool, _rewards = _reward_order([{**c, 'route': c['name'], 'plan': plans(c)} for c in pool])
            if _rewards: self.note('candidate_selector', 'ordered', json.dumps(_rewards)[:300], started)
        except Exception:  # noqa: BLE001 - ordering is advisory
            pass
        if os.environ.get('AGERE_S1_FALLBACK', 'on') == 'off' or not ready(self.stack, 'AGERE_S1_FALLBACK'):
            self.note('system_one_fallback', 'not available', f'{len(pool)} executed candidate(s); System One not ready', started)
            return None
        try:
            pick = fallback(self.question, [{'name': c['name'], 'graph': c['pair'][0]['graph'], 'result': c['pair'][0]['executed'],
                                             'reasons': c['reasons']} for c in pool], stack=self.stack, ledger=self.ledger)
        except Exception as exc:  # noqa: BLE001 - the outage is reported instead
            self.note('system_one_fallback', 'error', f'{type(exc).__name__}: {exc}'[:300], started); return None
        self.spend(pick['calls'], pick['cost_usd'])
        if pick['index'] is None:
            self.note('system_one_fallback', 'no proposal', f"System One ({pick['decided_by']}) found no candidate that answers "
                      f"the question as asked ({pick['label']}, p={pick['p']:.2f})", started)
            return None
        chosen = pool[pick['index']]
        why = (f"Claude was unavailable ({self.claude_down or 'provider error'}); System One ({pick['decided_by']}, p={pick['p']:.2f}) "
               f"chose this executed candidate from {chosen['name']} among {pick['candidates']}"
               + (f"; open checks: {'; '.join(chosen['reasons'])[:300]}" if chosen['reasons'] else '') + '.')
        self.note('system_one_fallback', 'accepted', why, started)
        contract, interpretation = chosen['pair']
        executed = dict(contract['executed'])
        answer = dict(executed.get('answer') or {})
        answer['limitations'] = list(dict.fromkeys([*(answer.get('limitations') or []), 'Partial: ' + why]))
        answer.setdefault('confidence', 'low')
        contract = {**contract, 'executed': {**executed, 'answer': answer}}
        interpretation = self.finish(interpretation, chosen['name'])
        return contract, {**interpretation, 'partial': True, 'partial_reason': 'system_one_fallback_provider_unavailable',
                          'system_one_fallback': pick, 'limitations': [*(interpretation.get('limitations') or []), why]}

    def usage(self):
        return {'new_paid_api_calls': self.calls, 'known_api_estimate_usd': round(self.usd, 6), 'api_cost_known': self.cost_known}

    def finish(self, interpretation, name):
        cost, calibration, kind = TIERS[name]
        if getattr(self, 'corrected', False) and 'feedback_identity' not in interpretation:
            from .learning import feedback_identity
            interpretation = {**interpretation, 'feedback_identity': feedback_identity(self.stack.path, self.question),
                              'feedback_consumed': name in FEEDBACK_READERS}
        return {**interpretation, 'interpreter': interpretation.get('interpreter') or name,
                'proposer': {'name': name, 'cost': cost, 'calibration': calibration, 'kind': kind,
                             'check': CHECK_VERSION, 'cascade': VERSION},
                'proposers': self.log, 'cascade_usage': self.usage(), 'system_one_routing': self.routing,
                'stage_diagnostics': stage_diagnostics.attribute(self.log, answered_by=name),
                'cascade_routing': {'system_one': self.routing, 'learned': self.learned_routing,
                                    'tier_router': getattr(self, 'tier_routing', None),
                                    'free_first': getattr(self, 'free_first_receipt', None)}}

    # -- typed proposals ------------------------------------------------------------------------------------------------
    def typed(self, name, found):
        """Accept a compiled typed contract only when the literal check passes over its executable parts."""
        if not found: return None
        contract, interpretation = found
        gaps = self.gaps(evidence_of(contract))
        if gaps: return self.reject(name, gaps)
        return contract, {**interpretation, 'literal_check': 'passed'}

    def tier_paraphrase_reuse(self):
        from .shared_planner import reuse_paraphrase
        return self.typed('paraphrase_reuse', reuse_paraphrase(self.stack, self.catalog, self.question, self.population,
                                                                self.memory_identity))

    def tier_terminology_mapping(self):
        from .terminology import tier
        live = getattr(self.stack, 'allow_live', False)
        try:
            found = tier(self.stack, self.catalog, self.question, self.population, self.ledger if live else None)
        except QuestionNotUnderstood as exc:
            used = getattr(exc, 'terminology_usage', None) or {}
            self.spend(used.get('new_paid_api_calls', 0), used.get('known_api_estimate_usd', 0.0)); raise
        if not found: return None
        used = found[1].get('terminology_usage') or {}
        self.spend(used.get('new_paid_api_calls', 0), used.get('known_api_estimate_usd', 0.0))
        return self.typed('terminology_mapping', found)

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
        gaps = self.gaps(executable_text([n.get('parameters') for n in graph.get('nodes', [])]))
        if gaps: return self.reject(name, gaps)
        if name in plan_fence.FENCED:  # saved work: its premises must still hold before it is reused
            fence = plan_fence.validate(self.stack, self.question, graph, receipt, tier=name)
            if isinstance(receipt, dict): receipt['plan_fence'] = fence
            if not fence['current']: return self.reject(name, ['PlanFence: ' + i for i in fence['issues']])
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
        if misfit:
            offered = getattr(self, 'budget', None) and self.executed(name, graph, receipt, result)
            if offered: self.budget.offer(offered, label=name, reasons=[misfit]); self.keep(name, offered, [misfit])
            return self.reject(name, [misfit])
        return self.executed(name, graph, receipt, result, paid_execution=result.get('new_paid_api_calls', 0))

    def declined(self, name, graph, receipt):
        answer = {'status': 'not_available', 'answer_type': 'unsupported', 'headline': 'Not available: ' + '; '.join(graph['unmet_requirements'])[:300],
                  'missing': graph['unmet_requirements'], 'patient_ids': [], 'actionable_cohort': False,
                  'data_absence_established': False, 'limitations': [f'Declined by {name}; no substitute question was run.']}
        return self.executed(name, graph, receipt, {'answer': answer, 'results': []})

    def executed(self, name, graph, receipt, result, paid_execution=0):
        if paid_execution: self.spend(paid_execution, 0, False)
        if (result.get('answer') or {}).get('status') != 'not_available':
            checked = answer_gate(self, name, graph, receipt, result)
            if checked is None:  # answer_checks rejected it (zero-result probe, guard, shape, certificate, System One); next proposer tries
                self.keep(name, self.contract_of(name, graph, receipt, result)); return None
            result = checked
        return self.contract_of(name, graph, receipt, result)

    def contract_of(self, name, graph, receipt, result):
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
    def tier_template_decoding(self): return self.graph('template_decoding', self.ladder.template_decoding())
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
    def tier_evidence_retrieval(self):
        from .evidence_proposers import retrieval_tier
        return self.graph('evidence_retrieval', retrieval_tier(self.stack, self.question, self.population))
    def tier_evidence_review(self):
        from .evidence_proposers import review_tier
        return self.graph('evidence_review', review_tier(self.stack, self.question, self.population))
    def tier_atomic_system_one(self):
        from .atomic_questions import tier
        def early(graph):  # System One is paid per patient: a plan the literal check will reject is declined before any call
            gaps = self.gaps(executable_text([n.get('parameters') for n in graph.get('nodes', [])]))
            if gaps: raise QuestionNotUnderstood('literal pre-check before any System One call: the plan drops ' + '; '.join(gaps)[:300])
        return self.graph('atomic_system_one', tier(self.stack, self.question, self.population, ledger=self.ledger, early_check=early))
    def tier_hcc_suspecting(self):
        from .hcc_suspecting import tier
        return self.graph('hcc_suspecting', tier(self.stack, self.question, self.population))
    def tier_scoped_review_compiler(self):
        from .legacy_proposers import scoped_review_tier
        return self.graph('scoped_review_compiler', scoped_review_tier(self.stack, self.question))
    def tier_semantic_analyst(self):
        from .legacy_proposers import semantic_analyst_tier
        return self.graph('semantic_analyst', semantic_analyst_tier(self.stack, self.question, self.ledger))
    def tier_stack_planner_propose(self):
        from .legacy_proposers import stack_planner_tier
        return self.graph('stack_planner_propose', stack_planner_tier(self.stack, self.question, self.ledger))
    def tier_legacy_cost_ladder(self):
        from .legacy_proposers import legacy_ladder_tier
        live = getattr(self.stack, 'allow_live', False)
        live = live and not self.claude_down  # Claude already out for this question: only the ladder's free rungs
        return self.graph('legacy_cost_ladder', legacy_ladder_tier(self.stack, self.question, self.ledger if live else None,
                                                                   self.ladder.frontier))

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
        gaps = [g for n in graph['nodes'] for g in self.gaps(executable_text(n['parameters']))]
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
