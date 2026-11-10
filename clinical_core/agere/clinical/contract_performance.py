"""A versioned contract input joins existing cohort, financial and quality work.

This domain operator uses received payer attribution, integer-cent accounting and
the existing measure library. It does not infer a contract from a program name,
turn allowed amounts into cash, or establish clinical eligibility.
"""
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
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


def load(contract_id):
    if not isinstance(contract_id, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]*', contract_id):
        raise ValueError('A registered contract input id is required')
    target = DIRECTORY / (contract_id + '.json')
    if not target.is_file():
        raise ValueError('Contract inputs are unavailable; supply explicit versioned terms, attribution and period')
    data = json.loads(target.read_text())
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
    return obj({'contract_id': {'type': 'string', 'description': 'Explicit registered input id; no terms are inferred from enrollment.'},
                'contract_digest': {'type': 'string', 'description': 'Optional on first execution; pinned by validation. Changed bindings refuse stale reuse.'}})


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
            'decision_key': digest({'contract_digest': binding, 'as_of': as_of, 'member_months': sorted((pid, month, practice) for (pid, month), practice in members.items()), 'claims': claim_bindings, 'quality': quality}),
            'assumptions': [*contract['assumptions'], *quality.get('warnings', []),
                            'Observed cost drivers are descriptive, not causal intervention savings. Approximate quality is not certified or independent clinical review.'],
            'method': 'Exact payer/member-month attribution and integer-cent claim conservation, registered approximate quality definition and explicitly authored scenario arithmetic; no model calculation.'}


def execute(path, patient_ids, parameters, as_of):
    target = semantic_layer.ensure(path)
    with sqlite3.connect(Path(target).resolve().as_uri() + '?mode=ro', uri=True) as db:
        return run(db, patient_ids, parameters, str(as_of)[:10])


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
        return None
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
