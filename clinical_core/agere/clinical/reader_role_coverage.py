"""Expose representational limits without changing frozen model outputs or fits."""
import re
from . import reader_comparison as C, semantic_reader as R, semantic_student as S, windowed_student as W, learning_cohorts as L

VERSION='statement-role-label-coverage/1'


def coverage(manifest):
    supported={a:list(manifest['heads'][a]['classes']) for a in R.AXES}
    if any(len(v)!=len(set(v)) or set(v)-set(R.AXES[a]) for a,v in supported.items()):
        raise ValueError('Model classes do not match the statement contract')
    missing={a:sorted(set(R.AXES[a])-set(supported[a])) for a in R.AXES}
    return {'supported_labels':supported,'unavailable_labels':missing,'full_label_contract':not any(missing.values()),
            'selective_threshold':manifest['calibration']['threshold'],
            'automatic_acceptance':False,'deployment_qualified':False,
            'meaning':'A missing class can never be predicted by this head. Representability does not establish accuracy; native readers also remain unvalidated.'}


def inspect(stack,body):
    if not isinstance(body,dict) or set(body)!={'cohort_id'}:raise ValueError('Exact cohort required')
    # Reuse strict registered identity and completed-fit verification.
    if not isinstance(body['cohort_id'],str) or not re.fullmatch('[a-f0-9]{64}',body['cohort_id']):raise ValueError('Exact cohort required')
    specs,_=C.model_specs(stack,body['cohort_id'],C.ROLES[:2])
    output,_,_,_=L.dataset(stack,body['cohort_id']);lexical=S.verify(output/'models/lexical')
    _,windowed,_=W.model(stack,body['cohort_id'])
    roles={role:{**coverage(m),'model_identity':specs[role]['model_identity']} for role,m in [('cohort_lexical',lexical),('cohort_windowed_minilm',windowed)]}
    return {'schema':VERSION,'cohort_id':body['cohort_id'],'roles':roles,'new_paid_api_calls':0,'training_performed':False,
            'native_roles':'Kev and d1 receive every label option in the shared request. That does not establish reliable performance for those labels.',
            'routing_rule':'These heads remain shadow proposals. Missing output classes and absent qualified thresholds preclude replacing the complete statement reader contract.'}
