"""Execution of the question spec's two additions: note predicates (System One reads the evidence) and a single-patient note answer.

The compiler (request_plan) leaves a three-valued `note_pred(patient_id, 'nK')` in the SQL. Here, before the SQL runs:

  1. the patients a note predicate can still change are found with SQL alone: every note predicate is NULL, and the patients whose
     WHERE comes out NULL are the candidates (a patient the structured part already rules out is never read; a patient it already
     rules in is never read either);
  2. the registered reader (`note_frames.evaluate`, builder A) reads those patients' evidence once per predicate, predicates in order,
     each later predicate only over patients the earlier ones left undecided or true;
  3. each reading becomes true (status in the predicate's statuses), false, or NULL (undecided, never silently "no");
  4. the three-valued results are written into the SQL as CASE expressions, so AND / OR / NOT follow SQL's logic and
     `unknown_membership_query` returns exactly the patients whose answer the notes could not settle.

The SQL that results is read-only and carries no model output except those patient-id sets. A missing reader leaves every candidate undecided.
"""
import contextlib
import json
import re
import time

from . import request_plan

VERSION = 'spec-exec/1'
UNDECIDED = (None, '', 'unknown', 'undecided', 'insufficient', 'error', 'unavailable')
NULL = 'CAST(NULL AS INTEGER)'


@contextlib.contextmanager
def ledger_for(stack):
    """The budget ledger a note reading may charge (live stacks only; None otherwise)."""
    if not getattr(stack, 'allow_live', False):
        yield None
        return
    from ..gateway import BudgetLedger, default_ledger_path
    ledger = BudgetLedger(default_ledger_path())
    try:
        yield ledger
    finally:
        ledger.close()


def case_sql(pid, true_ids, unknown_ids):
    """The 1 / NULL / 0 value of one note predicate for `pid` (a patient_id column expression)."""
    parts = []
    if true_ids: parts.append(f'WHEN {pid} IN (SELECT value FROM json_each({request_plan.literal(json.dumps(sorted(true_ids)))})) THEN 1')
    if unknown_ids: parts.append(f'WHEN {pid} IN (SELECT value FROM json_each({request_plan.literal(json.dumps(sorted(unknown_ids)))})) THEN NULL')
    return f'(CASE {" ".join(parts)} ELSE 0 END)' if parts else '0'


def substitute(sql, bound):
    """sql with each note_pred(...) replaced by its bound result, or NULL for a predicate not read (yet)."""
    if not sql: return sql
    def one(m):
        got = bound.get(m.group(2))
        return NULL if got is None else case_sql(m.group(1), got['true'], got['unknown'])
    return request_plan.NOTE_PRED.sub(one, sql)


def holds(predicate, reading):
    """True / False / None for one patient's reading against the predicate: status in statuses, details not contradicted."""
    if not isinstance(reading, dict): return None
    if reading.get('holds') is not None or 'holds' in reading: return reading['holds']  # the note reader's own verdict (True / False / None = undecided)
    status = reading.get('status')
    if status in UNDECIDED: return None
    if status not in predicate.get('statuses', []): return False
    wanted, got = predicate.get('detail') or {}, reading.get('detail') or {}
    for key, options in wanted.items():
        if key not in got: continue  # the reader applied the detail itself; nothing to contradict
        options = options if isinstance(options, list) else [options]
        values = got[key] if isinstance(got[key], list) else [got[key]]
        if not set(values) & set(options): return False
    return True


def evaluate_one(stack, predicate, ids, *, as_of, ledger, evaluate):
    """-> ({pid: reading}, receipt). A reader that raises or is absent leaves every patient undecided."""
    started = time.perf_counter()
    if not ids: return {}, {'available': evaluate is not None, 'patients': 0, 'skipped': 'structured or earlier predicates already settle this subject'}
    if evaluate is None:
        return {}, {'available': False, 'reason': 'the note reader (note_frames.evaluate) is not installed', 'patients': len(ids)}
    try:
        out = evaluate(stack, predicate, list(ids), as_of=as_of, ledger=ledger)
    except Exception as exc:  # noqa: BLE001 - undecided, never a guess
        return {}, {'available': True, 'error': f'{type(exc).__name__}: {str(exc)[:200]}', 'patients': len(ids),
                    'seconds': round(time.perf_counter() - started, 3)}
    return (out if isinstance(out, dict) else {}), {'available': True, 'patients': len(ids), 'seconds': round(time.perf_counter() - started, 3)}


