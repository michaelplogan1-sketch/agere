"""Register existing research as cited knowledge; never relabel it clinical training truth."""
import json
from pathlib import Path
from ..contracts import digest

ROOT=Path(__file__).resolve().parents[2]
MAPPING={
 'signal_detection':{'operator':'clinical_core.signal_math.gaussian_change_points','origin':'Astronomy / signal processing','role':'Find level changes with explicit measurement errors'},
 'state_observation':{'operator':'clinical_core.joint_observation.infer_joint','origin':'Inverse problems / sensor fusion','role':'Separate patient state from recording process; simulator-conditioned hypotheses'},
 'information_value':{'operator':'clinical_core.information_value.expected_information_value','origin':'Decision theory / active sensing','role':'Compare expected value of obtaining missing evidence'},
 'process_monitoring':{'operator':'clinical_core.process_monitoring.source_volume_monitors','origin':'Manufacturing quality control','role':'CUSUM / EWMA source-volume anomaly detection'},
 'incremental_execution':{'operator':'clinical_core.dependency_cache.DependencyCache','origin':'Build systems / databases','role':'Invalidate only dependent computations'},
 'causal_effect':{'operator':'clinical_core.agere.analytics.crossfit_effect','origin':'Econometrics / causal inference','role':'Group-cross-fitted AIPW under explicit identification assumptions'},
 'facility_comparison':{'operator':'clinical_core.agere.analytics.risk_adjusted_facilities','origin':'Reliability engineering / statistics','role':'Observed versus expected outcomes with family-separated fitting'},
 'shrinkage':{'operator':'clinical_core.agere.mathematics.facility_shrinkage','origin':'Actuarial credibility / Bayesian statistics','role':'Stabilize small denominators; not case-mix adjustment'},
 'claim_runout':{'operator':'clinical_core.agere.mathematics.claim_development','origin':'Insurance reserving','role':'Expose service-to-payment lag without fabricated ultimate costs'},
 'process_paths':{'operator':'clinical_core.agere.operators.process_paths','origin':'Process mining / supply chains','role':'Observed event transitions and delays, not causal links'},
 'multiple_testing':{'operator':'clinical_core.agere.mathematics.benjamini_hochberg','origin':'Statistical discovery','role':'FDR correction when valid p-values and dependence assumptions hold'},
 'allocation':{'operator':'clinical_core.agere.mathematics.allocate','origin':'Operations research','role':'MILP benefit allocation subject to explicit cost and capacity'},
 'tensor_train':{'operator':'clinical_core.agere.tensor_train.TensorTrain','origin':'Tensor networks / many-body physics','role':'Classical low-rank classifier compared with logistic control'},
 'routing':{'operator':'clinical_core.agere.mathematics.route_search','origin':'Constrained optimization / algorithm search','role':'Search routing thresholds under calibration error and compute budgets'}
}

def catalog():
    sources=[]
    for path in sorted((ROOT/'research').rglob('*')):
        if path.suffix not in {'.md','.json','.csv'}:continue
        sources.append({'path':str(path.relative_to(ROOT)),'sha256':digest(path.read_text()),
                        'use':'Cited design/context or annotated curriculum; not automatically supervised clinical truth'})
    return {'research_files':sources,'mathematical_operators':MAPPING,
            'novelty':'Combination and application are research hypotheses. No claim that established equations were newly invented.',
            'excluded_from_default':['Quantum hardware or quantum advantage claims','Physics analogies without an executable objective',
                                     'Raw copyrighted publication text used as default fine-tuning data']}
