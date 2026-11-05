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

from .. import model_policy

from . import request_plan, spec_fill, spec_readback, spec_verify
from .question_input import QuestionNotUnderstood

VERSION = 'spec-path/1'


def enabled(stack=None):
    return os.environ.get('AGERE_SPEC_PATH', 'on') != 'off'


def spend_receipt(cascade, receipt):
    cascade.spend(receipt.get('calls', 0), receipt.get('usd', 0.0), receipt.get('cost_known', True))
    # a Haiku spec call made by the early start ran outside this tier's model tracking: record it here, so the escalation rung (Sonnet)
    # still knows this tier spent a Haiku attempt (Codex 2026-10-24)
    haiku = ((receipt.get('fillers') or {}).get('haiku') or {})
    if haiku.get('calls') and not model_policy.escalating():
        model_policy.record(os.environ.get('AGERE_SPEC_HAIKU', spec_fill.HAIKU))


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


def merge_fill_receipts(prior,current):
    if not prior:return current
    return {**current,'fillers':{**prior.get('fillers',{}),**current.get('fillers',{})},
        'calls':prior.get('calls',0)+current.get('calls',0),
        'usd':prior.get('usd',0.)+current.get('usd',0.),
        'seconds':prior.get('seconds',0.)+current.get('seconds',0.),
        'cost_known':prior.get('cost_known',True)and current.get('cost_known',True),
        'scheduling':'nonfrontier_then_hosted'}


def fill_in_order(stack,catalog,question,*,fillers=None,**kwargs):
    names=tuple(fillers)if fillers is not None else spec_fill.enabled_fillers()
    if os.environ.get('AGERE_NONFRONTIER_FIRST')!='on':
        return spec_fill.fill(stack,catalog,question,fillers=names,**kwargs)
    # Unilateral scheduling deadlines must not discard a valid cheap proposal
    # while its hosted replacement is already being bought. Transport/native
    # context limits and explicit caller cancellation remain in force.
    kwargs.setdefault('seconds',float('inf'))
    kwargs.setdefault('filler_limits',{name:float('inf')for name in names})
    native=tuple(n for n in names if n!='haiku');prior=None
    if native:
        try:return spec_fill.fill(stack,catalog,question,fillers=native,**kwargs)
        except spec_fill.SpecNotFilled as exc:prior=exc
    if 'haiku'not in names:
        if prior is not None:raise prior
        raise spec_fill.SpecNotFilled('No applicable spec filler configured')
    try:result=spec_fill.fill(stack,catalog,question,fillers=('haiku',),**kwargs)
    except spec_fill.SpecNotFilled as exc:
        exc.receipt=merge_fill_receipts(getattr(prior,'receipt',None),exc.receipt or {})
        if exc.held is None and prior is not None:exc.held=prior.held
        raise
    result['receipt']=merge_fill_receipts(getattr(prior,'receipt',None),result['receipt'])
    return result


def propose(c):
    """Cascade tier body. -> (contract, interpretation), 'held' (an unresolved spec held as the decline), or None / QuestionNotUnderstood."""
    from . import shared_planner
    from .answer_checks import gate as answer_gate
    live = bool(getattr(c.stack, 'allow_live', False))
    ledger = c.ledger if live else None
    started = time.perf_counter()
    fillers = spec_fill.enabled_fillers() if live else ('grammar',)
    if model_policy.escalating():  # the Sonnet rung: only the Claude spec writer runs again (the grammar and System One slot fillers already failed)
        if 'haiku' not in fillers: return None
        fillers = ('haiku',)
    try:
        filled = fill_in_order(c.stack, c.catalog, c.question, population=c.population, ledger=ledger, fillers=fillers,
                                verify=(lambda cand: verify_spec(c, c.question, c.catalog, cand['plan'], ledger=ledger)) if live else None)
    except spec_fill.SpecNotFilled as exc:
        if exc.receipt: spend_receipt(c, exc.receipt)
        # Carry this request's failed reasoning into the existing investigator.
        # It is not an executed proposal, compiled contract or reuse candidate.
        attempts = []
        for filler in (exc.receipt or {}).get('fillers', {}).values():
            for item in (filler.get('notes') or {}).get('rejected_proposals', []):
                if (item.get('authority') == 'unverified_proposal' and item.get('executed') is False
                        and item.get('question_fingerprint') == request_plan.fingerprint(c.question)
                        and item.get('proposal_fingerprint') == request_plan.fingerprint(item.get('proposal'))
                        and isinstance(item.get('validation_error'), str)):
                    attempts.append(item)
        if attempts:
            import copy
            c.second_look_hint = {**getattr(c, 'second_look_hint', {}),
                                  'prior_plan_attempts': copy.deepcopy(attempts)}
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
        # The typed contract is executed again after its checked proposal. Keep
        # the same unambiguous output conformance on the actual shipped result.
        from .answer_shape import final
        result.update(final(question, result.get('graph') or {}, {}, result))
        answer = result['answer']
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
