"""Verify instead of first-claim-wins (HANDOFF 2026-10-14 root cause: the ladder stopped at the first confident cheap rung).

When the checked-proposer cascade accepts an executed proposal whose answer is NOT certified (answer_checks certificate
other than 'certified'), verify() runs the next INDEPENDENT proposers (a different family: saved-work replays never verify
each other) through the same checks, cheapest first and System One (Jev/Perplexity) before any Claude tier, until one
executes, and reconciles:
  agree       the first claim stands, marked independently confirmed (pass^2 stable)
  disagree    the cross-family position-swapped judge (judge.compare: Jev + Perplexity, both orders) picks; 'B' replaces
              the first claim with the verifier's answer; 'split' / 'undecided' keeps the first claim with both readings
              attached, low confidence and a stability warning (heal, never block: an answer is always returned)
  no verifier nothing independent executed; the first claim stands and the receipt says what was tried
Stop at confirmation (2026-10-23 latency): a certified answer, or one System One's cross-check confirmed with no 'answers_partially'
vote, is not re-run through other tiers; the receipt says 'confirmed by System One'. Verifiers of an uncertified, unconfirmed answer
start only while the answer budget still covers their expected seconds (otherwise noted skipped: budget).
Never removes a tier and never stops the question: a verifier that errors or declines is just noted. Every paid call
(System One judge, paid verifier tiers) is charged to the cascade. AGERE_VERIFY_CLAIMS=off restores first-claim-wins;
AGERE_VERIFY_MAX (default 3) bounds the verifiers that actually propose (a free tier that proposes nothing does not count;
at most 4x that many tiers are asked).
"""
import os
import time

VERSION = 'claim-verification/1'

# proposer -> family; proposers in one family are not independent of each other
FAMILY = {
    'exact_local_plan': 'authored', 'scoped_review_compiler': 'authored', 'undelivered_input': 'decline',
    'specialist_families_early': 'families', 'specialist_families_late': 'families',
    'cohort_family_early': 'cohort_family', 'cohort_family_late': 'cohort_family',
    'paraphrase_reuse': 'saved_work', 'semantic_cache': 'saved_work', 'template_decoding': 'saved_work', 'learned_verified_sql': 'saved_work', 'bottled_sql': 'saved_work',
    'evidence_retrieval': 'evidence', 'evidence_review': 'evidence', 'atomic_system_one': 'system_one', 'typed_decider': 'system_one',
    'local_normalizer': 'local_model', 'qwen_rewrite_compiler': 'local_model', 'qwen_typed_interpreter': 'local_model', 'local_free_sql': 'local_model',
    'local_compiler': 'compiler', 'metric_router': 'router', 'operator_router': 'router', 's1_metric_router': 'router', 's1_operator_router': 'router',
    'hosted_typed_interpreter': 'claude_typed', 'hosted_verified_sql': 'claude_sql', 'semantic_analyst': 'claude_sql',
    'frontier_graph_planner': 'claude_planner', 'stack_planner_propose': 'claude_planner', 'hybrid_analysts': 'claude_analysts',
    'legacy_cost_ladder': 'legacy',
}
SELF_CERTAIN = {'exact_local_plan', 'undelivered_input'}  # exact authored match / deterministic decline: nothing to verify
NEVER_VERIFY_WITH = {'undelivered_input', 'legacy_cost_ladder'}
COST_RANK = {'free': 0, 'system_one': 1, 'claude': 2}


def enabled():
    return os.environ.get('AGERE_VERIFY_CLAIMS', 'on') != 'off'


def max_verifiers():
    try:
        return max(0, int(os.environ.get('AGERE_VERIFY_MAX', '3')))
    except ValueError:
        return 3


def cost_class(name):
    from .system_one_gate import CLAUDE_TIERS
    if name in CLAUDE_TIERS: return 'claude'
    if FAMILY.get(name) == 'system_one' or name in ('s1_metric_router', 's1_operator_router'): return 'system_one'
    return 'free'