def patient_ids(stack, sql, population, target):
    from . import sql_query
    if not sql: return []
    return sql_query.execute(stack.path, {'query': sql, 'returns': 'patient_set'}, patient_ids=population, target=target)['patient_ids']


def bind(stack, contract, *, population=None, target=None, as_of=None, ledger=None, evaluate=None):
    """-> {'contract': the contract with its note predicates filled (query, membership_query, unknown_membership_query rewritten),
    'readings': {predicate id: {pid: reading}}, 'receipt': {...}}. The original contract is not changed."""
    predicates = contract.get('note_predicates') or {}
    if not predicates: return {'contract': contract, 'readings': {}, 'receipt': {'version': VERSION, 'predicates': 0}}
    as_of = as_of or (contract.get('source_identity') or {}).get('as_of')
    evaluate = evaluate if evaluate is not None else request_plan.executor('note_frames.evaluate')
    bound, readings, condition_readings, steps = {}, {}, {}, []
    for ident in sorted(predicates, key=lambda k: int(k[1:])):
        predicate = predicates[ident]
        candidates = patient_ids(stack, substitute(contract.get('unknown_membership_query'), bound), population, target)
        read, receipt = evaluate_one(stack, predicate, candidates, as_of=as_of, ledger=ledger, evaluate=evaluate)
        true, unknown = set(), set()
        for pid in candidates:
            verdict = holds(predicate, read.get(pid))
            if verdict is True: true.add(pid)
            elif verdict is None: unknown.add(pid)
        condition_readings[ident] = read
        bound[ident] = {'true': true, 'unknown': unknown}
        readings[ident] = {pid: read[pid] for pid in true if pid in read}
        steps.append({'predicate': ident, 'frame': predicate['frame'], 'candidates': len(candidates), 'true': len(true),
                      'undecided': len(unknown), 'false': len(candidates) - len(true) - len(unknown), **receipt})
    bound_contract = {**contract}
    for key in ('query', 'membership_query', 'unknown_membership_query', 'coverage_query'):
        if bound_contract.get(key): bound_contract[key] = substitute(bound_contract[key], bound)
    return {'contract': bound_contract, 'readings': readings, 'condition_readings': condition_readings, 'receipt': {'version': VERSION, 'predicates': len(predicates), 'steps': steps}}


def evidence_of(readings, ids, limit=200):
    """Per-patient note evidence (status, detail, passages with record id, date, note type and quote) for the patients an answer names."""
    out = {}
    for ident, by_patient in readings.items():
        for pid in ids:
            if pid in by_patient and len(out) < limit:
                out.setdefault(pid, {})[ident] = {k: by_patient[pid].get(k) for k in ('status', 'detail', 'evidence', 'p')}
    return out


def range_note(answer, undecided, readings_receipt):
    """An undecided patient makes a count a range (decision_context: unknown is never silently no)."""
    if not undecided: return answer
    answer['unknown_count'] = max(answer.get('unknown_count') or 0, len(undecided))
    value = answer.get('count')
    if isinstance(value, int) and not isinstance(value, bool):
        answer['count_range'] = {'low': value, 'high': value + len(undecided)}
        answer['limitations'] = [*(answer.get('limitations') or []),
                                 f'{len(undecided)} patients could not be decided from the notes; the count is between {value} and {value + len(undecided)}.']
    else:
        answer['limitations'] = [*(answer.get('limitations') or []), f'{len(undecided)} patients could not be decided from the notes and are not in this result.']
    if answer.get('status') == 'answered': answer['status'] = 'partial'
    answer['note_reading'] = readings_receipt
    return answer


