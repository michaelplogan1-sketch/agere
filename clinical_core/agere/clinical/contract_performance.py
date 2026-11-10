"""A versioned contract input joins existing cohort, financial and quality work.

This domain operator uses received payer attribution, integer-cent accounting and
the existing measure library. It does not infer a contract from a program name,
turn allowed amounts into cash, or establish clinical eligibility.
"""
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import os
import fcntl
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3

from ...contracts import digest
from . import measures, semantic_layer, vbc

VERSION = 'contract-performance/1'
OP = 'contract_performance'
DIRECTORY = Path(__file__).resolve().parents[3] / 'config/agere/contracts'
PRODUCT = {'population': 'optional', 'view': 'table',
           'use': 'Performance of an explicitly versioned attributed-population contract: observed payer allowed cost, member-months, approximate quality, descriptive drivers and internal review work. Authored demo terms remain labeled.'}
CONTRACT = {'inputs': ['optional earlier_set'],
            'parameters': {'contract_id': 'registered input id', 'contract_digest': 'current input/definition binding'},
            'meaning': PRODUCT['use']}
NATIVE = ('value',)
TEMPLATES = ('Show contract performance for demo-vbc-2029-c3',)


def _input_root():
    value = os.environ.get('AGERE_CONTRACT_INPUTS')
    return Path(value).resolve() if value else None


def _input_data(contract_id):
    if not isinstance(contract_id, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]*', contract_id):
        raise ValueError('A registered contract input id is required')
    target = DIRECTORY / (contract_id + '.json')
    if not target.is_file():
        raise ValueError('Contract inputs are unavailable; supply explicit versioned terms, attribution and period')
    data = json.loads(target.read_text())
    root = _input_root()
    pointer = root / contract_id / 'current.json' if root else None
    if pointer and pointer.is_file():
        active = json.loads(pointer.read_text())
        revision = active['revision']
        if revision != 'base':
            if not re.fullmatch(r'[0-9a-f]{64}', revision):
                raise ValueError('Invalid saved contract revision')
            event = json.loads((pointer.parent / 'versions' / (revision + '.json')).read_text())
            if digest(event) != revision or event['contract_id'] != contract_id:
                raise ValueError('Saved contract revision integrity differs')
            data = event['data']
    return data


