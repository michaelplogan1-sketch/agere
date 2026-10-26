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
import re
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

from . import request_plan
from .. import model_policy
from .literal_check import VERSION as CHECK_VERSION, executable_text, missing, shape_misfit
from .question_input import QuestionNotUnderstood
from .answer_checks import gate as answer_gate
from .system_one_gate import CLAUDE_TIERS, outage
from . import requirement_ledger, plan_fence, claim_verification, stage_diagnostics
from .answer_budget import cancelled

VERSION = 'checked-proposer-cascade/1'

# name -> (cost class, calibration evidence from the 2026-10-21 component inventory, what it proposes)
TIERS = {
    'chart_reader': ('free (grammar + SQL); System One only for open slots and note passages', 'single named patient: typed chart intents over every structured source + note frames; deterministic SQL; 1,080/1,080 oracle checks', 'graph'),
    'exact_local_plan': ('free', 'exact authored plans and recipes; exact normalized-question match', 'graph'),
    'scoped_review_compiler': ('free', 'exact source-review command grammar; abstains otherwise; uncalibrated', 'graph'),
    'specialist_families_early': ('free', 'DQ/VBC families certified, bounds 2.45% / 2.5%', 'graph'),
    'cohort_family_early': ('free (Haiku slot filler only when slots are missing)', 'certified 0.71, 752 cases / 0 errors', 'graph'),
    'undelivered_input': ('free', 'deterministic delivered-field check; declines only', 'decline'),
    'paraphrase_reuse': ('free (MiniLM); System One confirms the slot mapping of a reused spec', 'verified interpretation of a slot-identical rewording', 'typed'),
    'spec_path': ('grammar free; System One slot choices ~$0.0002; ONE Haiku spec call (~$0.01) + one repair; Sonnet only on the escalation rung (after every Haiku attempt failed); '
                  'System One ~$0.0002 per verified part', 'question spec filled three ways in parallel, compiled, literal-checked, decision-context-checked and '
                  'verified part by part by System One; uncalibrated', 'typed'),
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
    's1_metric_router': ('Jev/Perplexity ~$0.0002 per typed choice (one metric choice + up to 3 slot choices)', 'uncalibrated; exact-fit, shape, number-accounting and answer checks gate it; declines to the Claude metric router', 'graph'),
    's1_operator_router': ('Jev/Perplexity ~$0.0002 per typed choice (one engine choice + up to 3 parameter choices)', 'uncalibrated; validators and the literal check gate it; declines to the Claude operator router', 'graph'),
    'typed_decider': ('Perplexity, ~$0.000006/call', 'curated unfiltered recipes only, 0.95 threshold', 'graph'),
    'hosted_typed_interpreter': ('Haiku 5.5 plan + Haiku review; Sonnet 5.5 only on the escalation rung', '20-question dev set; compiler + quote binding + audit', 'typed'),
    'hosted_verified_sql': ('Haiku writer + second Haiku sample, Haiku judge + Perplexity decider; Sonnet only on the escalation rung', 'two-model agreement + oracles; uncalibrated', 'graph'),
    'semantic_analyst': ('Haiku tool loop + Haiku reconcile on disagreement; Sonnet only on the escalation rung', 'legacy single analyst; uncalibrated', 'graph'),
    'frontier_graph_planner': ('Haiku 5.5, Sonnet 5.5 on the escalation rung', 'full capability catalog; uncalibrated', 'graph'),
    'stack_planner_propose': ('Haiku 5.5, one call; Sonnet 5.5 on the escalation rung', 'older registry plan proposer; uncalibrated', 'graph'),
    'hybrid_analysts': ('3x Haiku 5.5 + vote, tie-break Haiku; Sonnet 5.5 on the escalation rung', '18/20 dev, 7/10 held-out, pilot 75/100', 'answer'),
    'local_compiler': ('free', 'FAILED calibration: held-out 19/49 wrong; proposes only', 'graph'),
    'metric_router': ('Haiku', 'FAILED calibration: held-out 13/13 wrong; proposes only', 'graph'),
    'operator_router': ('Haiku', 'FAILED calibration: held-out 19/19 wrong; proposes only', 'graph'),
    'local_free_sql': ('free (Qwen3-4B)', 'FAILED calibration: precision too low; proposes only', 'graph'),
    'legacy_cost_ladder': ('free locally; its paid rungs only when live', 'pre-2026-10-19 ladder + together semantics, last resort; uncalibrated', 'graph'),
    'backstop_agent': ('Sonnet (the escalation rung), one tool loop of up to 20 calls', 'final backstop analyst with SQL + document tools + the '
                       'conversation; quotes verified verbatim; accepted with checks as annotations (Mike 2026-10-25); uncalibrated', 'answer'),
}
# The cascade runs in six stages (CTO, 2026-10-23; Mike, 2026-10-23: the spec path is the main path); a tier never runs before an earlier
# stage's tiers, whatever the learned routers prefer (they reorder within a stage only). A: reuse and certified free tiers. SPEC: the question
# spec (docs/agere/QUESTION_SPEC.md) filled by the grammar, System One slot choices and one Haiku spec call in parallel, compiled, checked and
# verified part by part by System One. B: governed engines and metric routers by System One typed choice (~$0.0002). C: free deterministic
# planners (local compiler, the families). D: System One on record evidence (per patient). E: the Claude routers, then the FREE-FORM writers
# (FREE_FORM: Claude SQL / plan writers and the local Qwen tiers), last, marked free-form, each answer needing System One's confirmation.
# The learned and System One routers may reorder B-D for a question (e.g. chart evidence first) but never move anything ahead of A or the
# spec path, nor a Claude tier ahead of B-D. 'system_one_*' tiers -> B.
STAGES = (
    ('reuse_certified', ('exact_local_plan', 'chart_reader', 'paraphrase_reuse', 'semantic_cache', 'learned_verified_sql', 'template_decoding',
                         'specialist_families_early', 'cohort_family_early', 'scoped_review_compiler', 'undelivered_input')),
    ('system_one_choice', ('s1_metric_router', 's1_operator_router', 'typed_decider', 'terminology_mapping', 'hcc_suspecting')),
    ('free_planners', ('local_compiler', 'specialist_families_late', 'cohort_family_late')),
    # the spec path follows the System One choosers and the free planners (Mike 2026-10-24: System One before Claude); its Haiku spec call
    # was started in parallel when the grammar declined (spec_path.prefetch), so waiting for it here costs no time
    ('spec_path', ('spec_path',)),
    ('system_one_evidence', ('evidence_retrieval', 'evidence_review', 'atomic_system_one')),
    ('claude', ('metric_router', 'operator_router', 'hosted_verified_sql', 'hosted_typed_interpreter', 'local_normalizer', 'qwen_rewrite_compiler',
                'bottled_sql', 'qwen_typed_interpreter', 'local_free_sql',
                # the backstop (Sonnet tool agent: governed engines per sub-question, SQL, documents) before the Haiku analyst: dev3 build-half
                # insight 7/17 vs 3/17 (2026-10-25); the Haiku analyst still runs when it declines
                'backstop_agent', 'semantic_analyst', 'hybrid_analysts', 'frontier_graph_planner',
                'stack_planner_propose', 'legacy_cost_ladder')),
)
STAGE_OF = {t: i for i, (_, tiers) in enumerate(STAGES) for t in tiers}
STAGE_INDEX = {name: i for i, (name, _) in enumerate(STAGES)}
CHEAP_STAGES = frozenset({'reuse_certified', 'spec_path', 'system_one_choice'})  # the stages the costly (local-model, per-patient) tiers wait for
# Free-form writers: they write SQL or a plan with no verified spec behind it. They run last; their answers are marked free-form and ship only after
# System One confirms them (answer_checks.local_sql_confirmed).
FREE_FORM = frozenset({'hosted_verified_sql', 'semantic_analyst', 'hybrid_analysts', 'frontier_graph_planner', 'stack_planner_propose', 'legacy_cost_ladder',
                       'local_normalizer', 'qwen_rewrite_compiler', 'bottled_sql', 'qwen_typed_interpreter', 'local_free_sql'})
# Rejection reasons that mean "not yet confirmed", not "shown wrong" (answer_checks): the System One fallback may pick these.
FALLBACK_SOFT_REASONS = ("an uncalibrated free planner's answer is shipped only when", 'certificate:')


# A reworded question (phrasing.py) is tried on the free stages only; the question as typed then gets the whole cascade (shared_planner.compose)
import contextvars as _cv
FREE_ONLY = _cv.ContextVar('agere_free_stages_only', default=False)
FREE_STAGES = frozenset({'reuse_certified', 'system_one_choice', 'free_planners'})


