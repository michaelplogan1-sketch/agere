"""The spec path: the main path after reuse (docs/agere/QUESTION_SPEC.md). The cascade tier `spec_path` calls propose().

  1. spec_fill.fill: the grammar, System One slot filling and one Haiku spec call, in parallel; the first valid spec, with the choice rules
     for disagreeing readings.
  2. spec_verify.verify: System One checks the spec part by part against the question (the grammar's own spec is deterministic and not re-judged).
     A failed part is repaired once by the Haiku filler; a part that still fails leaves the question unresolved for this path.
  3. The spec is executed once inside the tier and passed through answer_checks (decision context and the other mandatory checks; a spec-verified
     answer needs no result-summary cross-check), then returned as an ordinary typed contract, which shared_planner executes, caches and
     presents with answer['read_back'].

Every spec that passes is saved under its question shape (shared_planner.store_request / request_shapes; evaluation mode writes nothing), and a
rewording that reuses it has its slot mapping confirmed by System One before it is answered (confirm_reuse).
"""
import os
import time

from . import request_plan, spec_fill, spec_readback, spec_verify
from .question_input import QuestionNotUnderstood

VERSION = 'spec-path/1'


def enabled(stack=None):
    return os.environ.get('AGERE_SPEC_PATH', 'on') != 'off'


def spend_receipt(cascade, receipt):
    cascade.spend(receipt.get('calls', 0), receipt.get('usd', 0.0), receipt.get('cost_known', True))


def interpretation_for(question, catalog, fill_result, verification, repaired=None):
    plan, contract = fill_result['plan'], fill_result['contract']
    parts = spec_readback.read_back(plan, catalog, question)
    confirmed = verification is None or verification['confirmed']
    contrary = [p for p in (verification or {}).get('parts', []) if p.get('status') == 'unverified' and p.get('label') in ('partly', 'no')]
    soft = not confirmed and not contrary  # System One could not clear its bar but nothing it said contradicts the reading: answered, low confidence, parts named
    out = {'interpreter': 'spec_path', 'review': 'spec_verified' if confirmed else 'spec_partly_verified', 'execution_accepted': True,
           'literal_check': 'passed', 'qualified_interpretation': False, 'attempts': [],
           'semantic_review': {'equivalent': True, 'issues': []} if confirmed else None,
           'spec_filler': fill_result['winner'], 'spec_agreement': fill_result['agreement'], 'spec_fill': fill_result['receipt'],
           'read_back': parts,
           'acceptance_scope': 'Typed spec compiled, literal-checked and decision-context-checked; each part checked against the question by System One'
                               if confirmed else 'Typed spec compiled and checked; some parts were not confirmed by System One'}
    if fill_result.get('readings'): out['spec_readings'] = fill_result['readings']
    if verification is not None: out['spec_verification'] = spec_verify.summary(verification)
    else: out['spec_verification'] = {'version': spec_verify.VERSION, 'verdict': 'not_needed', 'confirmed': True,
                                      'note': 'the deterministic grammar filled this spec'}
    if repaired: out['spec_repair'] = repaired
    terms = fill_result['candidate'].get('notes', {}).get('terminology')
    if terms: out['terminology'] = terms
    if not confirmed:
        out.pop('semantic_review')
        out['spec_unconfirmed'] = 'soft' if soft else 'contrary'
        if not soft:
            out['partial'] = True
            out['partial_reason'] = 'spec_parts_unconfirmed'
    return out


def verify_spec(cascade_or_stack, question, catalog, plan, *, ledger):
    stack = getattr(cascade_or_stack, 'stack', cascade_or_stack)
    return spec_verify.verify(stack, question, plan, catalog, ledger=ledger)


