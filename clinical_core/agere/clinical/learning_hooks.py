"""Learn from every answer: one call after an answer is built (conversation.handle_compose, after capture).

after_answer(stack, question, result, body=None):
  * verified answers (answered, nothing missing, accepted interpretation, no high-precision quality warning, no user
    correction) that a cascade proposer produced are saved as a learned (question -> SQL) pair, so the next identical
    question is answered by learned_verified_sql and the next slot-identical rewording by the semantic cache (typed
    contracts are already saved for paraphrase reuse by shared_planner.plan). Paid proposals are the point: their cost is
    paid once. Replays of saved work (paraphrase reuse, semantic cache, learned SQL, local normalizer) are never saved
    under the new wording; the hosted verified ladder keeps its own capture (learning.capture_verified).
  * every verified answer is appended to the training corpus (<AGERE_TRAINING_DIR or artifacts/training>/traffic) in the
    labeled-row format tools/training/datasets.py reads, so the self-learning loop retrains on real traffic too; a
    decline is appended to the gaps file (grammar growth / negatives), never as a learned answer. Every verified cascaded
    answer also appends a router-outcome row (the tier whose checked answer was right; tools/training/router_outcomes.py),
    the outcome-labelled data the tier router trains on (tools/training/self_improve.py closes the loop).
  * the off-path learning components are wired: learning.quarantine -> record_correction for answers that trip a
    high-precision control (correction_record); an explicit correction in the request body (learning.record_correction,
    also excluded from the corpus); question-source learning tasks (question_learning.prepare); learning-queue
    suggestions (learning_queue.suggest, paid providers only when the stack allows live calls); follow-up answer
    bindings (followup_answers.attach).
Learning is bookkeeping: it never raises into the answer path (errors are returned on the receipt). Synthetic data only.
"""
import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path

from .evaluation import active as _evaluating

VERSION = 'learning-hooks/1'
ROOT = Path(__file__).resolve().parents[3]
REPLAYS = {'paraphrase_reuse', 'local_normalizer', 'semantic_cache', 'learned_verified_sql'}  # never saved under new wording
EXACT = {'compositional_grammar', 'compositional_grammar_program', 'learned_request_shape', 'learned_analysis_shape'}


def training_dir():
    return Path(os.environ.get('AGERE_TRAINING_DIR') or ROOT / 'artifacts' / 'training') / 'traffic'


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def interpretation_of(result):
    return ((result or {}).get('planner_receipt') or {}).get('interpretation') or {}


def proposer_of(result):
    i = interpretation_of(result)
    return (i.get('proposer') or {}).get('name') or i.get('interpreter')


def verified(stack, question, result):
    """-> (bool, reasons). Only verified answers are learned from; Agere never learns from its own unverified output."""
    from .learning import QUARANTINE_CONTROLS, corrected
    answer = (result or {}).get('answer') or {}
    reasons = []
    if answer.get('status') != 'answered': reasons.append(f"answer status {answer.get('status')}")
    if answer.get('missing'): reasons.append('unresolved answer requirements')
    if not interpretation_of(result).get('execution_accepted'): reasons.append('interpretation not accepted')
    hits = sorted({c['control'] for c in answer.get('quality_checks') or []
                   if c.get('severity') == 'warning' and c.get('control') in QUARANTINE_CONTROLS | {'QC-DRIFT'}})
    if hits: reasons.append('quality controls: ' + ', '.join(hits))
    reasons += check_flags(result)
    try:
        if corrected(stack.path, question): reasons.append('a user corrected this question')
    except sqlite3.Error:
        pass
    return not reasons, reasons


def check_flags(result):
    """Flags the answer checks raised on THIS answer that make it unfit to save as a verified pair (FRONTIER #5: capture only
    after checks, quarantine anything flagged): low confidence (unchecked zero, unreconciled readings), a System One check
    that found part of the question unanswered, System One families that disagreed in the cross-check, independent
    proposers that disagreed (claim verification) and an unstable answer (pass^k failed)."""
    answer = (result or {}).get('answer') or {}
    interp = interpretation_of(result)
    checks = ((result or {}).get('planner_receipt') or {}).get('answer_checks') or (result or {}).get('answer_checks') or {}
    out = []
    if answer.get('confidence') == 'low' or checks.get('confidence') == 'low': out.append('low confidence')
    if (checks.get('system_one') or {}).get('label') == 'answers_partially': out.append('System One: part of the question may be unanswered')
    if (checks.get('system_one_cross_check') or {}).get('verdict') == 'split': out.append('System One families disagree')
    cv = interp.get('claim_verification') or {}
    if cv.get('status') == 'unreconciled': out.append('independent proposers disagree')
    stab = interp.get('answer_stability') or {}
    if stab.get('k', 0) >= 2 and not stab.get('pass_k'): out.append(f"unstable answer (not pass^{stab['k']})")
    if answer.get('readings'): out.append('two defensible readings')
    return out