def modality_check(answer, tool_calls):
    """-> (provenance, reason) for a backstop answer with no SQL to gate: 'quote_verified' (a document answer whose quotes were verified
    against their own records: the only claim check here), 'grounded' (every number it states appears in, or is a share of, tool results:
    provenance only), 'absence_searched' (a stated absence after at least one search or query: provenance only), else 'unverified'."""
    calls = [c for c in tool_calls if isinstance(c, dict) and not (isinstance(c.get('result'), dict) and c['result'].get('error'))]
    if answer.get('status') == 'not_available':
        return ('absence_searched', '') if calls else ('unverified', 'no document search or query stands behind the stated absence')
    if any(isinstance(s, dict) and s.get('record_id') and str(s.get('quote') or '').strip() for s in answer.get('sources') or []):
        return 'quote_verified', ''
    shown = json.dumps([c.get('result') for c in calls], default=str)
    nums = set(re.findall(r'\d+(?:\.\d+)?', shown.replace(',', '')))
    said = [n for n in re.findall(r'\d+(?:\.\d+)?', json.dumps([answer.get(k) for k in ('headline', 'value', 'count', 'facts')], default=str).replace(',', ''))
            if not re.fullmatch(r'(?:19|20)\d\d', n)]
    vals = sorted({float(n) for n in nums if float(n) != 0})[:300]

    def derived(n):  # a share or ratio the agent computed from two shown numbers (e.g. 97 of 490 -> 19.8)
        x = float(n)
        return any(abs(a / b * k - x) <= max(0.051, abs(x) * 0.001) for a in vals for b in vals if a <= b for k in (1, 100))
    if said and all(n in nums or str(float(n)) in nums or n.rstrip('0').rstrip('.') in nums or derived(n) for n in said):
        return 'grounded', ''
    if not said and calls:
        return 'grounded', ''
    return 'unverified', 'a stated number or claim is not found in any tool result and no verified quote supports it'


CERTIFIED_CHART = frozenset({'chart_reader', 'exact_local_plan', 'paraphrase_reuse', 'semantic_cache', 'learned_verified_sql', 'data_domain_check'})


def quoted(contract):
    answer = ((contract or {}).get('executed') or {}).get('answer') or {}
    return any(isinstance(s, dict) and s.get('record_id') and str(s.get('quote') or '').strip() for s in answer.get('sources') or [])


def unquoted_chart_answer(question, contract):
    """Does this executed answer to a one-patient question (a patient id in it) cite no quoted passage while it is a note / document answer
    (note_answer, yes_no, or note sources)?"""
    if not requirement_ledger.PATIENT_ID.search(question or ''): return False
    answer = ((contract or {}).get('executed') or {}).get('answer') or {}
    if answer.get('status') not in ('answered', 'partial'): return False
    sources = [s for s in answer.get('sources') or [] if isinstance(s, dict)]
    chartish = answer.get('answer_type') in ('note_answer', 'yes_no') or answer.get('note_sources') or any(
        str(s.get('table') or s.get('source') or '').endswith(('note', 'document', 'document_text')) for s in sources)
    quoted = any(s.get('record_id') and str(s.get('quote') or '').strip() for s in sources)
    return bool(chartish) and not quoted


def second_look_fast_path(stack, question, graph, result, *, population=None, started=None):
    """The deterministic fast paths (compositional grammar, request programs, population engines, the typed spec) answer before the
    cascade and never see its checks: System One judges their executed result against the question as asked, and a flagged answer goes to
    the backstop investigation exactly as Cascade.second_look does. -> a replacement shared-planner result, or None (the answer stands).
    Never for a free-stage sub-question (the backstop's own ask_agere), a one-patient question or a stack without Claude."""
    if FREE_ONLY.get() or not getattr(stack, 'allow_live', False) or not claude_available() or os.environ.get('AGERE_SECOND_LOOK', 'on') == 'off':
        return None
    if ((result or {}).get('answer') or {}).get('status') not in ('answered', 'partial'): return None
    c = Cascade(stack, request_plan.Catalog.load(stack.path), question, population=population)
    try:
        order = list(ORDER)
        found = ({'kind': 'checked_graph', 'graph': graph, 'executed': result}, {'interpreter': 'fast_path'})
        got, name = c.second_look(order, 'fast_path', found)
        if name != 'backstop_agent': return None
        contract, interpretation = got
        out = dict(contract['executed'])
        answer = dict(out.get('answer') or {})
        answer['limitations'] = list(dict.fromkeys([*(answer.get('limitations') or []),
            'Investigated by the backstop analyst after System One judged the first engine answer does not fully answer the question as asked.']))
        from .shared_planner import economics
        receipt = {'route': 'backstop_agent', 'second_look': getattr(c, 'second_look_hint', None), 'new_paid_api_calls': c.calls,
                   'known_api_estimate_usd': c.usd, 'api_cost_known': c.cost_known, 'proposers': c.log}
        return {**out, 'schema': 'shared-warehouse-planner/5', 'synthetic': True, 'question': question, 'route': 'shared_planner',
                'graph': contract['graph'], 'answer': answer, 'request_contract': {k: v for k, v in contract.items() if k != 'executed'},
                'planner_receipt': receipt, 'new_paid_api_calls': c.calls, 'planning_economics': economics(receipt),
                'elapsed_seconds': (time.perf_counter() - started) if started else None, 'clinical_execution_authorized': False}
    except Exception as exc:  # a failed review preserves the first answer, except Mike's explicit subscription stop
        if outage(exc) == 'rate_limit' and os.environ.get('AGERE_CLAUDE_TRANSPORT') == 'subscription': raise
        return None
    finally:
        c.close()


FAST_ONE_PATIENT = ('exact_local_plan', 'paraphrase_reuse', 'semantic_cache', 'learned_verified_sql', 'chart_reader', 'data_domain_check')


def backstop_limit():
    """Only an explicitly configured investigation deadline; accuracy takes precedence over an invented time quota."""
    raw = os.environ.get('AGERE_BACKSTOP_SECONDS', '').strip().lower()
    if raw in ('', 'off', 'none', 'inf', 'unlimited'): return None
    try: return max(0.0, float(raw))
    except ValueError: return None


def investigator_first_for_one_patient(order):
    """A permutation for a one-patient question: the backstop investigator runs right after the fast exact tiers and the chart reader,
    ahead of the passage retrieval / review / interpreter tiers whose one-patient answers it replaces anyway (matched diagnostic
    2026-10-26, confirmed by Codex: ~100 s saved per note question, same answer). Every tier still runs after it when it declines."""
    order = list(order)
    if 'backstop_agent' not in order: return tuple(order)
    order.remove('backstop_agent')
    at = max([order.index(t) + 1 for t in FAST_ONE_PATIENT if t in order] or [0])
    order.insert(at, 'backstop_agent')
    return tuple(order)


GENERALISTS = ('semantic_analyst', 'hybrid_analysts', 'frontier_graph_planner', 'stack_planner_propose', 'legacy_cost_ladder')


def backstop_before_generalists(order):
    """A permutation: the backstop agent runs before the free-form generalist analysts whatever the learned routers predicted (they
    were trained before it existed; dev3 build-half insight 7/17 for the backstop vs 3/17 for the Haiku analyst, 2026-10-25)."""
    order = list(order)
    if 'backstop_agent' not in order: return tuple(order)
    first = min((order.index(t) for t in GENERALISTS if t in order), default=None)
    if first is not None and order.index('backstop_agent') > first:
        order.remove('backstop_agent'); order.insert(first, 'backstop_agent')
    return tuple(order)


def stage_of(tier):
    if tier in STAGE_OF: return STAGE_OF[tier]
    return STAGE_INDEX['system_one_choice'] if tier.startswith(('system_one_', 's1_')) else len(STAGES) - 1


def cheap(tier):
    return STAGES[stage_of(tier)][0] in CHEAP_STAGES


def staged(order, routed=()):
    """Hard boundaries only: reuse/certified tiers first, then the spec path, Claude and the free-form writers last; the routers' order is kept
    inside, so a System One router may send a chart-evidence question to the evidence readers before the free planners (a stable sort: nothing
    dropped)."""
    last, spec, evidence = len(STAGES) - 1, STAGE_INDEX['spec_path'], STAGE_INDEX['system_one_evidence']
    # System One choosers and free planners, then the spec path, then System One on record evidence; a tier the System One question router
    # routed to (routed: e.g. the evidence readers for a chart-evidence question) keeps its routed place among the choosers and planners
    return tuple(sorted(order, key=lambda t: (0 if stage_of(t) == 0 else 4 if stage_of(t) == last else 1 if t in routed
                                              else 2 if stage_of(t) == spec else 3 if stage_of(t) == evidence else 1)))