def needs_verification(name, contract):
    """Only an executed, asserted, uncertified graph answer is verified."""
    if not enabled() or name in SELF_CERTAIN or not isinstance(contract, dict) or contract.get('kind') != 'checked_graph': return False
    executed = contract.get('executed') or {}
    answer = executed.get('answer') or {}
    if answer.get('status') not in ('answered', 'partial') or answer.get('answer_type') in (None, 'unsupported'): return False
    cert = ((executed.get('answer_checks') or {}).get('certificate') or {}).get('status')
    return cert != 'certified'


def confirmed_by_system_one(contract):
    """System One's cross-check (both families) confirmed the executed answer and neither family called it only partial."""
    s1 = (((contract or {}).get('executed') or {}).get('answer_checks') or {}).get('system_one_cross_check') or {}
    votes = s1.get('votes') or {}
    return s1.get('verdict') == 'confirmed' and not any(isinstance(v, dict) and v.get('label') == 'answers_partially' for v in votes.values())


def verifiers(order, first, *, tried=(), claude_down=False):
    """Independent proposers after `first`, cheapest class first (free, System One, then Claude), cascade order inside a class."""
    fam = FAMILY.get(first, first)
    rest = list(order)[list(order).index(first) + 1:] if first in order else list(order)
    pool = [n for n in rest if n not in tried and n not in NEVER_VERIFY_WITH and FAMILY.get(n, n) != fam
            and not (claude_down and cost_class(n) == 'claude')]
    return sorted(pool, key=lambda n: COST_RANK[cost_class(n)])  # stable: cascade order inside a class


def _answer(contract):
    return ((contract or {}).get('executed') or {}).get('answer') or {}