def load(contract_id):
    data = _input_data(contract_id)
    if (data.get('schema') != 'agere-contract-performance/1' or data.get('id') != contract_id
            or data.get('authored_synthetic') is not True or type(data.get('version')) is not int):
        raise ValueError('This local route requires a labeled, versioned synthetic contract input')
    from .clinical_events import validate_window
    window = validate_window({'basis': 'source_event', **data['period']})
    if not window['start'] or not window['end']:
        raise ValueError('An explicit contract period is required')
    for field, maximum in [('benchmark_pmpm_cents', None), ('sharing_rate_bps', 10000)]:
        value = data.get(field)
        if type(value) is not int or value < 0 or (maximum is not None and value > maximum):
            raise ValueError('Invalid contract term: ' + field)
    if data['benchmark_pmpm_cents'] == 0:
        raise ValueError('The contract benchmark must be positive')
    q = data['quality']
    if type(q.get('target_bps')) is not int or not 0 <= q['target_bps'] <= 10000:
        raise ValueError('Quality target must be integer basis points')
    definition = measures.library()[q['measure_id']]
    binding = digest({'contract': data, 'quality_definition': definition,
                      'implementation': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    return data, definition, binding


def catalog():
    """Complete current named inputs; no implied selection or payer offer."""
    out = []
    for target in sorted(DIRECTORY.glob('*.json')):
        data, _, binding = load(target.stem)
        root = _input_root()
        versions = []
        if root and (root / target.stem / 'versions').is_dir():
            for p in sorted((root / target.stem / 'versions').glob('*.json')):
                event = json.loads(p.read_text())
                if digest(event) != p.stem:
                    raise ValueError('Contract history integrity differs')
                versions.append({'revision': p.stem, 'version': event['data']['version'], 'reason': event['reason']})
        out.append({'id': data['id'], 'label': data['label'], 'version': data['version'],
                    'authored_synthetic': data['authored_synthetic'], 'payer_id': data['payer_id'],
                    'period': data['period'], 'contract_digest': binding, 'terms': data,
                    'saved_versions': versions, 'editing_available': root is not None,
                    'active_revision': json.loads((root / data['id'] / 'current.json').read_text())['revision']
                        if root and (root / data['id'] / 'current.json').is_file() else 'base'})
    return out


def _publish(path, data, *, immutable=False):
    """Publish complete JSON; never replace an immutable history record."""
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.input-', suffix='.partial', delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(data, stream, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    if immutable:
        try:
            os.link(temporary, path)
        except FileExistsError:
            if json.loads(path.read_text()) != data:
                raise ValueError('Immutable contract event differs')
    else:
        os.replace(temporary, path)
    if temporary.exists():
        temporary.unlink()  # only this freshly authored, successfully published temporary
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def save_input(request):
    """Version authored demo terms with optimistic concurrency and exact rollback.

    No source warehouse, packaged rule, old answer or work row is overwritten.
    The server's configured data directory is never supplied by the request.
    """
    if (not isinstance(request, dict) or set(request) != {'contract_id', 'expected_digest', 'changes', 'restore_revision', 'reason'}
            or not isinstance(request['reason'], str) or not request['reason'].strip()):
        raise ValueError('Exact contract, current binding, changes or restore revision, and reason required')
    root = _input_root()
    if root is None:
        raise ValueError('Contract editing requires explicitly configured runtime input storage')
    ident = request['contract_id']
    load(ident)  # validate ID before creating any path
    folder = root / ident
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / '.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current, _, binding = load(ident)
        if binding != request['expected_digest']:
            raise ValueError('Contract inputs changed in another window; reload before saving')
        changes, restore = request['changes'], request['restore_revision']
        if not isinstance(changes, dict) or bool(changes) == bool(restore):
            raise ValueError('Choose a term revision or restore a saved revision')
        versions = folder / 'versions'
        events = folder / 'events'
        versions.mkdir(exist_ok=True)
        events.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat()
        if restore:
            if restore != 'base':
                if not isinstance(restore, str) or not re.fullmatch(r'[0-9a-f]{64}', restore):
                    raise ValueError('A saved revision or base is required')
                event = json.loads((versions / (restore + '.json')).read_text())
                if digest(event) != restore or event['contract_id'] != ident:
                    raise ValueError('Saved revision integrity differs')
            revision = restore
        else:
            allowed = {'benchmark_pmpm_cents', 'sharing_rate_bps', 'quality_target_bps'}
            if set(changes) - allowed or any(type(v) is not int for v in changes.values()):
                raise ValueError('Editable demo terms are integer benchmark cents, sharing basis points and quality-target basis points')
            data = json.loads(json.dumps(current))
            prior_versions = [json.loads(p.read_text())['data']['version'] for p in versions.glob('*.json')]
            data['version'] = max([current['version'], *prior_versions]) + 1
            for key, value in changes.items():
                if key == 'quality_target_bps':
                    if not 0 <= value <= 10000:
                        raise ValueError('Quality target must be between zero and one hundred percent')
                    data['quality']['target_bps'] = value
                else:
                    if value < 0 or (key == 'sharing_rate_bps' and value > 10000) or (key == 'benchmark_pmpm_cents' and value == 0):
                        raise ValueError('Invalid authored demo financial term')
                    data[key] = value
            event = {'schema': 'authored-contract-revision/1', 'contract_id': ident, 'data': data,
                     'parent_digest': binding, 'reason': request['reason'].strip(), 'created_at': stamp,
                     'source_facts_established': False, 'authored_synthetic': True}
            revision = digest(event)
            _publish(versions / (revision + '.json'), event, immutable=True)
        activation = {'schema': 'authored-contract-activation/1', 'contract_id': ident, 'revision': revision,
                      'previous_digest': binding, 'reason': request['reason'].strip(), 'created_at': stamp}
        _publish(events / (digest(activation) + '.json'), activation, immutable=True)
        _publish(folder / 'current.json', {'revision': revision, 'activation': digest(activation)})
        data, _, active = load(ident)
    return {'status': 'saved', 'contract_id': ident, 'revision': revision, 'version': data['version'],
            'contract_digest': active, 'authored_synthetic': True, 'source_facts_established': False,
            'prior_answers_preserved': True, 'dependent_work_requires_current_binding': True,
            'question': 'Show contract performance for ' + ident}


def validate(parameters):
    if not isinstance(parameters, dict) or set(parameters) - {'contract_id', 'contract_digest'}:
        raise ValueError('Contract performance needs contract_id and optional current contract_digest')
    data, _, binding = load(parameters.get('contract_id'))
    if parameters.get('contract_digest', binding) != binding:
        raise ValueError('Contract terms, quality definition or calculation changed; recompute this decision before reuse or work')
    return {'contract_id': data['id'], 'contract_digest': binding}


def premise_issues(parameters):
    if not isinstance(parameters, dict) or not parameters.get('contract_digest'):
        return ['Contract input has no saved version binding; recompute before reuse or work']
    try:
        validate(parameters)
    except (ValueError, KeyError, OSError) as exc:
        return [str(exc)]
    return []


def schema(obj, string, array):
    available = catalog()
    out = obj({'contract_id': {'type': 'string', 'enum': [r['id'] for r in available],
                              'description': 'Choose explicitly; these labeled synthetic terms are not received payer offers.'},
               'contract_digest': {'type': 'string', 'description': 'Pinned by validation; changed bindings refuse stale reuse.'}})
    out['required'] = ['contract_id']
    out['description'] = json.dumps({'registered_inputs': available, 'selection': 'Ask which input the user intends when it is not explicit; never silently use a demo contract as their actual contract.'})
    return out


def executable_evidence(parameters):
    p = validate(parameters)
    data, _, _ = load(p['contract_id'])
    return {'contract_input': data, 'outputs': ['patient_id', 'recorded_member_months', 'observed_allowed_cents',
            'approximate_quality', 'monthly_cost', 'practice_drivers', 'review_needed'],
            'claims': 'payer_claim counts_in_metrics = 1; same payer and attributed patient/member month; no cash or causal savings inference'}


def _cents(value):
    return int(Decimal(value).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def run(db, patient_ids, parameters, as_of):
    p = validate(parameters)
    contract, definition, binding = load(p['contract_id'])
    start, end = contract['period']['start'], contract['period']['end']
    if end > as_of:
        raise ValueError('The contract period extends beyond the received snapshot; choose a complete observed period')
    keep = None if patient_ids is None else set(patient_ids)
    # The schema contains one governed row per patient/month. Preserve the
    # actual recorded payer and practice rather than assigning an annual PCP.
    members, sources = {}, defaultdict(set)
    for pid, month, practice, rid in db.execute(
            'SELECT patient_id, substr(month,1,7), attribution_practice_id, record_id FROM member_month '
            'WHERE payer_id=? AND substr(month,1,7) BETWEEN ? AND ? AND attribution_practice_id IS NOT NULL',
            (contract['payer_id'], start[:7], end[:7])):
        if keep is not None and pid not in keep:
            continue
        key = (pid, month)
        if key in members and members[key] != practice:
            raise ValueError('Conflicting practice attribution for a member-month; resolve source before calculation')
        members[key] = practice
        sources[pid].add(rid)
    people_months = defaultdict(set)
    for pid, month in members:
        people_months[pid].add(month)
    rows = {pid: {'patient_id': pid, 'recorded_member_months': len(months),
                 'known_allowed_subtotal_cents': 0, 'included_claims': 0, 'missing_amount_claims': 0,
                 'practice_ids': sorted({members[(pid, m)] for m in months})}
            for pid, months in sorted(people_months.items())}
    monthly = {m: {'month': m, 'recorded_member_months': 0, 'known_allowed_subtotal_cents': 0,
                   'included_claims': 0, 'missing_amount_claims': 0} for m in vbc._months(start, end)}
    practices = defaultdict(lambda: {'recorded_member_months': 0, 'known_allowed_subtotal_cents': 0,
                                     'included_claims': 0, 'missing_amount_claims': 0})
    for (pid, month), practice in members.items():
        monthly[month]['recorded_member_months'] += 1
        practices[practice]['recorded_member_months'] += 1
    excluded = Counter()
    claim_bindings = []
    query = ('SELECT payer_claim_id,patient_id,service_from,allowed_cents,counts_in_metrics,last_available_date,record_id '
             'FROM payer_claim WHERE payer_id=? AND service_from BETWEEN ? AND ? ORDER BY payer_claim_id')
    for cid, pid, day, cents, countable, available, rid in db.execute(query, (contract['payer_id'], start, end)):
        if keep is not None and pid not in keep:
            continue
        if countable != 1:
            excluded['superseded_or_nonmetric_claim'] += 1
            continue
        key = (pid, day[:7])
        if key not in members:
            excluded['no_same_payer_attributed_member_month'] += 1
            continue
        if not available or available[:10] > as_of:
            excluded['not_observed_by_snapshot'] += 1
            continue
        targets = [rows[pid], monthly[day[:7]], practices[members[key]]]
        for target in targets:
            target['included_claims'] += 1
            if type(cents) is int:
                target['known_allowed_subtotal_cents'] += cents
            else:
                target['missing_amount_claims'] += 1
        sources[pid].add(rid)
        claim_bindings.append({'claim_id': cid, 'patient_id': pid, 'member_month': day[:7],
                               'practice_id': members[key], 'record_id': rid, 'allowed_cents': cents})
    def finish(row):
        mm = row['recorded_member_months']
        row['observed_allowed_cents'] = None if row['missing_amount_claims'] else row['known_allowed_subtotal_cents']
        row['benchmark_cents'] = mm * contract['benchmark_pmpm_cents']
        row['observed_pmpm_cents'] = None if not mm or row['observed_allowed_cents'] is None else row['observed_allowed_cents'] / mm
        row['observed_variance_cents'] = None if row['observed_allowed_cents'] is None else row['benchmark_cents'] - row['observed_allowed_cents']
        return row
    total = finish({key: sum(row[key] for row in rows.values()) for key in
                    ('recorded_member_months', 'known_allowed_subtotal_cents', 'included_claims', 'missing_amount_claims')})
    for row in [*rows.values(), *monthly.values(), *practices.values()]:
        finish(row)
    # Keep the existing measure implementation and its missing/uncertain source
    # semantics. Override only enrollment with this contract's exact months.
    people = vbc.eligibility(db, end, start, end)
    people = {pid: {**people[pid], 'months': sorted(months), 'enrollment_basis': 'contract_recorded_member_months'}
              for pid, months in people_months.items() if pid in people}
    quality = measures.evaluate_db(db, definition, start, end, as_of, keep=set(rows), people=people,
                                   output='patients', top=0)
    assessments = {r['patient_id']: r for r in quality.get('patient_rows', [])}
    for pid, row in rows.items():
        assessment = assessments.get(pid)
        row['quality_assessment'] = assessment or {'assessment': 'outside_known_measure_denominator_or_unavailable', 'met': None}
        reasons = []
        if row['missing_amount_claims']:
            reasons.append('Resolve missing received claim amounts')
        if row['observed_variance_cents'] is not None and row['observed_variance_cents'] < 0:
            reasons.append('Review descriptive cost above the authored member benchmark')
        if assessment and assessment.get('met') is False and assessment.get('excluded') is False:
            reasons.append('Review the approximate measure evidence; clinical appropriateness is unestablished')
        if quality.get('declined') or (assessment and assessment.get('assessment') == 'indeterminate'):
            reasons.append('Resolve missing or uncertain quality inputs')
        row['review_needed'] = bool(reasons)
        row['review_reasons'] = reasons
        row['source_record_ids'] = sorted(x for x in sources[pid] if x is not None)
    overall = quality.get('overall') or {}
    den, num = overall.get('denominator'), overall.get('numerator')
    quality_known = (not quality.get('declined') and type(den) is int and den > 0
                     and type(num) is int and not quality.get('unresolved_evidence'))
    target_met = (num * 10000 >= den * contract['quality']['target_bps']) if quality_known else None
    payout = None
    if target_met is not None and total['observed_variance_cents'] is not None and total['recorded_member_months']:
        payout = _cents(Decimal(max(0, total['observed_variance_cents'])) * contract['sharing_rate_bps'] / 10000) if target_met else 0
    total.update(members=len(rows), approximate_quality_target_met=target_met,
                 illustrative_provider_share_cents=payout,
                 illustrative_payer_retained_positive_variance_cents=None if payout is None else max(0, total['observed_variance_cents']) - payout,
                 settlement_established=False)
    assert sum(r['known_allowed_subtotal_cents'] for r in monthly.values()) == total['known_allowed_subtotal_cents']
    assert sum(r['known_allowed_subtotal_cents'] for r in practices.values()) == total['known_allowed_subtotal_cents']
    return {'schema': VERSION, 'parameters': p, 'contract': contract, 'contract_digest': binding,
            'period': contract['period'], 'source_snapshot_as_of': as_of, 'summary': total,
            'table': list(rows.values()), 'patient_ids': sorted(rows), 'monthly': list(monthly.values()),
            'practice_drivers': [{'practice_id': key, **row} for key, row in sorted(practices.items())],
            'excluded_claim_counts': dict(excluded), 'claim_bindings': claim_bindings,
            'quality': quality, 'quality_definition_digest': digest(definition),
            'input_history': next(row['saved_versions'] for row in catalog() if row['id'] == contract['id']),
            'editing_available': _input_root() is not None,
            'decision_key': digest({'contract_digest': binding, 'as_of': as_of, 'member_months': sorted((pid, month, practice) for (pid, month), practice in members.items()), 'claims': claim_bindings, 'quality': quality}),
            'assumptions': [*contract['assumptions'], *quality.get('warnings', []),
                            'Observed cost drivers are descriptive, not causal intervention savings. Approximate quality is not certified or independent clinical review.'],
            'method': 'Exact payer/member-month attribution and integer-cent claim conservation, registered approximate quality definition and explicitly authored scenario arithmetic; no model calculation.'}


def execute(path, patient_ids, parameters, as_of):
    from ..deliverables.answer_context import source_identity
    from .answer_dependencies import wal_identity
    observation = {'source': source_identity(path), 'wal': wal_identity(path)}
    target = semantic_layer.ensure(path)
    with sqlite3.connect(Path(target).resolve().as_uri() + '?mode=ro', uri=True) as db:
        report = run(db, patient_ids, parameters, str(as_of)[:10])
    if observation != {'source': source_identity(path), 'wal': wal_identity(path)}:
        raise ValueError('Observed source changed during contract calculation')
    if report['contract_digest'] != load(report['parameters']['contract_id'])[2]:
        raise ValueError('Contract inputs changed during calculation; recompute before saving')
    report['observation_binding'] = observation
    return report


def answer(report, plan, question):
    s = report['summary']
    partial = (plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
               or not s['members'] or s['missing_amount_claims'] or s['approximate_quality_target_met'] is None)
    summary = (f"Synthetic demo contract {report['contract']['id']}: {s['members']} recorded attributed members, "
               f"{s['recorded_member_months']} member-months and {s['known_allowed_subtotal_cents']} known allowed cents. "
               'Payer and provider views share this cohort, ledger and quality definition. Terms are authored assumptions; no payment or clinical qualification is established.')
    return {'status': 'partial' if partial else 'answered', 'answer_type': 'table', 'table': report['table'],
            'count': len(report['table']), 'patient_ids': report['patient_ids'], 'membership_role': 'evaluated',
            'value': summary, 'method': report['method'], 'limitations': report['assumptions'],
            'definitions': [{'term': 'contract input', 'basis': report['contract']}],
            'overall': {OP: {key: value for key, value in report.items() if key not in ('table', 'patient_ids', 'claim_bindings')}}}


def coverage_state(path, question, graph, result):
    """A source-bound, complete field/role proof for coverage judgment only.

    The full native result must match its persisted execution, current observed
    source and versioned input. Models need not recheck every numeric cell to
    judge a fully consumed named-report request. Free-text or inherited graph
    compositions retain their ordinary complete-evidence path.
    """
    expected = registered_input_command(question, None)
    nodes = (graph or {}).get('nodes') or []
    if expected is None or len(nodes) != 1 or graph != result.get('graph'):
        return None
    if nodes[0]['parameters'] != expected['nodes'][0]['parameters'] or result.get('question') != question:
        raise ValueError('Contract coverage differs from the named current input request')
    with sqlite3.connect((Path(path) / 'stack.sqlite3').resolve().as_uri() + '?mode=ro', uri=True) as db:
        row = db.execute('SELECT payload FROM result_cache WHERE id=?', (result.get('result_id'),)).fetchone()
    if row is None:
        return None
    saved = json.loads(row[0])
    if saved.get('graph') != graph or saved.get('question') != question or saved.get('results') != result.get('results'):
        raise ValueError('Contract native execution changed before coverage judgment')
    current_answer = result.get('answer') or {}
    native_answer = saved.get('answer') or {}
    for key in ('table', 'patient_ids', 'overall', 'status', 'answer_type', 'count', 'method', 'headline'):
        if current_answer.get(key) != native_answer.get(key):
            raise ValueError('Contract answer changed before coverage judgment: ' + key)
    report = saved['results'][0]['result']
    from ..deliverables.answer_context import source_identity
    from .answer_dependencies import wal_identity
    observation = {'source': source_identity(path), 'wal': wal_identity(path)}
    if report.get('observation_binding') != observation:
        raise ValueError('Contract source changed before coverage judgment')
    contract, definition, binding = load(report['parameters']['contract_id'])
    if binding != report['contract_digest']:
        raise ValueError('Contract input changed before coverage judgment')
    rows = report['table']
    return {'schema': 'native-contract-coverage/1', 'original_question': question,
            'status': current_answer['status'], 'answer_type': current_answer['answer_type'],
            'contract': contract, 'contract_digest': binding, 'quality_definition': definition,
            'summary': report['summary'], 'rows': len(rows),
            'columns': sorted({key for row in rows for key in row}),
            'complete_native_result_digest': digest(saved), 'table_digest': digest(rows),
            'observation_binding': observation,
            'retained_collections': {'all_members': len(rows), 'all_months': len(report['monthly']),
                'all_practices': len(report['practice_drivers']), 'all_included_claim_bindings': len(report['claim_bindings']),
                'review_needed_members': sum(bool(row['review_needed']) for row in rows)},
            'uncertainty': {'missing_amount_claims': report['summary']['missing_amount_claims'],
                'quality_declined': report['quality'].get('declined'),
                'missing_quality_inputs': report['quality'].get('missing_inputs', []),
                'excluded_claim_counts': report['excluded_claim_counts'],
                'quality_assessments': dict(Counter(str(row['quality_assessment'].get('assessment') or
                    ('excluded' if row['quality_assessment'].get('excluded') else row['quality_assessment'].get('met'))) for row in rows))},
            'field_roles': {'population': contract['population_rule'], 'money': contract['amount_basis'],
                'quality': 'The complete registered approximate definition above is a synthetic demo rule, not certified clinical quality.',
                'drivers': 'Complete monthly and practice cost/exposure partitions; descriptive, not causal.',
                'work': 'Every member retains source IDs and review reasons; internal evidence review does not authorize care.'},
            'limitations': report['assumptions'], 'clinical_semantic_acceptance': False,
            'judgment_scope': 'Whether these complete executed fields, scope and explicit unknowns cover the named report request. Native integrity is checked in code; no model row sampling, numeric recalculation or clinical certification.'}


def route(question, profile, as_of):
    match = re.fullmatch(r'\s*(?:show|review|analyze|analyse) (?:the )?contract performance for ([a-z0-9-]+)[?.]?\s*', str(question), re.I)
    if not match:
        return None
    try:
        p = validate({'contract_id': match[1].lower()})
    except (ValueError, KeyError):
        return None
    from .engine_suite import plan
    return plan(OP, p, 'One bound contract-performance decision with payer/provider projections and internal review needs', question)


def requested_population(question):
    """Only a fully consumed request for a real registered input binds scope."""
    proposed = route(question, None, None)
    if proposed is None:
        if input_request(question) is None:
            return None
        return {'living_only': False, 'record_subject_scope': True, 'unresolved_registered_input': True,
                'reason': 'Contract performance is a financial source report; its required versioned input must be selected before execution.'}
    return {'living_only': False, 'record_subject_scope': True,
            'registered_input_scope': proposed[0]['nodes'][0]['parameters'],
            'reason': 'The named versioned input defines retrospective recorded membership; present survival does not remove its financial subjects. Internal evidence review is separate from care eligibility.'}


def registered_input_command(question, as_of):
    """The named-input command is authored software, not free plan generation.

    It consumes the entire request, resolves a concrete versioned input, pins its
    definition, and still passes normal execution/source/output/action guards.
    Extra predicates and unknown inputs defer to ordinary interpretation.
    """
    proposed = route(question, None, as_of)
    return None if proposed is None else proposed[0]


def command_for_input(parameters):
    return 'Show contract performance for ' + validate(parameters)['contract_id']


def input_request(question):
    if not re.fullmatch(r'\s*(?:show|review|analyze|analyse) (?:the )?contract performance[?.]?\s*', str(question), re.I):
        return None
    inputs = catalog()
    return {'operation': OP, 'bindings': {row['id']: {'contract_id': row['id'], 'contract_digest': row['contract_digest']} for row in inputs},
            'request': {'slot': 'registered_contract_input', 'question': 'Which contract input should this analysis use?',
                'reason': 'A payer enrollment record does not establish your contract terms. The available demo is an authored synthetic scenario; it must be chosen explicitly.',
                'options': [{'id': row['id'], 'label': row['label'] + ' · version ' + str(row['version']),
                             'meaning': row['id'] + ': ' + row['payer_id'] + ', ' + row['period']['start'] + ' to ' + row['period']['end'] + '. Authored demo terms, not a received payer offer.'} for row in inputs]
                           + ([{'id': 'inspect_requirements', 'label': 'Inspect required inputs', 'meaning': 'Keep the analysis unresolved and show the missing input requirements.'}] if not inputs else [])
                           + [{'id': 'different_inputs', 'label': 'Use different contract inputs', 'meaning': 'Keep the analysis unresolved until explicit versioned terms, payer, population and period are supplied.'}]}}