ORDER = tuple(t for _, tiers in STAGES for t in tiers if t in TIERS) + staged(t for t in TIERS if t not in STAGE_OF)  # the stated order within each stage
PAID = {'typed_decider', 'hosted_typed_interpreter', 'hosted_verified_sql', 'frontier_graph_planner', 'hybrid_analysts',
        'metric_router', 'operator_router', 'semantic_analyst', 'stack_planner_propose', 'atomic_system_one', 's1_metric_router',
        's1_operator_router'}
# Tiers whose cost is minutes, not seconds: the free local compiler (when its plan already passes the literal check) is tried before them, and
# each tier may use only a share of the answer budget (a tier that runs out yields like a decline; the cascade continues).
HEAVY = ('atomic_system_one', 'evidence_retrieval', 'evidence_review', 'qwen_rewrite_compiler', 'qwen_typed_interpreter', 'bottled_sql')
TIER_SHARE = {'heavy': 0.25, 'other': 0.5}
# Local-model tiers and atomic System One: they run only after the cheap tiers (stages A-B, local_compiler, the free families, evidence readers)
# found nothing, in order of the learned router's value estimate for the question (static order without one); never dropped.
COSTLY = ('local_normalizer', 'qwen_rewrite_compiler', 'bottled_sql', 'qwen_typed_interpreter', 'local_free_sql', 'atomic_system_one')
# The local Qwen planners (no trained adapter; 5-75 s each). When Claude is available they wait until the quick Claude planners
# (Haiku/Sonnet, ~1-10 s) have had their turn: on 2026-10-23 they used 21 s of the 30 s budget proposing nothing and the Haiku
# planner never ran. They still run after those (and in background continuation), so nothing is removed.
LOCAL_QWEN = ('local_normalizer', 'qwen_rewrite_compiler', 'bottled_sql', 'qwen_typed_interpreter', 'local_free_sql')
QUICK_CLAUDE = ('metric_router', 'operator_router', 'hosted_verified_sql', 'hosted_typed_interpreter')
# The Haiku-first ladder (model_policy, Mike 2026-10-24). The Claude stage runs on the Haiku rung: the spec writer and the Haiku routers
# before any SQL writer. Only when every one of them has failed for the question does escalate() run the tiers that spent a Haiku attempt
# again on the Sonnet rung, in cascade order. A Sonnet call therefore never precedes another tier's Haiku attempt, and a tier that spent no
# Claude call (declined before asking, skipped, held) is not run again.
ESCALATABLE = ('spec_path', 'hosted_typed_interpreter', 'metric_router', 'operator_router', 'hosted_verified_sql', 'semantic_analyst',
               'hybrid_analysts', 'frontier_graph_planner', 'stack_planner_propose', 'legacy_cost_ladder')


def claude_available():
    return bool(os.environ.get('ANTHROPIC_API_KEY')) and os.environ.get('AGERE_CLAUDE', 'on') != 'off' \
        and os.environ.get('AGERE_QWEN_BEFORE_CLAUDE', 'off') != 'on'
# Tiers that run a local MLX model (in process or in the typed-worker subprocess). Questions run concurrently (no service-wide
# lock), but two local models on the GPU at once exhausted Metal memory and aborted the server: these tiers take turns.
LOCAL_MODEL_TIERS = frozenset({'local_normalizer', 'qwen_rewrite_compiler', 'bottled_sql', 'qwen_typed_interpreter', 'local_free_sql',
                               'evidence_retrieval', 'evidence_review'})
# cohort_family_early/late are not listed: they usually run no model, so they take LOCAL_MODEL_LOCK only around the bottled slot fill itself (bottled_cohort_hook)
from .foreground_priority import LOCAL_MODEL_LOCK  # a priority lock: a foreground local tier is served before a queued background one
FEEDBACK_READERS = ('hosted_typed_interpreter',)  # proposers whose prompt includes recorded corrections
TYPED_WITH_POPULATION = {'backstop_agent', 'spec_path', 'terminology_mapping', 'qwen_typed_interpreter', 'hosted_typed_interpreter', 'evidence_retrieval', 'evidence_review', 'atomic_system_one', 'hcc_suspecting'}  # the only tiers that honour a saved cohort

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


# Registered but off the default path (2026-10-24): held-out calibration 13/13 and 19/19 wrong, precision too low, and no accepted correct answer
# on the 100 dev questions; hosted_verified_sql: end-to-end dev run deb4a33, reached 6 times, 0 accepted, 103 s of answer budget, which left the
# semantic analyst unreached. AGERE_PROPOSER_ON=metric_router,... puts one back.
# cohort_family_late: two end-to-end dev runs (deb4a33, fb36f0a), 0 proposals, 36 s of budget. hybrid_analysts, bottled_sql, qwen_rewrite_compiler:
# 0 accepted answers in every end-to-end run (dev and the held-out milestone), 73 s / 0 s / 12 s of dev budget (Codex final review 2026-10-24).
# frontier_graph_planner, legacy_cost_ladder, stack_planner_propose: with the 120 s budget they ran on every dev2 refusal (ac9bf5a): reached 20 each,
# 0 accepted answers, ~800 s and most of the run's $3.75 between them.
OFF_DEFAULT = ('metric_router', 'operator_router', 'local_free_sql', 'hosted_verified_sql', 'cohort_family_late', 'hybrid_analysts', 'bottled_sql',
               'qwen_rewrite_compiler', 'frontier_graph_planner', 'legacy_cost_ladder', 'stack_planner_propose')
# The hosted typed interpreter accepted no answer end to end but stays on: it reads recorded corrections (FEEDBACK_READERS) and procedural-memory
# lessons, which a corrections-only switch would cut off (tried 2026-10-24: 20 correction/lesson tests regressed).


def enabled(name, stack):
    skip = {s.strip() for s in os.environ.get('AGERE_PROPOSER_SKIP', '').split(',') if s.strip()}
    if name in skip or os.environ.get('AGERE_PROPOSERS', 'on') == 'off': return False
    if name in OFF_DEFAULT and name not in {s.strip() for s in os.environ.get('AGERE_PROPOSER_ON', '').split(',')}: return False
    if name in PAID and not getattr(stack, 'allow_live', False): return False
    return True


def evidence_of(contract):
    """Executable text of a typed contract: compiled SQL, predicates, outputs and engine parameters, never quotes."""
    return executable_text({k: v for k, v in contract.items()
                            if k in ('query', 'membership_query', 'request', 'analysis', 'program', 'nodes', 'returns')})


