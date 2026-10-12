"""Current source coverage at an internal task, without rewriting old handoffs."""
import hashlib
from pathlib import Path
from ...contracts import digest
from . import care_capabilities as care, care_readiness, source_synthesis

VERSION='synthesis-work-availability/1'


def prepare(path, task_id):
    task=care_readiness.task(path,task_id)
    plan=care.inspect_plan(path,task['plan_id']);origin=plan['origin']
    if task['patient_id']!=plan['patient_id']:
        raise ValueError('Task and plan patient identities differ')
    if origin['kind']=='answer':
        bindings=[{'answer_id':origin['id'],'answer_hash':origin['hash']}]
    else:
        bindings=(origin['assessment'].get('synthesis_context') or {}).get('answers',[])
    answers=[]
    for binding in bindings:
        answer=source_synthesis.inspect(path,binding['answer_id'])
        if answer['patient_id']!=task['patient_id'] or digest(answer)!=binding['answer_hash']:
            raise ValueError('Task answer identity or current source dependencies changed')
        availability=answer.get('source_availability')
        answers.append({'answer_id':answer['id'],'answer_hash':digest(answer),'question':answer['question'],
            'availability_status':'recorded_at_inference' if availability else 'not_recorded_in_historical_answer',
            'availability':availability,'source_scope':answer['source_scope'],'record_scope':answer.get('record_scope'),
            'coverage':answer['coverage'],
            'source_passes':[{'pass':p['pass_number'],'availability':p.get('source_availability')} for p in answer.get('passes',[])],
            'reconciliation_groups':[{'source_records':g['source_records'],'availability':g.get('source_availability')}
                                     for g in answer.get('reconciliation',{}).get('groups',[])],
            'clinical_meaning_validated':False})
    result={'schema':VERSION,'task_id':task_id,'task_revision':task['revision'],'plan_id':plan['id'],
        'patient_id':task['patient_id'],'origin_hash':digest(origin),'answers':answers,
        'code_identity':digest([VERSION,hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),care.identity()]),
        'authority':'current_source_coverage_context_only','clinical_execution_authorized':False,
        'human_review_established':False,'new_paid_api_calls':0,'external_actions':0,
        'scope':'Availability and unit submission describe the saved interpretation, not a complete clinical review. '
                'Historical answers without availability metadata remain explicit. No handoff or review state is changed.'}
    if care_readiness.task(path,task_id)!=task:
        raise ValueError('Task changed while reading its answer coverage')
    result['id']=digest(result)
    return result
