"""Reproduce an explicitly named local study's results without model inference.

Reads only its approved manifest and completed receipts. Model outputs are graded
again on the original source variants; verification results never select training
examples or activate a release. Missing or changed evidence fails the review.
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

from clinical_core.contracts import digest
from clinical_core.agere.clinical import adapter_learning as A, component_learning as C
from tools.training import adapter_tasks as T, adapters as D


def outcomes(details):
    # A query can be rejected on one source and wrong on another. Keep that
    # overlap visible, but never count it twice in the outcome partition.
    counts = Counter('accepted_wrong' if r['guarded_wrong'] else
                     'acceptable' if r['acceptable'] else 'declined' for r in details)
    return {k: counts[k] for k in ('acceptable', 'accepted_wrong', 'declined')}


def review_round(manifest, receipt, *, stack):
    spec, by, dataset = T.load(manifest)
    saved = json.loads(C.checked_path(receipt).read_text())
    qualification = saved['qualification']
    learner = spec['learner']
    if (qualification['dataset'] != dataset or qualification['learner'] != learner or
            qualification['contract'] != A.contract(learner)):
        raise ValueError('Study inputs or implementation differ from the completed round')
    ctx = T.Context(stack)
    variants = [T.Context(C.checked_path(Path(manifest).parent / v['path']))
                for v in spec.get('source_variants', [])]
    if (ctx.identity != qualification['source_identity'] or
            [v.identity for v in variants] != [v['identity'] for v in qualification['source_variants']]):
        raise ValueError('Study source or variant changed')
    comparisons = qualification['comparisons']
    if not {'baseline', 'candidate'} <= comparisons.keys():
        raise ValueError('Both baseline and candidate comparisons are required')
    summaries = {}
    fresh = {}
    with D.offline() as guard:
        for name, splits in comparisons.items():
            fresh[name] = {}
            summaries[name] = {}
            for split in ('calibration', 'verification'):
                stored = splits[split]
                expected = [digest([r['question'], r['family']]) for r in by[split]]
                if ([r['task'] for r in stored['details']] != expected or
                        [r['family'] for r in stored['details']] != [r['family'] for r in by[split]]):
                    raise ValueError('Comparison task identities/order differ from the immutable split')
                outputs = {r['task']: r['output'] for r in stored['details']}
                result = D.evaluate(ctx, learner, by[split],
                                    lambda row: outputs[digest([row['question'], row['family']])], variants)
                for key in ('cases', 'acceptable', 'guarded_wrong', 'declined'):
                    if stored[key] != result[key]:
                        raise ValueError('Stored comparison totals do not reproduce: ' + name + '/' + split)
                for old, new in zip(stored['details'], result['details']):
                    if any(old[k] != new[k] for k in ('guarded', 'acceptable', 'guarded_wrong', 'query')
                           if k in old or k in new):
                        raise ValueError('Stored task verdict no longer reproduces: ' + old['task'])
                fresh[name][split] = result['details']
                families = defaultdict(list)
                for row in result['details']:
                    families[row['family']].append(row)
                source_results = {}
                failures = Counter()
                for index, source in enumerate([ctx, *variants]):
                    name_in_sources = 'primary' if index == 0 else f'variant_{index}'
                    grades = [source.assess(learner, row, outputs[digest([row['question'], row['family']])])
                              for row in by[split]]
                    reasons = Counter(r['reason'] for r in grades if not r['acceptable'])
                    failures.update({name_in_sources + ': ' + reason: n for reason, n in reasons.items()})
                    source_results[name_in_sources] = {
                        'identity': source.identity, 'cases': len(grades),
                        'acceptable': sum(r['acceptable'] for r in grades),
                        'accepted_wrong': sum(r['guarded'] and not r['acceptable'] for r in grades),
                        'declined': sum(not r['guarded'] for r in grades), 'failure_reasons': dict(reasons)}
                summaries[name][split] = {
                    'cases': result['cases'], 'outcomes': outcomes(result['details']),
                    'guarded_wrong_any_source': result['guarded_wrong'],
                    'declined_on_any_source': result['declined'],
                    'source_results': source_results,
                    'failure_reasons': dict(failures),
                    'failure_reason_unit': 'Source-task pairs; a task can fail on multiple sources',
                    'families': {k: {'cases': len(v), **outcomes(v)} for k, v in families.items()},
                    'original_comparison_seconds': stored['seconds'],
                    'fresh_native_review_seconds': result['seconds']}
        deterministic = [ctx.deterministic(row) and all(v.deterministic(row) for v in variants)
                         for row in by['verification']]
    if guard['blocked_attempts']:
        raise ValueError('Offline review attempted an external operation')
    if (T.Context(stack).identity != ctx.identity or
            any(T.Context(v.path).identity != v.identity for v in variants) or
            A.contract(learner) != qualification['contract']):
        raise ValueError('Source or implementation changed during review')
    candidate = fresh['candidate']
    baseline = fresh['baseline']['verification']
    previous = fresh.get('parent_candidate', fresh['baseline'])['verification']
    useful = [i for i, covered in enumerate(deterministic) if not covered]
    gained = sum(candidate['verification'][i]['acceptable'] and not baseline[i]['acceptable'] for i in useful)
    incremental = sum(candidate['verification'][i]['acceptable'] and not previous[i]['acceptable'] for i in useful)
    regressions = []
    for name in fresh.keys() - {'candidate'}:
        for split in ('calibration', 'verification'):
            for old, new in zip(fresh[name][split], candidate[split]):
                if old['acceptable'] and not new['acceptable']:
                    regressions.append({'task': old['task'], 'family': old['family'], 'split': split, 'against': name})
    wrong = sum(r['guarded_wrong'] for rows in candidate.values() for r in rows)
    selected = gained > 0 and incremental > 0 and not regressions and wrong == 0
    if (qualification['selected'] != selected or qualification['useful_improvements'] != gained or
            qualification['improvements_over_parent'] != incremental or
            qualification['regressions'] != len(regressions) or
            qualification['deterministic_already_acceptable'] != sum(deterministic)):
        raise ValueError('Stored qualification decision does not reproduce')
    return {'schema': 'offline-study-review/1', 'status': 'reproduced', 'revision': saved['revision'],
            'manifest': str(Path(manifest).resolve()), 'receipt': str(Path(receipt).resolve()),
            'receipt_sha256': A.file_hash(receipt), 'dataset': dataset, 'contract': qualification['contract'],
            'source_identity': ctx.identity, 'comparisons': summaries,
            'useful_improvements': gained, 'improvements_over_parent': incremental,
            'regression_edges': len(regressions), 'regressed_unique_tasks': len({r['task'] for r in regressions}),
            'regressions': regressions, 'selected_candidate': selected,
            'local_decodes': qualification['local_decodes'], 'original_round_seconds': saved['seconds'],
            'provider_calls': 0, 'local_compute_dollars': None,
            'cost_per_clinically_right_answer': None,
            'cost_limitation': 'Local hardware cost and physician-adjudicated answers were not measured',
            'install': False, 'clinical_qualification': False}


def review_study(result, *, stack):
    result = C.checked_path(result)
    run = json.loads(result.read_text())
    if run.get('status') != 'completed':
        raise ValueError('Only completed studies can receive a final review')
    study = C.checked_path(run['study'])
    protocol = json.loads((study / 'protocol.json').read_text())
    if any(run[k] != protocol[k] for k in protocol):
        raise ValueError('Study completion and original protocol differ')
    count = run['completed_rounds']
    if type(count) is not int or not 1 <= count <= protocol['rounds']:
        raise ValueError('Study has an invalid completed-round count')
    _, original, original_dataset = T.load(protocol['initial_manifest'])
    if original_dataset != protocol['dataset']:
        raise ValueError('Original study data changed')
    rounds = []
    for index in range(count):
        manifest = protocol['initial_manifest'] if index == 0 else study / f'correction-{index}/manifest.json'
        if index:
            _, correction, _ = T.load(manifest)
            if any(digest(correction[s]) != digest(original[s]) for s in ('calibration', 'verification')):
                raise ValueError('A recursive correction changed the comparison splits')
            probe = json.loads((study / f'fit-probe-{index}.json').read_text())
            first = {}
            families = defaultdict(list)
            for row in original['fit']:
                families[row['family']].append(row)
                first.setdefault(row['family'], digest([row['question'], row['family']]))
            if ([r['task'] for r in probe['details']] != list(first.values()) or
                    [r['family'] for r in probe['details']] != list(first)):
                raise ValueError('Recursive correction probe is not the declared fit-only probe')
            failed = {r['family'] for r in probe['details'] if not r['acceptable']}
            expected = [row for family, rows in families.items() for row in rows[:
                        protocol['corrections_per_family'] if family in failed else protocol['rehearsal_per_family']]]
            if digest(expected) != digest(correction['fit']):
                raise ValueError('Recursive fitting differs from the declared correction/rehearsal allocation')
        rounds.append(review_round(manifest, study / f'round-{index+1}.json', stack=stack))
    if [r['revision'] for r in rounds] != run['revisions']:
        raise ValueError('Study revision history differs from its rounds')
    return {'schema': 'offline-recursive-study-review/1', 'status': 'reproduced',
            'result': str(result), 'result_sha256': A.file_hash(result), 'rounds': rounds,
            'selected_revisions': [r['revision'] for r in rounds if r['selected_candidate']],
            'provider_calls': 0, 'install': False, 'clinical_qualification': False}


def dependency_complete(results, *, process_alive):
    """Completion receipts are authoritative even after a PID is reused.

    A surviving process alone never means success; a dead process without all
    required receipts fails instead of starting a dependent GPU study.
    """
    complete = []
    for path in results:
        path = C.checked_path(path)
        if not path.is_file():
            complete.append(False)
            continue
        receipt = json.loads(path.read_text())
        if receipt.get('status') != 'completed':
            raise ValueError('Dependency did not complete: ' + str(path))
        complete.append(True)
    if not complete:
        raise ValueError('Explicit dependency completion receipts required')
    if all(complete):
        return True
    if not process_alive:
        raise RuntimeError('Dependency exited without all required completion receipts')
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('result', 'stack', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    reviewed = review_study(args.result, stack=args.stack)
    output = C.checked_path(args.output)
    if output.exists():
        raise ValueError('Preserve existing study reviews; choose a new output path')
    output.write_text(json.dumps(reviewed, indent=2) + '\n')
    print(json.dumps({k: v for k, v in reviewed.items() if k != 'rounds'}, indent=2))


if __name__ == '__main__':
    main()