def propose(c):
    """Cascade tier body. -> (contract, interpretation), 'held' (an unresolved spec held as the decline), or None / QuestionNotUnderstood."""
    from . import shared_planner
    from .answer_checks import gate as answer_gate
    live = bool(getattr(c.stack, 'allow_live', False))
    ledger = c.ledger if live else None
    started = time.perf_counter()
    fillers = spec_fill.enabled_fillers() if live else ('grammar',)
    try:
        filled = spec_fill.fill(c.stack, c.catalog, c.question, population=c.population, ledger=ledger, fillers=fillers,
                                verify=(lambda cand: verify_spec(c, c.question, c.catalog, cand['plan'], ledger=ledger)) if live else None)
    except spec_fill.SpecNotFilled as exc:
        if exc.receipt: spend_receipt(c, exc.receipt)
        if exc.held:
            plan, contract = exc.held
            interpretation = interpretation_for(c.question, c.catalog, {'plan': plan, 'contract': contract, 'winner': 'haiku', 'agreement': 'single',
                                                                         'receipt': exc.receipt or {}, 'readings': None, 'candidate': {}}, None)
            c.held_decline = c.held_decline or (contract, {**interpretation, 'hosted_dispatch_authority': 'server_allow_live_after_deterministic_decline'})
            return 'held'
        raise QuestionNotUnderstood(str(exc)) from exc
    spend_receipt(c, filled['receipt'])
    verification, repaired = None, None
    if filled['winner'] != 'grammar' and live:
        verification = filled.get('candidate', {}).get('verification') or verify_spec(c, c.question, c.catalog, filled['plan'], ledger=ledger)
        c.spend(verification['calls'], verification['cost_usd'])
        if verification['failed']:
            review = spec_verify.feedback(verification)
            try:
                again = spec_fill.repair(c.stack, c.catalog, c.question, filled['plan'], review, population=c.population, ledger=ledger)
            except spec_fill.SpecNotFilled as exc:
                if exc.receipt: spend_receipt(c, exc.receipt)
                c.reject('spec_path', ['unresolved: System One refused ' + '; '.join(f'{p["id"]} ({p["label"]})' for p in verification['parts'] if p['status'] == 'fail')
                                       + f'; the repair did not validate ({exc})'])
                return None
            spend_receipt(c, again['receipt'])
            second = verify_spec(c, c.question, c.catalog, again['plan'], ledger=ledger)
            c.spend(second['calls'], second['cost_usd'])
            repaired = {'first_failed': verification['failed'], 'first_read_back': verification['read_back'], 'second_verdict': second['verdict']}
            if second['failed']:
                c.reject('spec_path', ['unresolved: after one repair System One still refused ' + '; '.join(f'{p["id"]} ({p["label"]})' for p in second['parts'] if p['status'] == 'fail')])
                return None
            filled, verification = {**again, 'agreement': 'repair'}, second
    interpretation = interpretation_for(c.question, c.catalog, filled, verification, repaired)
    contract = filled['contract']
    graph = shared_planner.graph_for(contract)
    receipt = {'route': 'shared_planner', 'planner': 'shared_planner', 'request_contract': contract, 'interpretation': interpretation,
               'new_paid_api_calls': 0, 'known_api_estimate_usd': 0.0, 'api_cost_known': True,
               'feedback_identity': request_plan.fingerprint([]), 'spec_verification': interpretation['spec_verification']}
    try:
        result = shared_planner.execute_contract(c.stack, c.question, graph, receipt, population=c.population, started=time.perf_counter())
    except (ValueError, KeyError, TypeError, RuntimeError) as exc:
        c.reject('spec_path', [f'execution failed: {type(exc).__name__}: {exc}'[:300]])
        return None
    if (result.get('answer') or {}).get('status') == 'not_understood':
        c.reject('spec_path', ['the spec could not be executed as a complete request: ' + '; '.join(map(str, result['answer'].get('missing') or []))[:300]])
        return None
    if (result.get('answer') or {}).get('status') != 'not_available':
        checked = answer_gate(c, 'spec_path', graph, receipt, result)
        if checked is None: return None
        summary = receipt.get('answer_checks')
        if summary: interpretation['answer_checks'] = summary
    interpretation['spec_seconds'] = round(time.perf_counter() - started, 3)
    return contract, interpretation


def confirm_reuse(stack, catalog, question, contract, *, ledger=None):
    """A rewording that reuses a verified spec (shape or paraphrase): System One confirms the slot mapping against THIS wording, part by part.
    -> the spec_verify receipt, or None when System One is not available (the deterministic reuse guards stand)."""
    from .system_one_gate import ready
    if not ready(stack, 'AGERE_S1_SPEC_VERIFY'): return None
    return spec_verify.verify(stack, question, contract['request'], catalog, ledger=ledger)


def attach(result, interpretation, contract, catalog, question):
    """Every typed answer carries its read-back; a spec-path answer also its verification and fill receipts."""
    answer = result.get('answer')
    if not isinstance(answer, dict) or contract.get('kind') in ('checked_graph', 'request_program', 'general_analysis'): return result
    try:
        answer['read_back'] = interpretation.get('read_back') if interpretation.get('read_back') and not interpretation.get('exact_contract_cache_hit') else \
            spec_readback.read_back(contract['request'], catalog, question)
    except Exception:  # noqa: BLE001 - a read-back never takes an answer down
        pass
    if interpretation.get('spec_verification'): answer['spec_verification'] = interpretation['spec_verification']
    if interpretation.get('spec_fill'): answer['spec_fill'] = interpretation['spec_fill']
    checks = interpretation.get('answer_checks')
    if checks:
        result['answer_checks'] = checks
        answer['limitations'] = list(dict.fromkeys([*(answer.get('limitations') or []), *(checks.get('annotations') or [])]))
    unconfirmed = [p for p in (interpretation.get('spec_verification') or {}).get('parts', []) if p.get('status') != 'pass']
    if interpretation.get('spec_unconfirmed') and unconfirmed:
        if interpretation['spec_unconfirmed'] == 'contrary' and answer.get('status') == 'answered': answer['status'] = 'partial'
        else: answer.setdefault('confidence', 'low')
        answer['limitations'] = [*(answer.get('limitations') or []),
                                 'System One could not confirm these parts of the reading against the question: ' + '; '.join(p['text'] for p in unconfirmed)[:500]]
    if interpretation.get('free_form'):
        answer['free_form'] = True
        answer['limitations'] = list(dict.fromkeys([*(answer.get('limitations') or []),
                                                    'Free-form interpretation (not a verified spec); shipped only after System One confirmed it answers the question.']))
    return result
