"""Check fixed development design before acquisition, without fitting or labels."""
from collections import Counter
from pathlib import Path

from ...contracts import digest
from . import source_distillation as distillation, semantic_student as student
from . import learning_queue as queue

VERSION = 'distillation-design-qualification/1'


def assess(rows, unassessed, policy):
    counts = {part: sum(r['partition'] == part for r in rows) for part in student.PARTITIONS}
    families = {part: len({r['patient_family'] for r in rows if r['partition'] == part}) for part in student.PARTITIONS}
    reasons = []
    if counts['calibration'] < policy['minimum_accepted_rows']:
        reasons.append({'code': 'insufficient_calibration_targets', 'observed': counts['calibration'],
            'minimum': policy['minimum_accepted_rows'],
            'detail': 'Even accepting every target cannot meet the calibration accepted-row requirement.'})
    if families['calibration'] < policy['minimum_accepted_families']:
        reasons.append({'code': 'insufficient_calibration_families', 'observed': families['calibration'],
            'minimum': policy['minimum_accepted_families'], 'detail': 'Insufficient distinct calibration families.'})
    # Apply the actual existing error-bound rule to both stages. Validation has
    # no separate 30-row/5-family rule; do not invent one. Even zero errors on all
    # rows need enough observations to meet this descriptive Wilson bound.
    perfect_minimum = next(n for n in range(1,100001)
                           if student.error_upper(0,n) <= policy['maximum_empirical_error_upper_95'])
    for part in ('calibration', 'validation'):
        if counts[part] < perfect_minimum:
            reasons.append({'code': 'insufficient_'+part+'_error_bound', 'observed': counts[part],
                'minimum': perfect_minimum, 'best_possible_error_upper_95': student.error_upper(0,counts[part]),
                'detail': 'Even zero errors at full coverage cannot meet the existing empirical error-bound rule.'})
    if not counts['train']:
        reasons.append({'code': 'no_training_targets', 'observed': 0, 'minimum': 1, 'detail': 'No fitting inputs.'})
    return {'counts': counts, 'families': families, 'unassessed_fields': len(unassessed),
        'unassessed_reasons': dict(Counter(r['reason'] for r in unassessed)),
        'structural_minimums_met': not reasons, 'structural_failures': reasons,
        'recommended_next_step': 'Acquire a separately versioned, larger development sample before qualifying automatic acceptance.' if reasons else
            'The design meets only sample minimums. Acquire declared supervision and test class coverage, fitting and calibration.',
        'unmeasured': ['Reviewed label quality', 'Training class coverage', 'Teacher imitation or clinical accuracy',
            'Selective calibration error and coverage', 'Representative independent clinical validation'],
        'fit_performed': False, 'model_qualified': False, 'automatic_acceptance': False}


def inspect(path):
    if not (Path(path)/'source-distillation-link.json').exists():
        return {'schema': VERSION, 'status': 'not_attached', 'automatic_acceptance': False}
    root, plan, rows = distillation.connected(path)
    result = assess(rows, plan['unassessed'], student.POLICY)
    return {'schema': VERSION, 'status': 'design_checked', 'plan_sha256': queue.sha(root/'plan.json'),
        'source_identity': plan['source_identity'], 'teacher': plan['teacher'],
        'policy': student.POLICY, 'policy_digest': digest(student.POLICY), **result,
        'new_paid_api_calls': 0, 'model_calls': 0, 'labels_created': 0,
        'limits': ['Design minimums are necessary, never sufficient, for selective model acceptance.',
            'These subdivisions come only from acquisition training families; frozen and outer evaluation families are excluded.',
            'Changing or enlarging a sample requires a new predeclared plan; do not move observed examples between splits.',
            'This check is not a spending limit and does not forbid explicit bounded exploratory readings.']}