def quarantine_pair(stack, question, result, reasons):
    """A cascade answer that a check flagged: its plan goes to the quarantined table with the reasons (audit only; never reused
    or trained on), so the store holds only pairs that passed every check."""
    from . import learning
    plan = sql_pair(result)
    if plan is None: return None
    db = learning._db(stack.path)
    try:
        db.execute('INSERT INTO quarantined(question, plan_kind, plan, signature, route, agreement, controls, created, context) '
                   'VALUES (?,?,?,?,?,?,?,?,?)',
                   (question.strip(), 'sql', json.dumps(plan, sort_keys=True), json.dumps(answer_signature(result.get('answer') or {}), default=str)[:4000],
                    f'cascade:{proposer_of(result)}', None, json.dumps(reasons), time.time(), learning.context(stack.path)))
        db.commit()
    finally:
        db.close()
    return {'quarantined': True, 'reasons': reasons}


def sql_pair(result):
    """The single executable SQL plan behind an answer ({'query', 'returns'}), or None when it is not one SQL query."""
    contract = (result or {}).get('request_contract') or {}
    graph = (result or {}).get('graph') or {}
    nodes = graph.get('nodes') or []
    if len(nodes) == 1 and nodes[0].get('operation') == 'sql':
        p = nodes[0].get('parameters') or {}
        if re.search(r'\bagere_result_\d+\b', str(p.get('query') or '')): return None
        if p.get('query'): return {'query': p['query'], 'returns': p.get('returns') or (result.get('answer') or {}).get('answer_type')}
    request = contract.get('request')
    if contract.get('query') and isinstance(request, dict) and not request.get('analyses') and not request.get('workflow'):
        return {'query': contract['query'], 'returns': contract.get('returns')}
    return None


def typed_request(result):
    contract = (result or {}).get('request_contract') or {}
    if contract.get('kind') == 'request_program' and contract.get('program'): return {'kind': 'program', 'value': contract['program']}
    if contract.get('kind') == 'general_analysis' and contract.get('analysis'): return {'kind': 'analysis', 'value': contract['analysis']}
    if isinstance(contract.get('request'), dict) and 'source' in contract['request']: return {'kind': 'request', 'value': contract['request']}
    return None


def answer_signature(answer):
    keep = {k: answer.get(k) for k in ('answer_type', 'count', 'value') if k in answer}
    if answer.get('table') is not None: keep['rows'] = answer['table'][:50]
    if answer.get('patient_ids'): keep['patient_ids_sha'] = _digest(sorted(answer['patient_ids']))[:20]
    return keep


def save_pair(stack, question, result, proposer):
    """learning.record_verified for a cascade-accepted answer: next time learned_verified_sql / semantic cache answer free."""
    from . import learning
    plan = sql_pair(result)
    if plan is None: return {'saved': False, 'reason': 'not a single SQL plan (typed contracts are reused through paraphrase reuse)'}
    interp = interpretation_of(result)
    p = interp.get('proposer') or {}
    learning.record_verified(stack.path, question, 'sql', plan, answer_signature(result.get('answer') or {}),
                             f'cascade:{proposer}', [p.get('cost')] if p.get('cost') else [],
                             f"cascade:{proposer}|{p.get('check') or ''}")
    return {'saved': True, 'route': f'cascade:{proposer}', 'paid': bool(result.get('new_paid_api_calls'))}


