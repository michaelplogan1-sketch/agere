"""Offline, matched-arm diagnostics. No model calls, product imports, or question-bank reads.

Input is an explicitly authored/open_build receipt, not an existing evaluation bank.
Adjudicators supply per-arm verdicts against independently checked received evidence.
Usage: python -m tools.diagnostic_cycle receipt.json --out report.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from pathlib import Path

VERSION = 'matched-diagnostic/1'
ARMS = ('exact_evidence', 'investigator', 'full_stack')
FROZEN = ('candidate_sha', 'data_sha256', 'definitions_sha256', 'scorer_sha256',
          'configuration_sha256', 'evidence_sha256', 'reference_sha256')
STATUSES = {'completed', 'provider_error', 'execution_error', 'not_run'}


def _nonnegative(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f'{name} must be a finite nonnegative number')


def validate(receipt):
    if receipt.get('schema') != VERSION:
        raise ValueError('Unsupported receipt schema')
    manifest = receipt.get('manifest', {})
    for name in FROZEN:
        value = manifest.get(name)
        size = 40 if name == 'candidate_sha' else 64
        if not isinstance(value, str) or len(value) != size or any(c not in '0123456789abcdef' for c in value):
            raise ValueError(f'{name} must be a full immutable hexadecimal digest')
    if not isinstance(manifest.get('as_of'), str) or len(manifest['as_of']) != 10:
        raise ValueError('as_of is required')
    from datetime import date
    date.fromisoformat(manifest['as_of'])
    if manifest.get('cache_policy') not in ('cold', 'warm', 'disabled'):
        raise ValueError('Declare cache policy; do not blend cold and warm runs')
    if not manifest.get('run_id') or not manifest.get('transport'):
        raise ValueError('Declare run_id and transport')
    cases = receipt.get('cases')
    if not isinstance(cases, list) or not cases:
        raise ValueError('At least one case is required')
    ids = set()
    for case in cases:
        cid = case.get('id')
        if not isinstance(cid, str) or not cid or cid in ids:
            raise ValueError('Case ids must be unique nonempty strings')
        ids.add(cid)
        if case.get('origin') not in ('authored', 'open_build'):
            raise ValueError('Only authored/open_build cases are allowed; no CHECK or held-out diagnosis')
        if not case.get('bucket'):
            raise ValueError('Each case must declare its bucket')
        audit = case.get('reference_audit', {})
        if audit.get('state') not in ('verified', 'invalid', 'unresolved'):
            raise ValueError('Reference audit state is required')
        if not audit.get('reviewer') or not audit.get('basis'):
            raise ValueError('Reference reviewer and evidence basis are required')
        if audit.get('state') == 'invalid' and audit.get('issue') not in ('warehouse_inconsistent', 'reference_wrong', 'definition_ambiguous', 'evidence_missing'):
            raise ValueError('An invalid reference must distinguish warehouse, key, definition, or evidence issues')
        if audit.get('state') == 'verified' and audit.get('evidence_sufficient') is not True:
            raise ValueError('A verified reference must establish evidence sufficiency')
        outputs = case.get('arms', {})
        if set(outputs) != set(ARMS):
            raise ValueError('All three arms must be represented, including not_run')
        for arm in ARMS:
            out = outputs[arm]
            if out.get('status') not in STATUSES:
                raise ValueError('Unknown arm status')
            if out.get('correct') is not None and type(out['correct']) is not bool:
                raise ValueError('correct must be a boolean or null')
            if out['status'] != 'completed' and out.get('correct') is not None:
                raise ValueError('An infrastructure failure or unrun arm has no correctness verdict')
            if out.get('correct') is not None and not out.get('adjudicator'):
                raise ValueError('A verdict needs an adjudicator')
            if out['status'] != 'not_run':
                for key in ('model', 'transport', 'output_sha256'):
                    if not out.get(key):
                        raise ValueError(f'Executed arms must identify {key}')
                if out['transport'] != manifest['transport']:
                    raise ValueError('Mixed transports confound the paired comparison')
                if len(out['output_sha256']) != 64 or any(c not in '0123456789abcdef' for c in out['output_sha256']):
                    raise ValueError('Outputs must be hash-bound')
                _nonnegative(out.get('seconds'), 'seconds')
            if out.get('cost_usd') is not None:
                _nonnegative(out['cost_usd'], 'cost_usd')
            if out.get('answered') is not None and type(out['answered']) is not bool:
                raise ValueError('answered must be boolean or null')
    return receipt


def classify(case):
    """A diagnostic signal, not a causal proof from one stochastic trial."""
    ref = case['reference_audit']
    if ref['state'] == 'invalid':
        return {'warehouse_inconsistent': 'warehouse_inconsistent', 'reference_wrong': 'reference_key_wrong',
                'definition_ambiguous': 'definition_ambiguous', 'evidence_missing': 'required_evidence_missing'}[ref['issue']]
    if ref['state'] != 'verified':
        return 'reference_unresolved'
    arms = [case['arms'][a] for a in ARMS]
    if any(a['status'] in ('provider_error', 'execution_error') for a in arms):
        return 'infrastructure_confounded'
    if any(a['status'] != 'completed' or a.get('correct') is None for a in arms):
        return 'incomplete_or_unadjudicated'
    exact, tools, stack = [a['correct'] for a in arms]
    if exact and tools and stack:
        return 'all_correct'
    if not exact and not tools and not stack:
        return 'evidence_reading_or_reasoning'
    if exact and not tools and not stack:
        return 'retrieval_tools_or_planning'
    if exact and tools and not stack:
        return 'cascade_or_acceptance'
    return 'non_monotonic_requires_repetition'


def arm_stats(rows):
    attempted = [r for r in rows if r['status'] != 'not_run']
    completed = [r for r in attempted if r['status'] == 'completed']
    judged = [r for r in completed if type(r.get('correct')) is bool]
    known_cost = [r for r in attempted if r.get('cost_usd') is not None]
    times = sorted(r['seconds'] for r in attempted)
    correct = sum(r['correct'] for r in judged)
    answered = [r for r in judged if r.get('answered') is True]
    missing_cost = len(attempted) - len(known_cost)
    all_judged = bool(rows) and len(judged) == len(rows)
    return {'planned': len(rows), 'attempted': len(attempted), 'completed': len(completed),
            'adjudicated': len(judged), 'correct': correct,
            'overall_correct_fraction': correct / len(rows) if all_judged else None,
            'overall_unavailable_reason': None if all_judged else 'incomplete, unadjudicated, or infrastructure-confounded arms',
            'answered_adjudicated': len(answered),
            'precision_on_adjudicated_answers': sum(r['correct'] for r in answered) / len(answered) if answered else None,
            'provider_errors': sum(r['status'] == 'provider_error' for r in attempted),
            'execution_errors': sum(r['status'] == 'execution_error' for r in attempted),
            'p50_seconds': statistics.median(times) if times else None,
            'p90_seconds': times[max(0, math.ceil(.9 * len(times)) - 1)] if times else None,
            'known_cost_usd': sum(r['cost_usd'] for r in known_cost), 'missing_cost_rows': missing_cost,
            'cost_per_correct_usd': (sum(r['cost_usd'] for r in known_cost) / correct
                                     if correct and all_judged and not missing_cost else None)}


def report(receipt):
    validate(receipt)
    cases = receipt['cases']
    usable = [c for c in cases if c['reference_audit']['state'] == 'verified']
    counts = {}
    for c in cases:
        label = classify(c)
        counts[label] = counts.get(label, 0) + 1
    return {'schema': VERSION, 'manifest': receipt['manifest'], 'reference_cases': len(cases),
            'verified_reference_cases': len(usable), 'class_counts': counts,
            'arms': {a: arm_stats([c['arms'][a] for c in usable]) for a in ARMS},
            'buckets': {b: {a: arm_stats([c['arms'][a] for c in usable if c['bucket'] == b]) for a in ARMS}
                        for b in sorted({c['bucket'] for c in usable})},
            'case_classes': [{'id': c['id'], 'class': classify(c)} for c in cases],
            'limits': ['Single trials suggest failure locations, not causal certainty; repeat discordant outcomes.',
                       'Reference validity is attested by the named reviewer, not established by this receipt validator.',
                       'No result qualifies clinical realism or deployment readiness.']}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('receipt', type=Path)
    p.add_argument('--out', required=True, type=Path)
    args = p.parse_args()
    # This utility intentionally accepts only explicit receipts, never discovers question banks.
    raw = args.receipt.read_bytes()
    result = report(json.loads(raw))
    result['input_receipt_sha256'] = hashlib.sha256(raw).hexdigest()
    args.out.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    print(json.dumps({'output': str(args.out), 'class_counts': result['class_counts']}))


if __name__ == '__main__':
    main()