class Cascade:
    foreground_wait = 0.0  # seconds a background continuation waited for foreground questions (foreground_priority)
    claude_models, escalated = None, None  # per-question ladder state (set in __init__; class defaults keep bare instances usable)
    started, deferred, background, budget = None, (), None, None  # per-question state is set in __init__ / run (class defaults keep bare instances usable)
    def __init__(self, stack, catalog, question, *, population=None, ledger=None, hosted_model=None, memory_identity=None):
        self.stack, self.catalog, self.question, self.population = stack, catalog, question, population
        self.hosted_model, self.memory_identity = hosted_model, memory_identity
        self._ledger, self._owned = ledger, False
        self._ladder = None
        self.log, self.usd, self.calls, self.cost_known = [], 0.0, 0, True
        self.held_decline = None
        self.provider_error = None
        self.claude_models = {}  # tier -> models of its Claude requests on the Haiku rung (the escalation rung runs only tiers listed here)
        self.escalated = []  # [{'tier', 'from', 'outcome'}] tiers re-run on the Sonnet rung
        self.claude_down = None  # outage kind once a Claude tier failed on the provider: Claude tiers are skipped after that
        self.candidates = []  # executed proposals a check rejected: the System One fallback chooses among them on an outage
        self.routing = None
        self.learned_routing = None
        self.measured = {}  # tier -> seconds measured this question (stored as the tier's running median when the question ends)
        self.deferred = []  # tiers not run because the answer budget was short: [{'tier', 'expected_seconds', 'reason'}]
        self.background = None
        self.started = time.perf_counter()

    # -- shared resources -------------------------------------------------------------------------------------------
    @property
    def ledger(self):
        if self._ledger is None:
            from ..gateway import BudgetLedger, default_ledger_path
            self._ledger, self._owned = BudgetLedger(default_ledger_path()), True
        return self._ledger

    def close(self):
        try:  # the question is settled: early spec fillers started for it stop at their next step (spec_fill.cancel_early)
            from .spec_fill import cancel_early
            cancel_early(self.stack, self.question, self.population)
        except Exception:  # noqa: BLE001 - cleanup only
            pass
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
        routed = ()
        if (self.routing or {}).get('applied'):
            from .system_one_gate import ROUTES
            routed = tuple(t for t in ROUTES.get(self.routing.get('label'), ()) if stage_of(t) != len(STAGES) - 1)
        order = backstop_before_generalists(self.heavy_last(self.free_first(staged(order, routed))))
        return investigator_first_for_one_patient(order) if self.one_patient() else order

    def heavy_last(self, order):
        """A permutation: the costly tiers (COSTLY) run only after the cheap ones (stages A-B and local_compiler), and among themselves
        by the learned router's predicted success for this question within their stage (static order without a prediction)."""
        order = list(order)
        costly = [t for t in order if t in COSTLY]
        if not costly: return tuple(order)
        anchors = [i for i, t in enumerate(order) if t not in COSTLY and (t == 'local_compiler' or cheap(t))]
        if anchors:
            after = max(anchors)
            early = [t for i, t in enumerate(order) if t in COSTLY and i < after]  # a route asked for a local model first: it waits
            if early:
                order = [t for t in order if t not in early]
                at = max(i for i, t in enumerate(order) if t == 'local_compiler' or (t not in COSTLY and cheap(t))) + 1
                order[at:at] = early
        learned = self.learned_routing or {}
        predicted, movable = learned.get('predicted') or {}, set(learned.get('movable') or ())  # base rates alone never reorder the cascade
        by_stage = {}
        for i, t in enumerate(order):
            if t in COSTLY and t in movable: by_stage.setdefault(stage_of(t), []).append(i)
        for slots in by_stage.values():
            members = sorted((order[i] for i in slots), key=lambda t: (-predicted.get(t, 0.0), order.index(t)))
            for i, t in zip(slots, members): order[i] = t
        return tuple(self.analyst_first(self.qwen_after_quick_claude(order)))

    def analyst_first(self, order):
        """A permutation (AGERE_ANALYST_FIRST=on): the semantic analyst (one Claude tool loop over the documented semantic layer,
        3-13 s) leads the Claude stage, so a population question reaches it inside the answer budget; it ran last and was
        almost never reached (2026-10-23). Its answers stay free-form and need System One confirmation."""
        order = list(order)
        # on by default since the end-to-end dev run deb4a33 (2026-10-24): behind the other Claude tiers it was reached 0 times in 100 questions
        if os.environ.get('AGERE_ANALYST_FIRST', 'on') != 'on' or 'semantic_analyst' not in order or not claude_available(): return order
        if getattr(self, 'corrected', False): return order  # a recorded correction: its one reader (the hosted interpreter) keeps the lead
        claude = [i for i, t in enumerate(order) if STAGES[stage_of(t)][0] == 'claude']
        if not claude: return order
        order.remove('semantic_analyst')
        at = min(i for i, t in enumerate(order) if STAGES[stage_of(t)][0] == 'claude')
        return order[:at] + ['semantic_analyst'] + order[at:]

    def qwen_after_quick_claude(self, order):
        """A permutation: with Claude available, the local Qwen planners follow the quick Claude planners (QUICK_CLAUDE)."""
        order = list(order)
        quick = [i for i, t in enumerate(order) if t in QUICK_CLAUDE]
        if not quick or not claude_available(): return order
        local = [t for t in order if t in LOCAL_QWEN and order.index(t) < max(quick)]
        if not local: return order
        rest = [t for t in order if t not in local]
        at = max(i for i, t in enumerate(rest) if t in QUICK_CLAUDE) + 1
        return rest[:at] + local + rest[at:]

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
        """This tier's time in seconds: its share cap (a fraction of max(budget, 120 s)) bounded by what the budget has left;
        None when the budget is unlimited."""
        if budget.seconds == float('inf'): return None
        if name == 'backstop_agent':  # the final layer gets its own time, not a share of what the earlier layers left
            return backstop_limit()
        from .answer_budget import SHARE_BASE_SECONDS
        cap = max(budget.seconds, SHARE_BASE_SECONDS) * TIER_SHARE['heavy' if name in HEAVY or name in PAID else 'other']
        return max(0.5, min(cap, budget.remaining()))

    def expected(self, name):
        """Seconds this tier is expected to take on this stack (running median of its measured seconds; static defaults to start)."""
        from .tier_timing import estimate
        return estimate(getattr(self.stack, 'path', None), name)

    def call_tier(self, name, limit):
        """Run one tier on the cascade's own thread with a deadline (no concurrent tiers: in-process models and stack writes stay
        serial). The tier's loops and model calls stop at the deadline (answer_budget.cancelled / tier_remaining); a tier that
        reaches it without a proposal yields like a decline (QuestionNotUnderstood) and the cascade continues."""
        fn = getattr(self, 'tier_' + name)
        try:
            if name in LOCAL_MODEL_TIERS:
                with LOCAL_MODEL_LOCK:  # one local model on the GPU at a time across concurrent questions
                    return self._timed(fn, name, limit)
            return self._timed(fn, name, limit)
        finally:
            mx = sys.modules.get('mlx.core')
            if mx is not None: mx.clear_cache()  # freed GPU buffers go back to the system before the next tier loads a model

    def _timed(self, fn, name, limit):
        from ..request_lineage import component
        t0 = time.perf_counter()  # measured after any local-model lock wait: the tier's own seconds feed its running median
        try:
            with component('proposer_tier', tier=name) as metrics:
                result = self._call_tier(fn, name, limit)
                metrics['outcome'] = 'declined' if result is None else 'proposal_or_held'
                return result
        finally:
            self.measured.setdefault(name, []).append(time.perf_counter() - t0)

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

    def runnable(self, name):
        """Would this tier be asked at all (saved cohort, switches, live-only paid tiers)? Only those can be deferred."""
        from .acceptance_contract import skipped  # a tier whose proposal the acceptance contract rejected for this question is not asked again
        return not (self.population is not None and name not in TYPED_WITH_POPULATION) and enabled(name, self.stack) and not skipped(name)

    def attempt(self, name, limit):
        """Ask one tier (time-limited), log what it did and absorb its failure. -> the proposal (contract, interpretation) or None."""
        started = time.perf_counter()
        if self.claude_down and name in CLAUDE_TIERS:  # one outage per question: no repeated failing Claude calls
            self.note(name, 'skipped', f'Claude unavailable ({self.claude_down}) earlier in this question', started); return None
        try:
            with model_policy.tracking() as requested:
                try:
                    found = self.call_tier(name, limit)
                finally:
                    if not model_policy.escalating() and requested:
                        if self.claude_models is None: self.claude_models = {}
                        self.claude_models.setdefault(name, []).extend(requested)
        except QuestionNotUnderstood as exc:
            self.note(name, 'declined', str(exc), started)
            cause = exc.__cause__ or exc.__context__  # a decline wrapping a provider outage still means Claude is down
            if name in CLAUDE_TIERS and cause is not None and outage(cause) == 'rate_limit' and os.environ.get('AGERE_CLAUDE_TRANSPORT') == 'subscription': raise cause
            if name in CLAUDE_TIERS and cause is not None and outage(cause): self.claude_down = self.claude_down or outage(cause)
            return None
        except Exception as exc:  # noqa: BLE001 - one component failing never stops the others (heal, don't block)
            if name in CLAUDE_TIERS and outage(exc) == 'rate_limit' and os.environ.get('AGERE_CLAUDE_TRANSPORT') == 'subscription':
                raise  # Mike: a subscription limit ends the request; never try another Claude tier or the external API
            self.note(name, 'error', f'{type(exc).__name__}: {exc}', started)
            from ..fault_containment import on_tier_error  # a malformed state store is quarantined and recreated, not hit again
            healed = on_tier_error(getattr(self.stack, 'path', '.'), exc)
            if healed: self.log[-1]['healed_stores'] = healed
            if name in PAID and (outage(exc) or (isinstance(exc, RuntimeError) and not isinstance(exc, (ValueError, KeyError, TypeError, AttributeError, sqlite3.Error)))):
                self.provider_error = self.provider_error or exc
                if name in CLAUDE_TIERS: self.claude_down = self.claude_down or outage(exc)  # only a real provider outage (billing, rate, overload)
            return None
        if found is None:
            self.note(name, 'no proposal', None, started); return None
        if found == 'held':
            self.note(name, 'decline held', None, started); return None
        self.note(name, 'accepted', None, started)
        return found

    def accept(self, order, name, found):
        """A proposal passed every check: an independent proposer verifies it unless certified / confirmed by System One. A backstop
        answer whose quotes were verified against their own records is not re-verified by the weaker generalist tiers (matched
        diagnostic 2026-10-26: ~50 s of verifiers on one-patient note answers, none comparable)."""
        a = ((found[0] or {}).get('executed') or {}).get('answer') or {} if isinstance(found, tuple) else {}
        if name == 'backstop_agent':  # its continuing investigation owns repair; another analyst cannot repair a failed review service
            contract, interpretation = found
        else:
            contract, interpretation = claim_verification.verify(self, order, name, found)
        name = interpretation.get('verified_proposer') or name
        interpretation = {**interpretation, 'requirement_ledger': requirement_ledger.summary(
            requirement_ledger.check(self.question, self.evidence(contract)))}
        return contract, self.finish(interpretation, name)

    def defer(self, name, reason, expected=None):
        """Not run for lack of budget. Never dropped: listed in the receipt and continued in the background."""
        entry = {'tier': name, 'expected_seconds': round(self.expected(name) if expected is None else expected, 2), 'reason': reason}
        self.deferred.append(entry)
        if getattr(self, 'budget', None) is not None: self.budget.skip(name)
        self.note(name, 'skipped', f"skipped: budget ({reason}; expected ~{entry['expected_seconds']:g}s)", time.perf_counter())
        return entry

    def timing(self):
        """Receipt: total seconds, per-tier seconds, tiers skipped for budget, deferred tiers, background continuation."""
        spent = {}
        for e in self.log:
            if e.get('seconds') is not None and e['outcome'] != 'skipped': spent[e['proposer']] = round(spent.get(e['proposer'], 0.0) + e['seconds'], 3)
        budget = getattr(self, 'budget', None)
        return {'schema': 'answer-timing/1', 'total_seconds': round(time.perf_counter() - self.started, 3) if self.started else 0.0,
                'budget_seconds': None if budget is None or budget.seconds == float('inf') else budget.seconds,
                'tier_seconds': spent, 'skipped_for_budget': [d['tier'] for d in self.deferred], 'deferred_tiers': list(self.deferred),
                'background_continuation': self.background}

    def flush_timings(self):
        """Store this question's measured tier seconds in the stack's running medians (bookkeeping; never raises)."""
        try:
            from .tier_timing import record
            record(getattr(self.stack, 'path', None), self.measured)
        except Exception:  # noqa: BLE001
            pass
        self.measured = {}

    def run(self):
        from .foreground_priority import foreground
        with foreground(), model_policy.question_path():  # the background worker starts no tier while this question is in flight
            return self._run()

    def _run(self):
        from .answer_budget import start as start_budget
        # a rewording's free-stage attempt gets a short budget of its own: the question as typed then runs with the full answer budget
        budget = self.budget = start_budget(float(os.environ.get('AGERE_REWORDING_BUDGET_SECONDS', '8')) if FREE_ONLY.get() else None)
        try:
            order = self.order_used = self.order()
            free_only = FREE_ONLY.get()
            if free_only: order = self.order_used = [t for t in order if STAGES[stage_of(t)][0] in FREE_STAGES]
            for i, name in enumerate(order):
                if not self.runnable(name): continue
                if budget.expired():  # out of time: everything that would have run next is deferred, then the honest early reply
                    for later in order[i:]:
                        if self.runnable(later) and not (self.claude_down and later in CLAUDE_TIERS): self.defer(later, 'answer budget used up')
                    break
                est = self.expected(name)
                if not budget.allows(est) and name != 'backstop_agent':
                    self.defer(name, f'{budget.remaining():.0f}s left', est); continue
                found = self.attempt(name, self.tier_seconds(name, budget))
                if found is None: continue
                found, name = self.quoted_or_backstop(order, name, found)
                found, name = self.second_look(order, name, found)
                return self.accept(order, name, found)
            if free_only:
                raise QuestionNotUnderstood('The rewording was not read by the free stages; the question as typed is tried next.')
            # reserved slots, strongest first (dev3 build-half insight: backstop 7/17, Haiku analyst 3/17)
            found = self.reserved_backstop(order)  # the final backstop always gets its turn before the question is declined
            if found is not None: return found
            found = self.reserved_analyst(order)  # the analyst never got its turn inside the budget: it gets a reserved slot of its own
            if found is not None: return found
            found = self.escalate(order, budget)  # every Haiku attempt failed: the Sonnet rung, tier by tier
            if found is not None: return found
            if budget.best() and (budget.expired() or self.deferred):
                contract, interpretation = budget.best()  # time ran out: the best executed candidate, marked partial
                self.queue_background()
                return contract, budget.partial(self.finish(interpretation, interpretation['interpreter']))
            if self.held_decline and not self.placeholder(self.held_decline[0]):
                contract, interpretation = self.held_decline
                self.queue_background()
                return contract, self.finish(interpretation, interpretation['interpreter'])
            if self.provider_error is not None:
                self.queue_background()
                found = self.system_one_fallback()  # Claude out: System One picks among the executed candidates (partial)
                if found: return found
                # A provider outage is reported as an outage (unverified), never as a question nobody could read.
                self.provider_error.planner_receipt = {**self.usage(), 'proposers': self.log, 'answer_timing': self.timing(),
                                                       'stage_diagnostics': stage_diagnostics.attribute(self.log, error=self.provider_error)}
                raise self.provider_error
            reasons = [f"{e['proposer']}: {e['detail']}" for e in self.log if e['outcome'] == 'rejected']
            exc = QuestionNotUnderstood(
                'The original question was preserved; no proposer produced a plan that keeps all of it, so nothing was '
                'executed.' + (' Closest proposals and what they dropped: ' + ' | '.join(reasons[:4]) if reasons else ''))
            self.queue_background()
            exc.planner_receipt = self.usage()
            exc.planner_receipt['answer_timing'] = self.timing()
            exc.planner_receipt['deferred_tiers'] = list(self.deferred)
            exc.planner_receipt['proposers'] = self.log
            exc.planner_receipt['stage_diagnostics'] = stage_diagnostics.attribute(self.log)  # which stage failed
            raise exc
        finally:
            self.flush_timings()
            self.close()

    def quoted_or_backstop(self, order, name, found):
        """One owner for a one-patient chart investigation (audit 2026-10-25: retrieval answered "2 passages.", a pattern reader bound a status
        to the wrong medication line, a yes/no question became a count). Unless the certified chart reader answered with a quoted passage,
        the backstop investigates: it lists the patient's documents in scope, has System One judge each whole document, and quotes the
        sentence. Its answer replaces the first when it answers; a decline replaces only an unquoted first answer. -> (found, name)."""
        if name == 'backstop_agent' or not isinstance(found, tuple) or not self.one_patient(): return found, name
        if name in CERTIFIED_CHART and not unquoted_chart_answer(self.question, found[0]) and not self.system_one_disagrees(found[0]): return found, name
        if 'backstop_agent' not in order or not self.runnable('backstop_agent') or not claude_available() or self.claude_down: return found, name
        if any(e.get('proposer') == 'backstop_agent' and e.get('outcome') not in ('skipped', 'deferred') for e in self.log): return found, name
        second = self.attempt('backstop_agent', backstop_limit())
        if not isinstance(second, tuple): return found, name
        status = ((second[0] or {}).get('executed') or {}).get('answer', {}).get('status')
        if status in ('answered', 'partial') or unquoted_chart_answer(self.question, found[0]) or not quoted(found[0]): return second, 'backstop_agent'
        return found, name

    def system_one_disagrees(self, contract):
        """A yes / no chart answer: System One (Jev, then Perplexity) reads the cited documents whole and answers the question itself; a
        confident opposite label (p >= 0.8) sends the question to the backstop investigation (MGB CS1: the classifier reads each document)."""
        answer = ((contract or {}).get('executed') or {}).get('answer') or {}
        said = answer.get('yes_no', answer.get('value')) if answer.get('answer_type') == 'yes_no' else None
        if said is None:
            m = re.match(r'\s*(yes|no)\b', str(answer.get('headline') or ''), re.I)
            said = None if not m else m.group(1).lower() == 'yes'
        if not isinstance(said, bool) or os.environ.get('AGERE_CHART_S1_CHECK', 'on') == 'off': return False
        try:
            from . import system_one, note_evidence
            db = note_evidence.connect(self.stack)
            evidence = []
            for src in (answer.get('sources') or [])[:4]:
                rid = src.get('record_id') if isinstance(src, dict) else None
                row = db.execute("SELECT json_extract(payload,'$.text') FROM record WHERE record_id=?", (rid,)).fetchone() if rid else None
                text = (row[0] if row else None) or src.get('quote') or ''
                evidence += [{'id': f'{rid}#{k}', 'record_id': rid, 'text': text[i:i + 1800], 'date': src.get('date')}
                             for k, i in enumerate(range(0, max(len(text), 1), 1600))]
            if not evidence: return False
            r = system_one.decide(self.question, {'yes': 'yes, the documents say so', 'no': 'no, the documents say otherwise'}, evidence,
                                  as_of=self.catalog.identity['as_of'], task_key='agere_chart_check', ledger=self.ledger,
                                  allow_live=bool(getattr(self.stack, 'allow_live', False)))
            p = (r.get('calibrated_probabilities') or {}).get(r.get('label'))
            disagree = r.get('label') in ('yes', 'no') and (r['label'] == 'yes') != said and p is not None and p >= 0.8
            self.note('system_one_chart_check', 'disagrees' if disagree else 'agrees or unsure', f"{r.get('label')} p={p}", time.perf_counter())
            return disagree
        except Exception:  # noqa: BLE001 - the check is a second reading; no reading leaves the answer as it is
            return False

    def second_look(self, order, name, found):
        """A population answer from any proposer but a certified reuse: System One judges whether what was computed answers the question as
        asked (system_one_gate.check: the same typed question the answer checks ask, run here also for the spec-confirmed engines that skip
        it). 'answers partially' or 'a different question' (p >= AGERE_SECOND_LOOK_P, default 0.6) hands the question, with that answer and
        the verdict, to the backstop investigation; its answer replaces the first when it answers (audit 2026-10-25: confident wrong engine
        answers were accepted with no second look). -> (found, name)."""
        if name == 'backstop_agent' or not isinstance(found, tuple): return found, name
        if os.environ.get('AGERE_SECOND_LOOK', 'on') == 'off': return found, name
        if 'backstop_agent' not in order or not self.runnable('backstop_agent') or not claude_available() or self.claude_down: return found, name
        if any(e.get('proposer') == 'backstop_agent' and e.get('outcome') not in ('skipped', 'deferred') for e in self.log): return found, name
        contract = found[0] or {}
        result = contract.get('executed') or {}
        if (result.get('answer') or {}).get('status') not in ('answered', 'partial'): return found, name
        s1 = (result.get('answer_checks') or {}).get('system_one')
        try:
            if not s1:
                from .system_one_gate import check, ready
                if not ready(self.stack, 'AGERE_S1_CHECK'): return found, name
                s1 = check(self.question, contract.get('graph') or {}, result, stack=self.stack, ledger=self.ledger)
                self.spend(s1.get('calls', 0), s1.get('cost_usd', 0.0))
        except Exception:  # noqa: BLE001 - no reading: the answer stands
            return found, name
        label, p = s1.get('label'), float(s1.get('p') or 0.0)
        flagged = label in ('answers_partially', 'answers_a_different_question') and p >= float(os.environ.get('AGERE_SECOND_LOOK_P', '0.6'))
        self.note('second_look', 'investigate' if flagged else 'stands', f'System One: {label} p={p:.2f} on {name}', time.perf_counter())
        if not flagged: return found, name
        a = result.get('answer') or {}
        self.second_look_hint = {'earlier_answer': {'by': name, 'answer': a, 'graph': contract.get('graph')},
                                 'system_one_verdict': f"{label.replace('_', ' ')} (p={p:.2f})"}
        second = self.attempt('backstop_agent', backstop_limit())
        if isinstance(second, tuple) and ((second[0] or {}).get('executed') or {}).get('answer', {}).get('status') in ('answered', 'partial'):
            return second, 'backstop_agent'
        return found, name

    def one_patient(self):
        """Is this question about one patient: an id in it, or a follow-up of a one-patient turn with no population wording?"""
        if self.population is not None: return False
        if requirement_ledger.PATIENT_ID.search(self.question or ''): return True
        try:
            from . import patient_ids
            from .decision_context import _COHORT_WORDS
            return bool((patient_ids.current_prior() or {}).get('patient_id')) and not _COHORT_WORDS.search(self.question or '')
        except Exception:  # noqa: BLE001 - no conversation
            return False

    def reserved_backstop(self, order):
        """The final LLM backstop runs once before any decline (AGERE_BACKSTOP_SECONDS, default 90; 0 turns this off), when the budget ran out
        before it was asked. -> accept() result or None."""
        name, seconds = 'backstop_agent', backstop_limit()
        if (seconds is not None and seconds <= 0) or name not in order or not self.runnable(name) or not claude_available() or self.claude_down: return None
        if any(e.get('proposer') == name and e.get('outcome') not in ('skipped', 'deferred') for e in self.log): return None
        self.deferred = [d for d in self.deferred if d.get('tier') != name]
        found = self.attempt(name, seconds)
        return self.accept(order, name, found) if found is not None else None

    def tier_backstop_agent(self):
        """The final backstop analyst (backstop_agent.py) on the escalation rung (Sonnet): every Haiku tier before it declined. Its answer is
        accepted with the checks as annotations (heal, don't block): it is the last layer, and its document quotes are verified verbatim."""
        if os.environ.get('AGERE_BACKSTOP', 'on') == 'off' or not getattr(self.stack, 'allow_live', False) or not claude_available(): return None
        from . import backstop_agent
        from .semantic_analyst import successful_sql_calls
        checked_in_loop = False
        final_gate, final_binding = None, None
        def validate_final(answer, calls):
            nonlocal checked_in_loop, final_gate, final_binding
            sqls = [c['sql'] for c in successful_sql_calls(calls)]
            if not sqls or answer.get('status') == 'unsupported': return None
            graph = {'interpretation': self.question, 'coverage': 'supported_observation', 'unmet_requirements': [],
                     'nodes': [{'id': f'backstop_{i}', 'operation': 'sql', 'inputs': [], 'parameters': {'query': sql, 'returns': 'table'},
                                'purpose': 'Investigator query (executed)'} for i, sql in enumerate(sqls)]}
            from .answer_checks import investigation_binding
            receipt = {'route': 'backstop_agent', 'investigation_review': answer.get('investigation_review'),
                       'investigation_binding': investigation_binding(self.question, graph, answer)}
            checked = answer_gate(self, 'backstop_agent', graph, receipt, {'answer': answer, 'results': [], 'new_paid_api_calls': 0})
            checked_in_loop = checked is not None
            if checked is not None:
                final_gate, final_binding = checked.get('answer_checks'), receipt['investigation_binding']
                answer.update(checked['answer'])
                return None
            reasons = next((e['detail'] for e in reversed(self.log) if e['proposer'] == 'backstop_agent' and e['outcome'] == 'rejected'), 'final answer checks did not pass')
            return 'Final answer checks: ' + str(reasons) + '. Repair this in the current investigation using the existing tool results.'
        with model_policy.escalation():
            run = backstop_agent.answer(self.stack, self.question, ledger=self.ledger, population=self.population,
                                        hint=getattr(self, 'second_look_hint', None), validate_final=validate_final)
        self.spend(run.get('new_paid_api_calls', 0), run.get('known_cost_usd') or run.get('estimated_cost_usd') or 0.0, run.get('estimated_cost_usd') is not None)
        if run.get('error') and outage(RuntimeError(run['error'])) == 'rate_limit' and os.environ.get('AGERE_CLAUDE_TRANSPORT') == 'subscription':
            from ..gateway import ProviderUnavailable
            raise ProviderUnavailable(run['error'], kind='rate_limit')
        answer = run.get('answer')
        if not isinstance(answer, dict) or answer.get('status') not in ('answered', 'partial', 'unsupported'):
            return self.reject('backstop_agent', [str(run.get('error') or 'no answer')[:300]])
        if answer.get('answer_type') == 'note': answer['answer_type'] = 'note_answer'
        if answer.get('status') == 'unsupported':
            answer = {**answer, 'status': 'not_available', 'answer_type': 'unsupported', 'data_absence_established': False}
        sqls = [c['sql'] for c in successful_sql_calls(run.get('tool_calls') or [])]
        graph = {'interpretation': self.question, 'coverage': 'supported_observation', 'unmet_requirements': [],
                 'nodes': [{'id': f'backstop_{i}', 'operation': 'sql', 'inputs': [], 'parameters': {'query': q, 'returns': 'table'},
                            'purpose': 'Investigator query (executed)'} for i, q in enumerate(sqls)]}
        receipt = {'route': 'backstop_agent', 'model': run.get('model'), 'new_paid_api_calls': run.get('new_paid_api_calls', 0),
                   'backstop': {'version': run.get('version'), 'tool_calls': len(run.get('tool_calls') or []), 'seconds': run.get('seconds'),
                                'answer_reviews': run.get('answer_reviews'), 'review_stopped': run.get('review_stopped')}}
        notes = []
        try:  # the checks still run and are recorded, as annotations (never a veto at the last layer)
            misfit = shape_misfit(self.question, answer)
            if misfit: notes.append('shape: ' + str(misfit)[:200])
        except Exception:  # noqa: BLE001
            pass
        if notes: answer = {**answer, 'limitations': [*(answer.get('limitations') or []), *('Check note: ' + n for n in notes)]}
        answer.setdefault('patient_ids', answer.get('patient_ids') or [])
        result = {'answer': answer, 'results': [], 'new_paid_api_calls': 0,
                  'workflow':[{'index':i,**s,'status':'planned'} for i,s in enumerate(answer.get('workflow') or [])],
                  'investigation': {'model': run.get('model'), 'tool_calls': run.get('tool_calls') or [],
                                    'answer_reviews': run.get('answer_reviews') or [],
                                    'document_reviews':run.get('document_reviews') or [],
                                    'guarded_continuations':run.get('guarded_continuations') or [],
                                    'recursive_improvement':run.get('recursive_improvement')}}
        review = answer.get('investigation_review') or {}
        if review and review.get('status') != 'confirmed':
            answer = {**answer, 'verification': 'unverified', 'status': 'partial' if answer.get('status') == 'answered' else answer.get('status'),
                      'limitations': [*(answer.get('limitations') or []), 'The final investigation review did not confirm the complete answer: ' +
                                      str(review.get('feedback') or review.get('reason') or review.get('status'))]}
            return self.contract_of('backstop_agent', graph, receipt, {**result, 'answer': answer})
        if checked_in_loop:
            answer['verification'] = 'checked'
            receipt.update(answer_checks=final_gate, investigation_binding=final_binding)
            return self.contract_of('backstop_agent', graph, receipt, {**result, 'answer': answer, 'answer_checks': final_gate})
        if answer.get('status') == 'not_available' or not graph['nodes']:
            # no SQL to gate (documents, governed metrics, an absence): the modality's own check (Codex review 2026-10-26). A document answer
            # needs a verified quote; a numeric answer's numbers must appear in what the tools returned; an absence needs a search behind it.
            # A failed check ships the answer unverified and partial, never as a checked result.
            # Only a verified quote is a claim check. Numbers found in tool results and a search behind an absence are provenance, not proof
            # of the requested meaning (Codex review 2026-10-26: a list_metrics call does not establish absent mortality data; a ratio of
            # unrelated numbers is not a verified denominator), so those ship unverified and partial with the provenance recorded.
            provenance, why = modality_check(answer, run.get('tool_calls') or [])
            answer = {**answer, 'provenance': provenance, 'verification': 'system_one_reviewed' if review.get('status') == 'confirmed' else
                      'quote_verified' if provenance == 'quote_verified' else 'unverified'}
            if answer['verification'] == 'unverified':
                answer = {**answer, 'status': 'partial' if answer.get('status') == 'answered' else answer.get('status'),
                          'limitations': [*(answer.get('limitations') or []), 'Unverified: ' + (why or
                              f'no claim check for this answer type; provenance: {provenance.replace("_", " ")}')]}
            return self.contract_of('backstop_agent', graph, receipt, {**result, 'answer': answer})
        # the same answer gate as every other proposal (Codex review 2026-10-26: the last layer bypassed it). A failure goes back to the agent
        # once with the reasons; if the repair also fails, the answer ships marked unverified and partial, never as a checked answer.
        checked = answer_gate(self, 'backstop_agent', graph, receipt, result)
        if checked is not None:
            checked['answer'] = {**checked['answer'], 'verification': 'checked'}
            return self.contract_of('backstop_agent', graph, receipt, checked)
        reasons = next((e['detail'] for e in reversed(self.log) if e['proposer'] == 'backstop_agent' and e['outcome'] == 'rejected'), None)
        if not getattr(self, 'backstop_repaired', False):
            self.backstop_repaired = True
            self.second_look_hint = {**(getattr(self, 'second_look_hint', None) or {}), 'check_failures': reasons,
                                     'instruction': 'Your previous answer failed these checks; fix what they name and answer again.'}
            again = self.tier_backstop_agent()
            if isinstance(again, tuple): return again
        answer = {**answer, 'verification': 'unverified', 'status': 'partial' if answer.get('status') == 'answered' else answer.get('status'),
                  'limitations': [*(answer.get('limitations') or []), f'Unverified: the answer checks did not pass ({str(reasons)[:200]}).']}
        return self.contract_of('backstop_agent', graph, receipt, {**result, 'answer': answer})

    def reserved_analyst(self, order):
        """The LLM layer always gets a turn: when the answer budget ran out before the semantic analyst was asked (end-to-end held-out
        run f2cbeb0: 15 of 27 refusals never reached it), it runs once now with its own time (AGERE_ANALYST_RESERVE_SECONDS, default 25;
        0 turns this off). -> accept() result or None."""
        name, seconds = 'semantic_analyst', float(os.environ.get('AGERE_ANALYST_RESERVE_SECONDS', '25'))
        if seconds <= 0 or name not in order or not self.runnable(name) or not claude_available() or self.claude_down: return None
        if any(e.get('proposer') == name and e.get('outcome') not in ('skipped', 'deferred') for e in self.log): return None  # it had its turn
        self.deferred = [d for d in self.deferred if d.get('tier') != name]
        found = self.attempt(name, seconds)
        return self.accept(order, name, found) if found is not None else None

    def escalatable(self, name):
        """Did this tier spend a Haiku attempt that failed (declined, rejected, errored) and is Claude still reachable?"""
        if name not in ESCALATABLE or not (self.claude_models or {}).get(name) or self.claude_down: return False
        last = next((e for e in reversed(self.log) if e['proposer'] == name and e.get('rung') != 'escalation'), None)
        return last is not None and last['outcome'] in ('no proposal', 'declined', 'rejected', 'error') and self.runnable(name)

    def escalate(self, order, budget):
        """The ladder's second rung. Runs only after the whole cascade (every Haiku attempt of every tier) found nothing: each tier that spent a
        Haiku attempt is run again on Sonnet (model_policy.escalation), in cascade order, inside the answer budget. -> accept() result or None."""
        if not model_policy.escalation_enabled() or self.claude_down: return None
        for name in order:
            if not self.escalatable(name): continue
            if budget.expired():
                self.note(name, 'skipped', 'escalation to Sonnet skipped: answer budget used up', time.perf_counter()); break
            est = self.expected(name)
            if not budget.allows(est):
                self.note(name, 'skipped', f'escalation to Sonnet skipped: {budget.remaining():.0f}s left (expected ~{est:g}s)', time.perf_counter()); continue
            with model_policy.escalation():
                found = self.attempt(name, self.tier_seconds(name, budget))
            if self.escalated is None: self.escalated = []
            self.escalated.append({'tier': name, 'from': sorted(set(self.claude_models.get(name) or ())),
                                   'outcome': 'accepted' if found is not None else self.log[-1]['outcome'] if self.log else None})
            if found is not None: return self.accept(order, name, found)
        return None

    def ladder_receipt(self):
        """Which models each tier's Haiku-rung requests used, and which tiers were re-run on Sonnet (model_policy ladder)."""
        return {'ladder': model_policy.ladder('planner'), 'first_rung_models': {k: sorted(set(v)) for k, v in (self.claude_models or {}).items()},
                'escalated': list(self.escalated or [])}

    def continue_run(self, tiers, seconds):
        """Background continuation: ask the deferred tiers in order, each time-limited, within its own budget (no expected-cost gate:
        nobody waits). -> the accepted (contract, interpretation) or None. Never queues another continuation."""
        from .answer_budget import start as start_budget
        from . import foreground_priority as fp
        budget = self.budget = start_budget(seconds)
        self.foreground_wait = 0.0
        with fp.background_run(), model_policy.question_path():  # local-model lock acquisitions in here yield to any foreground waiter
            for name in tiers:
                if budget.expired(): break
                if not self.runnable(name): continue
                self.foreground_wait += fp.wait_for_foreground()  # foreground first: no new tier starts while a question is waited on
                found = self.attempt(name, self.tier_seconds(name, budget))
                if found is not None: return self.accept(getattr(self, 'order_used', None) or tuple(tiers), name, found)
            return self.escalate(getattr(self, 'order_used', None) or tuple(tiers), budget)

    def queue_background(self):
        """Hand the deferred tiers to the server's one background worker (AGERE_BACKGROUND_CONTINUE=off disables); records what happened."""
        if not self.deferred or self.background is not None: return self.background
        try:
            from . import background_continuation
            self.background = background_continuation.enqueue(self)
        except Exception as exc:  # noqa: BLE001 - the early reply never waits on or fails because of the queue
            self.background = {'queued': False, 'reason': f'{type(exc).__name__}: {exc}'[:200]}
        return self.background

    def gaps(self, text):
        """Every requirement of the original question the executable text drops (requirement_ledger: literal check, comparator
        direction, AND/OR structure)."""
        return requirement_ledger.gaps(self.question, text)

    def contract_gaps(self, *, contract=None, graph=None, receipt=None):
        """The acceptance contract (acceptance_contract.py) over a proposal before it executes: every unmet part of the question, [] when it passes.
        In observe mode the parts are only recorded."""
        from . import acceptance_contract as AC
        self.last_contract = None
        try:  # a semantic-analyst proposal is read with every query it rests on and what its definition views apply (analyst_acceptance)
            extra = AC.analyst_text(receipt, graph) if graph else ''
        except Exception:  # noqa: BLE001 - extra evidence is optional: the check then reads the SQL alone, as for every other tier
            extra = ''
        plain = lambda: self.gaps(evidence_of(contract or {}) if contract else '\n'.join(
            t for t in (executable_text([n.get('parameters') for n in graph.get('nodes', [])]), extra) if t))
        if AC.mode() == 'off': return plain()
        try:
            as_of = str((getattr(self.catalog, 'identity', None) or {}).get('as_of') or '')[:10] or None
            found = self.last_contract = AC.evaluate(self.question, contract=contract, graph=graph, as_of=as_of, phase='plan', extra_text=extra)
        except Exception as exc:  # noqa: BLE001 - the contract failing to run never blocks: the literal check below still decides (heal, don't block)
            return plain()
        return found['unmet'] if AC.enforcing() else plain()

    def evidence(self, contract):
        if (contract or {}).get('kind') == 'checked_graph':
            return executable_text([n.get('parameters') for n in contract['graph'].get('nodes', [])])
        return evidence_of(contract or {})

    def note(self, name, outcome, detail, started):
        self.log.append({'proposer': name, 'outcome': outcome, 'detail': detail,
                         'seconds': round(time.perf_counter() - started, 3), **({'rung': 'escalation'} if model_policy.escalating() else {})})

    def reject(self, name, reasons):
        if cancelled(): return None  # an abandoned tier records nothing
        self.log.append({'proposer': name, 'outcome': 'rejected', 'detail': '; '.join(reasons), 'seconds': None,
                         **({'rung': 'escalation'} if model_policy.escalating() else {})})
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
            # Only a candidate whose sole open issue is a missing confirmation may be picked: one a correctness check refuted
            # (values the warehouse never holds, a zero-result probe, source coverage, shape, guard, an unrun check) never ships.
            if any(not r.startswith(FALLBACK_SOFT_REASONS) for r in c['reasons']): continue
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
                **({'free_form': True} if name in FREE_FORM else {}),
                'proposer': {'name': name, 'cost': cost, 'calibration': calibration, 'kind': kind,
                             'check': CHECK_VERSION, 'cascade': VERSION},
                'proposers': self.log, 'cascade_usage': self.usage(), 'model_ladder': self.ladder_receipt(), 'system_one_routing': self.routing, 'answer_timing': self.timing(),
                'stage_diagnostics': stage_diagnostics.attribute(self.log, answered_by=name),
                'cascade_routing': {'system_one': self.routing, 'learned': self.learned_routing,
                                    'tier_router': getattr(self, 'tier_routing', None),
                                    'free_first': getattr(self, 'free_first_receipt', None)}}

    # -- typed proposals ------------------------------------------------------------------------------------------------
    def typed(self, name, found):
        """Accept a compiled typed contract only when the literal check passes over its executable parts."""
        if not found: return None
        contract, interpretation = found
        gaps = self.contract_gaps(contract=contract)
        if gaps: return self.reject(name, gaps)
        return contract, {**interpretation, 'literal_check': 'passed'}

    def tier_paraphrase_reuse(self):
        from .shared_planner import reuse_paraphrase
        found = reuse_paraphrase(self.stack, self.catalog, self.question, self.population, self.memory_identity)
        if found and found[1].get('review') in ('spec_verified', 'spec_partly_verified') and getattr(self.stack, 'allow_live', False):
            # a reworded question reusing a verified spec: System One confirms the slot mapping against THIS wording, part by part
            from . import spec_path
            check = spec_path.confirm_reuse(self.stack, self.catalog, self.question, found[0], ledger=self.ledger)
            if check is not None:
                self.spend(check['calls'], check['cost_usd'])
                if check['failed']:
                    return self.reject('paraphrase_reuse', ['spec slot mapping: System One refused ' + '; '.join(f'{p["id"]} ({p["label"]})' for p in check['parts'] if p['status'] == 'fail')])
                found = (found[0], {**found[1], 'spec_verification': spec_path.spec_verify.summary(check), 'slot_mapping_confirmed': check['confirmed']})
        return self.typed('paraphrase_reuse', found)

    def tier_spec_path(self):
        from . import spec_path
        if not spec_path.enabled(self.stack): return None
        return spec_path.propose(self)

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
        gaps = self.contract_gaps(graph=graph, receipt=receipt)
        if gaps: return self.reject(name, gaps)
        if isinstance(receipt, dict) and getattr(self, 'last_contract', None):  # the plan-time half of the contract rides on the proposal to answer_checks
            from . import acceptance_contract as AC
            receipt['acceptance_contract'] = AC.summary(self.last_contract)
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
        misfit = shape_misfit(self.question, result.get('answer'), analyst=isinstance(receipt, dict) and isinstance(receipt.get('analyst_evidence'), dict))
        if misfit:
            offered = getattr(self, 'budget', None) and self.executed(name, graph, receipt, result)
            if offered: self.budget.offer(offered, label=name, reasons=[misfit]); self.keep(name, offered, [misfit])
            return self.reject(name, [misfit])
        return self.executed(name, graph, receipt, result, paid_execution=result.get('new_paid_api_calls', 0))

    @staticmethod
    def placeholder(contract):
        """A held decline whose only query selects nothing by construction (WHERE 1=0) is a request to restate the question,
        never a '0 rows' result (2026-10-23)."""
        from .answer_checks import _EMPTY_BY_CONSTRUCTION
        queries = re.findall(r'"query":\s*"((?:[^"\\]|\\.)*)"', json.dumps(contract, default=str))
        return bool(queries) and all(_EMPTY_BY_CONSTRUCTION.search(q.replace('\\n', ' ')) for q in queries)

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

    def tier_chart_reader(self):  # a named patient is never a cohort question: the chart reader answers it first
        from .chart_reader import tier
        return self.graph('chart_reader', tier(self.stack, self.question, self.population, ledger=self.ledger))

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
    def tier_s1_metric_router(self): return self.s1_tier('s1_metric_router', self.ladder.s1_metric_router)
    def tier_s1_operator_router(self): return self.s1_tier('s1_operator_router', self.ladder.s1_operator_router)

    def s1_tier(self, name, route):
        """A System One router tier: a proposal is charged by graph() from its receipt; a decline still pays for the calls it made."""
        found = route()
        if not found:
            rec = getattr(self.ladder, 's1_rec', None)
            if rec is not None: self.spend(*rec.unbilled())
            return None
        return self.graph(name, found)
    def tier_local_free_sql(self): return self.graph('local_free_sql', self.ladder.local_sql(default='on'))
    def tier_evidence_retrieval(self):
        from .evidence_proposers import retrieval_tier
        return self.graph('evidence_retrieval', retrieval_tier(self.stack, self.question, self.population))
    def tier_evidence_review(self):
        from .evidence_proposers import review_tier
        return self.graph('evidence_review', review_tier(self.stack, self.question, self.population))
    def tier_atomic_system_one(self):
        from .atomic_questions import tier
        from . import system_one_gate
        def early(graph):  # System One is paid per patient: a plan the literal check will reject is declined before any call
            gaps = self.gaps(executable_text([n.get('parameters') for n in graph.get('nodes', [])]))
            if gaps: raise QuestionNotUnderstood('literal pre-check before any System One call: the plan drops ' + '; '.join(gaps)[:300])
            try:
                review=system_one_gate.check_plan(self.question, graph, stack=self.stack, ledger=self.ledger)
                self.spend(review['calls'],review['cost_usd'])
                self.note('atomic_system_one','plan_coverage',json.dumps(review),self.started)
            except Exception as exc:
                self.note('atomic_system_one','plan_coverage_unavailable',type(exc).__name__,self.started)
                return  # provider failure must not remove execution/reasoning alternatives
            if review['reject']:
                raise QuestionNotUnderstood('System One plan coverage: '+review['label']+'; continue investigation of the complete request')
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
        'accepted after the literal-question and output checks. Not clinical qualification.',
        *(['Free-form answer: no verified question spec stands behind it; it was shipped because System One confirmed it answers the question.']
          if receipt['interpretation'].get('free_form') else [])]))
    if receipt['interpretation'].get('free_form'): answer['free_form'] = True
    if answer.get('headline') is None:
        value = answer.get('count', answer.get('value'))
        answer['headline'] = str(value) if value is not None else f"{len(answer.get('table') or [])} rows"
    from .shared_planner import economics
    return {**result, 'schema': 'shared-warehouse-planner/5', 'synthetic': True, 'question': question,
            'route': 'shared_planner', 'graph': graph, 'answer': answer, 'request_contract': {k: v for k, v in contract.items() if k != 'executed'},
            'planner_receipt': {k: v for k, v in receipt.items() if k != 'request_contract'},
            'new_paid_api_calls': receipt['new_paid_api_calls'], 'planning_economics': economics(receipt),
            'elapsed_seconds': time.perf_counter() - started, 'clinical_execution_authorized': False}