def corpus_row(question, result, proposer, paid):
    """One labeled row (tools/training label format) from live traffic."""
    interp = interpretation_of(result)
    canonical = interp.get('normalized_question') or interp.get('paraphrase_of') or question
    answer = result.get('answer') or {}
    request = typed_request(result)
    plan = sql_pair(result)
    contract = result.get('request_contract') or {}
    tables = sorted(set(re.findall(r'\b(?:FROM|JOIN)\s+"?([a-z_][a-z_0-9]*)"?', (plan or {}).get('query') or contract.get('query') or '')))
    sig = answer_signature(answer)
    return {'schema': 'training-label-row/1', 'labeler': VERSION, 'status': 'ok', 'id': _digest([question, proposer])[:24],
            'family': 'traffic', 'form': proposer or 'unknown',
            'template_family': f'traffic/{proposer or "unknown"}/{int(_digest(canonical.casefold())[:4], 16) % 32}',
            'canonical': canonical, 'request_source': 'traffic',
            'grammar_reads': interp.get('interpreter') in EXACT or interp.get('interpreter') == 'local_normalizer',
            'request': request or ({'kind': 'sql', 'value': plan} if plan else None),
            'contract_kind': contract.get('kind') or 'request',
            'answer': {'status': 'answered', 'answer_type': answer.get('answer_type'), 'returns': (plan or {}).get('returns'), **sig},
            'answer_key': _digest(sig)[:20], 'provenance': {'tables': tables, 'columns': [], 'query': (plan or {}).get('query'),
                                                            'request_id': contract.get('id')},
            'paraphrases': [] if canonical == question else [{'text': question, 'styles': ['traffic']}],
            'chain': None, 'engines': sorted({n.get('operation') for n in (result.get('graph') or {}).get('nodes') or []} -
                                             {'sql', 'query_population', 'analysis', None}),
            'proposer': proposer, 'paid': paid, 'at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'synthetic': True}


def router_outcome_row(question, result, proposer, paid):
    """tools/training/router_outcomes.py row: the accepted (verified) tier and what the tiers before it did."""
    log = interpretation_of(result).get('proposers') or []
    return {'schema': 'router-outcome/1', 'question': question, 'accepted': proposer, 'paid': paid,
            'tried': [(e.get('proposer'), e.get('outcome')) for e in log][:40],
            'question_id': ((result or {}).get('cost_telemetry') or {}).get('question_id'),
            'at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'synthetic': True}


def append(name, row):
    if _evaluating(): return None  # evaluation mode: no training-traffic, gap or corpus file is written
    d = training_dir(); d.mkdir(parents=True, exist_ok=True)
    path = d / f'{name}-{time.strftime("%Y%m%d")}.jsonl'
    with path.open('a') as f: f.write(json.dumps(row, separators=(',', ':'), default=str) + '\n')
    return str(path)


def components(stack, question, result, body):
    """Explicit learning requests carried by the compose body ('learning') or the result ('learning_requests')."""
    requests = {**((result or {}).get('learning_requests') or {}), **(((body or {}).get('learning')) or {})}
    done = {}
    if requests.get('correction'):
        from .learning import record_correction
        c = requests['correction']
        done['correction_record'] = record_correction(stack.path, question, c['note'], c.get('expected'), c.get('rule'))
        append('corrections', {'question': question, 'note': c['note'], 'at': time.strftime('%Y-%m-%dT%H:%M:%S')})
    if requests.get('question_learning'):
        from . import question_learning
        done['question_learning'] = {'task_id': question_learning.prepare(stack.path, requests['question_learning'])['id']}
    if requests.get('learning_queue'):
        from . import learning_queue
        q = requests['learning_queue']
        provider = q.get('provider', 'student_minilm')
        live = bool(getattr(stack, 'allow_live', False))
        if provider in ('perplexity', 'jev') and not live:
            done['learning_queue_suggest'] = {'skipped': 'paid provider requested while live calls are off'}
        else:
            got = learning_queue.suggest(stack.path, q['item_id'], provider, allow_live=live)
            done['learning_queue_suggest'] = {'provider': provider, 'id': (got or {}).get('id')}
    if requests.get('followup_answer'):
        from . import followup_answers
        done['followup_answers'] = {'id': followup_answers.attach(stack.path, requests['followup_answer'])['id']}
    return done


def after_answer(stack, question, result, *, body=None):
    """Learn from one finished answer. -> receipt; never raises."""
    receipt = {'version': VERSION, 'verified': False, 'saved_pair': None, 'corpus': None, 'quarantine': [], 'components': {}, 'errors': []}
    if _evaluating():  # a measured run learns nothing: no pair, corpus row, gap, trace, stability row, correction or learning task
        receipt['evaluation'] = True
        if os.environ.get('AGERE_COMPONENT_LEARNING','off')=='on':return receipt
        try:
            receipt['verified'], receipt['reasons'] = verified(stack, question, result)
        except Exception as exc:  # noqa: BLE001
            receipt['errors'].append(f'{type(exc).__name__}: {exc}'[:300])
        return receipt
    if os.environ.get('AGERE_COMPONENT_LEARNING','off')=='on':
        # Explicit component-learning mode never reads the legacy learning,
        # question-plan or answer stores. Observations need direct review before
        # becoming training labels; model-generated learning_requests cannot approve themselves.
        try:
            from .component_learning import observe,run_plan
            receipt['components']['component_learning']=observe(stack,question,result,body)
            receipt['components']['learning_cycle']=run_plan(stack)
        except Exception as exc:
            receipt['errors'].append('component learning: '+type(exc).__name__+': '+str(exc)[:300])
        return receipt
    try:
        ok, reasons = verified(stack, question, result)
        receipt['verified'], receipt['reasons'] = ok, reasons
        pr = (result or {}).get('planner_receipt') or {}
        proposer = proposer_of(result)
        paid = bool((result or {}).get('new_paid_api_calls'))
        if pr.get('route') != 'hosted_verified_sql':  # the hosted ladder's own capture already quarantines
            from .learning import quarantine
            receipt['quarantine'] = quarantine(stack.path, question, (result or {}).get('answer'))
        cascaded = bool(interpretation_of(result).get('proposer'))
        if not ok and cascaded and ((result or {}).get('answer') or {}).get('status') == 'answered' and proposer not in REPLAYS \
                and pr.get('route') != 'hosted_verified_sql':
            receipt['quarantined_pair'] = quarantine_pair(stack, question, result, reasons)
        stab = interpretation_of(result).get('answer_stability')
        if paid and cascaded:  # pass^k on paid answers: the verifier's samples, else the proposer's own internal samples
            from . import answer_stability as S
            if not stab:
                samples = [(result or {}).get('answer') or {}, *S.internal_samples(((result or {}).get('request_contract') or {}).get('legacy_receipt'))]
                stab = S.stability(samples)
            S.record(stack.path, question, proposer, paid, stab)
            receipt['answer_stability'] = stab
        if cascaded:
            from .cascade_router import record_trace
            receipt['router_trace'] = record_trace(question, result, ok)
        if ok:
            if cascaded and proposer not in REPLAYS and pr.get('route') != 'hosted_verified_sql':
                receipt['saved_pair'] = save_pair(stack, question, result, proposer)
            if proposer not in ('semantic_cache', 'learned_verified_sql') or paid:
                receipt['corpus'] = append('labels-traffic', corpus_row(question, result, proposer, paid))
            if cascaded:  # outcome-labelled router data: this tier's checked answer was the verified one
                receipt['router_outcome'] = append('router-outcomes', router_outcome_row(question, result, proposer, paid))
        elif (result or {}).get('answer', {}).get('status') in ('not_understood', 'not_available', 'unsupported', 'partial'):
            receipt['corpus'] = append('gaps', {'question': question, 'status': result['answer']['status'], 'reasons': reasons,
                                                'proposers': [{k: p.get(k) for k in ('proposer', 'outcome', 'detail')}
                                                              for p in interpretation_of(result).get('proposers') or []][:24],
                                                'at': time.strftime('%Y-%m-%dT%H:%M:%S')})
    except Exception as exc:  # noqa: BLE001 - learning bookkeeping never fails an answer
        receipt['errors'].append(f'{type(exc).__name__}: {exc}'[:300])
    try:
        receipt['components'] = components(stack, question, result, body)
    except Exception as exc:  # noqa: BLE001
        receipt['errors'].append(f'components: {type(exc).__name__}: {exc}'[:300])
    return receipt


def after_failure(stack, question, exc):
    """A question nobody could answer: record it as a gap for grammar growth and training negatives. Never raises."""
    if _evaluating(): return None
    try:
        if os.environ.get('AGERE_COMPONENT_LEARNING','off')=='on':
            from .component_learning import observe
            return observe(stack,question,{'answer':{'status':'failed'}})
        usage = getattr(exc, 'planner_receipt', {}) or {}
        return append('gaps', {'question': question, 'status': 'error', 'error': f'{type(exc).__name__}: {exc}'[:500],
                               'proposers': [{k: p.get(k) for k in ('proposer', 'outcome', 'detail')} for p in usage.get('proposers') or []][:24],
                               'at': time.strftime('%Y-%m-%dT%H:%M:%S')})
    except Exception:  # noqa: BLE001
        return None