def compound_note_answer(stack, contract, *, as_of, ledger, question):
    """Execute the compiled three-valued condition; never flatten AND/OR/NOT.

    An open detail request needs the detail renderer and cannot be replaced by
    a yes/no membership test. Declining this adapter leaves the original request
    available to the next checked proposer.
    """
    from . import chart_queries as Q
    if (not re.match(r'(?i)^\s*(?:is|are|was|were|do|does|did|has|have|had|can|could)\b', question or '')
            or re.search(r'(?i)\b(?:what|why|when|where|how)\b', question or '')):
        raise request_plan.PlanError('A compound note detail request needs its full detail output; a Boolean answer would omit it')
    import inspect
    evaluate = request_plan.executor('note_frames.evaluate')
    if evaluate is not None:
        fn = evaluate
        accepts_live = 'allow_live' in inspect.signature(fn).parameters
        def evaluate(stack_, predicate, ids, *, as_of, ledger):
            kw = {'as_of': as_of, 'ledger': ledger}
            if accepts_live: kw['allow_live'] = bool(getattr(stack_, 'allow_live', False))
            return fn(stack_, predicate, ids, **kw)
    catalog = request_plan.Catalog.load(stack.path)
    population = contract.get('population')
    result = bind(stack, contract, population=population, target=catalog.target, as_of=as_of, ledger=ledger, evaluate=evaluate)
    compiled = result['contract']
    if not compiled.get('membership_query') or not compiled.get('unknown_membership_query'):
        raise request_plan.PlanError('The combined note condition has no complete executable membership contract')
    matches = patient_ids(stack, compiled.get('membership_query'), population, catalog.target)
    unknown = patient_ids(stack, compiled.get('unknown_membership_query'), population, catalog.target)
    pid = contract['subject']['patient_id']
    truth = True if pid in matches else None if pid in unknown else False
    sources, rows, seen = [], [], set()
    for ident, patients in result.get('condition_readings', {}).items():
        reading = patients.get(pid)
        if not reading: continue
        pred = contract['note_predicates'][ident]
        rows.append({'criterion': pred['concept']['term'], 'status': reading.get('status'),
                     'matches': holds(pred, reading), 'detail': reading.get('detail') or {}})
        for ev in reading.get('evidence') or []:
            key = (ev.get('record_id'), ev.get('quote'))
            if key in seen: continue
            seen.add(key)
            sources.append(Q.src(ev.get('note_type') or 'clinical_note', ev.get('record_id'), ev.get('date'), quote=ev.get('quote')))
    headline = ('Yes. The requested combination is supported' if truth is True else
                'No. The requested combination is not supported' if truth is False else
                'The requested combination remains unresolved') + ' for ' + pid + '.'
    return {'status': 'answered' if truth is not None else 'partial', 'answer_type': 'yes_no',
            'value': truth, 'yes_no': truth, 'headline': headline, 'table': rows, 'sources': sources,
            'patient_id': pid, 'patient_ids': [pid], 'unknown_count': int(truth is None),
            'missing': ['The combined condition cannot be settled from the available evidence.'] if truth is None else [],
            'limitations': [], 'actionable_cohort': False, 'data_absence_established': False,
            'note_reading': result['receipt'], 'condition_evaluation': {'truth': truth, 'compiled_request_id': contract['id']}}


def note_answer(stack, contract, *, as_of=None, ledger=None, answer=None, question=None):
    """A single patient's note answer through the chart reader (builder C). -> the answer contract, or an honest 'not available'."""
    as_of = as_of or (contract.get('source_identity') or {}).get('as_of')
    request = dict(contract['request'])
    where = request.get('where')
    if contract.get('note_predicates') and isinstance(where, dict) and where.get('op') != 'note':
        return compound_note_answer(stack, contract, as_of=as_of, ledger=ledger,
                                    question=question or contract.get('original_question') or contract.get('question') or '')
    answer = answer or request_plan.executor('chart_reader.answer')
    if answer is None:
        return {'status': 'not_available', 'answer_type': 'unsupported', 'headline': 'The chart reader is not installed in this build.',
                'missing': ['chart_reader.answer is not registered'], 'patient_ids': [], 'actionable_cohort': False,
                'data_absence_established': False, 'limitations': ['No note answer was read; nothing was guessed.']}
    # The chart reader executes note predicates from spec['note']; a spec writes them in where ({"op": "note", ...}, possibly under and/or).
    # Hand them over, with the subject and the question, so the reader executes the spec instead of re-reading the question text (dev3 2026-10-24:
    # every valid note spec came back "domain, intent left open").
    if not request.get('note'):
        found = []

        def collect(node):
            if isinstance(node, dict):
                if node.get('op') == 'note': found.append(node); return
                for v in node.values(): collect(v)
            elif isinstance(node, list):
                for v in node: collect(v)
        collect(request.get('where'))
        if found: request['note'] = found
    request.setdefault('subject', contract.get('subject'))
    request.setdefault('question', question or contract.get('question') or '')
    import inspect
    extra = {'question': request.get('question') or None} if 'question' in inspect.signature(answer).parameters else {}
    out = answer(stack, request, as_of=as_of, ledger=ledger, **extra)
    return out if isinstance(out, dict) else {'status': 'not_available', 'answer_type': 'unsupported', 'headline': 'The chart reader returned no answer.',
                                              'missing': ['empty chart reader answer'], 'patient_ids': [], 'actionable_cohort': False,
                                              'data_absence_established': False, 'limitations': []}