def verify(cascade, order, name, found):
    """cascade: proposers.Cascade after accepting `found` from `name`. -> (contract, interpretation) possibly replaced, with
    interpretation['claim_verification'] and ['answer_stability']."""
    from . import answer_stability as S
    contract, interpretation = found
    if not needs_verification(name, contract): return found
    if confirmed_by_system_one(contract):  # confirmed: no heavy re-run for a second opinion
        return contract, {**interpretation, 'claim_verification': {'version': VERSION, 'first_claim': name, 'tried': [], 'status': 'confirmed',
                                                                    'verification': 'confirmed by System One', 'seconds': 0.0}}
    started = time.perf_counter()
    first_family = FAMILY.get(name, name)
    receipt = {'version': VERSION, 'first_claim': name, 'tried': [], 'status': 'no_verifier'}
    second = None
    proposed = 0
    for v in verifiers(order, name, tried={e['proposer'] for e in cascade.log}, claude_down=bool(cascade.claude_down)):
        if proposed >= max_verifiers() or len(receipt['tried']) >= max_verifiers() * 4: break
        if cascade.claude_down and cost_class(v) == 'claude': continue
        budget = getattr(cascade, 'budget', None)
        if budget is not None and budget.expired(): receipt['stopped'] = 'answer time budget'; break
        expected = cascade.expected(v) if hasattr(cascade, 'expected') else 0.0
        if budget is not None and not budget.allows(expected):  # a second opinion only within what the budget has left
            receipt.setdefault('skipped_for_budget', []).append({'tier': v, 'expected_seconds': round(expected, 2)})
            cascade.note(v, 'skipped', f'skipped: budget (verification; expected ~{expected:g}s, {budget.remaining():.0f}s left)', time.perf_counter()); continue
        from .proposers import enabled as tier_on
        if cascade.population is not None:
            from .proposers import TYPED_WITH_POPULATION
            if v not in TYPED_WITH_POPULATION: continue
        if not tier_on(v, cascade.stack): continue
        t0 = time.perf_counter()
        try:
            got = cascade.call_tier(v, cascade.tier_seconds(v, budget)) if budget is not None and hasattr(cascade, 'call_tier') \
                else getattr(cascade, 'tier_' + v)()  # verifiers get the same per-tier time share as proposers
        except Exception as exc:  # noqa: BLE001 - a verifier failing never stops the answer
            cascade.note(v, 'verification error', f'{type(exc).__name__}: {exc}'[:300], t0); receipt['tried'].append(v); proposed += 1
            from .system_one_gate import CLAUDE_TIERS, outage
            if v in CLAUDE_TIERS and outage(exc): cascade.claude_down = cascade.claude_down or outage(exc)  # no further Claude verifier
            continue
        receipt['tried'].append(v)
        if got or cost_class(v) != 'free': proposed += 1
        if not got or got == 'held' or not isinstance(got, tuple) or (got[0] or {}).get('kind') != 'checked_graph' \
                or _answer(got[0]).get('status') not in ('answered', 'partial'):
            cascade.note(v, 'verification: no comparable answer', None, t0); continue
        cascade.note(v, 'verification: executed', f'independent of {name} ({first_family})', t0)
        second = (v, got); break
    if second is None:
        interpretation = {**interpretation, 'claim_verification': {**receipt, 'seconds': round(time.perf_counter() - started, 3)}}
        return contract, interpretation
    v, (c2, i2) = second
    a1, a2 = _answer(contract), _answer(c2)
    stab = S.stability([a1, a2, *S.internal_samples(contract.get('legacy_receipt'))])
    receipt.update(verifier=v, verifier_family=FAMILY.get(v, v), stability=stab)
    if stab['pass_k'] or S._same(a1, a2):
        receipt['status'] = 'confirmed'
        out = contract, interpretation
    else:
        from .judge import compare
        try:
            verdict = compare(cascade.question, {'name': name, 'graph': contract['graph'], 'result': contract['executed']},
                              {'name': v, 'graph': c2['graph'], 'result': c2['executed']}, stack=cascade.stack,
                              ledger=cascade.ledger if getattr(cascade.stack, 'allow_live', False) else None)
            cascade.spend(verdict['calls'], verdict['cost_usd'])
        except Exception as exc:  # noqa: BLE001 - no judge: keep the first claim, readings attached
            verdict = {'verdict': 'undecided', 'error': f'{type(exc).__name__}: {exc}'[:200]}
        receipt['judge'] = verdict
        if verdict['verdict'] == 'B':
            receipt['status'] = 'replaced_by_verifier'
            out = c2, {**i2, 'replaced_first_claim': name}
            name = v
        elif verdict['verdict'] == 'A':
            receipt['status'] = 'first_claim_upheld_by_judge'
            out = contract, interpretation
        else:
            receipt['status'] = 'unreconciled'
            executed = dict(contract['executed'])
            answer = dict(executed.get('answer') or {})
            answer['readings'] = [{'proposer': name, 'answer': {k: a1.get(k) for k in ('answer_type', 'count', 'value', 'headline')}},
                                  {'proposer': v, 'answer': {k: a2.get(k) for k in ('answer_type', 'count', 'value', 'headline')}}]
            answer['limitations'] = list(dict.fromkeys([*(answer.get('limitations') or []), S.warning(stab),
                                                        f"Independent proposers disagree ({name} vs {v}); the judge was {verdict['verdict']}."]))
            answer['confidence'] = 'low'
            # Two independent proposers disagree and nothing settled it: both readings ship as a partial answer, never the
            # first one as if it were checked (Codex review 2026-10-23).
            out = {**contract, 'executed': {**executed, 'answer': answer}}, {
                **interpretation, 'partial': True, 'partial_reason': 'independent_proposers_disagree',
                'limitations': [*(interpretation.get('limitations') or []),
                                f'Independent proposers disagree ({name} vs {v}); both readings are reported.']}
    contract, interpretation = out
    receipt['seconds'] = round(time.perf_counter() - started, 3)
    return contract, {**interpretation, 'claim_verification': receipt, 'answer_stability': stab, 'verified_proposer': name}
