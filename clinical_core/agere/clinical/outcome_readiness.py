"""Observed-target qualification; never convert absent records to clinical negatives."""
from collections import Counter
from .clinical_events import iso

VERSION = 'observed-target-readiness/1'


def assess(baseline, outcome):
    if baseline['patient_id'] != outcome['patient_id']:
        raise ValueError('Cross-patient baseline/outcome join')
    if baseline['outcome_receipt_id'] != outcome['id']:
        raise ValueError('Baseline/outcome receipt join changed')
    reasons = []
    if baseline['status'] != 'baseline_observations_available': reasons.append('no_baseline_observations')
    if outcome['indexed'] is not True: reasons.append('no_resolved_index')
    if outcome['indexed'] is True:
        if not outcome['calendar_mature']: reasons.append('incomplete_calendar_followup')
        if outcome['recorded_label'] not in (0, 1): reasons.append('unresolved_recorded_target')
    arrivals = []
    if outcome['index']:
        arrivals = [outcome['evidence'][key].get('arrival_date') for key in outcome['index']['evidence_ids']]
        # At least one copy of the index must already have been available at the
        # declared index day. Later corroborating copies do not erase early receipt.
        known = [value for value in arrivals if iso(value)]
        timely = bool(known and min(known) <= outcome['index']['date'])
        if not timely: reasons.append('index_not_known_by_index_day')
    else: timely = False
    if outcome['index'] and (not baseline['cutoff'] or baseline['cutoff'] >= outcome['index']['date']):
        reasons.append('invalid_pre_index_feature_cutoff')
    return {'schema': VERSION, 'patient_id': outcome['patient_id'],
        'baseline_receipt_id': baseline['id'], 'outcome_receipt_id': outcome['id'],
        'recorded_label': outcome['recorded_label'], 'calendar_mature': outcome['calendar_mature'],
        'index_known_by_index_day': timely, 'index_arrival_dates': arrivals,
        'baseline_and_mature_recorded_label': baseline['status'] == 'baseline_observations_available' and outcome['mature_recorded_label_usable'],
        'timing_eligible_observed_target': not reasons, 'exclusion_reasons': reasons,
        'capture_status': 'unknown', 'clinical_label': None,
        'clinical_prediction_authorized': False,
        'target_meaning': 'Observed received endpoint under the registered clock and window; zero is not no care.'}


def summarize(rows):
    eligible = [r for r in rows if r['timing_eligible_observed_target']]
    labels = Counter(str(r['recorded_label']) for r in eligible)
    gaps = []
    if not eligible: gaps.append('No timing-eligible observed targets with baseline evidence.')
    if set(labels) != {'0', '1'}: gaps.append('Timing-eligible targets do not contain both recorded outcome classes.')
    return {'schema': VERSION, 'patients_assessed': len(rows), 'timing_eligible': len(eligible),
        'baseline_and_mature_recorded_label': sum(r['baseline_and_mature_recorded_label'] for r in rows),
        'eligible_recorded_label_counts': dict(labels),
        'exclusion_counts': dict(Counter(reason for r in rows for reason in r['exclusion_reasons'])),
        'exclusion_counts_overlap': True, 'observed_target_has_two_classes': set(labels) == {'0', '1'},
        'data_gaps': gaps, 'further_training_requirements': [
            'Protected family/template partitions, sufficient classes per partition and a frozen development plan.',
            'Separate features/targets and predeclared baseline/model comparisons; no automatic model promotion.'],
        'source_capture_established': False, 'clinical_prediction_authorized': False,
        'model_fitted': False, 'new_paid_api_calls': 0}
