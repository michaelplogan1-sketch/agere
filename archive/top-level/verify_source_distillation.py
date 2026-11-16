"""Exercise source/model provenance and serving on the active synthetic stack."""
from collections import Counter
import json
from pathlib import Path
from clinical_core.contracts import digest
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import source_distillation as sd, semantic_student as student, semantic_reader as reader


def main():
    stack=stack_path();root=Path('artifacts/agere-source-distillation-v1')
    plan_dir,plan,targets=sd.connected(stack)
    status=sd.status(stack)
    assert all(p['successful_readings']==len(targets) and not p['execution_failures'] and p['pending']==0 for p in status['providers'].values())
    rows,dataset=sd.load_snapshot(stack,root/'dataset');student.validate_rows(rows)
    requests=[]
    for row in targets:
        native=[sd.reading_for(stack,row,plan,p) for p in sd.PROVIDERS]
        assert all(r and r['context']==row['input'] and r['target']==row['target'] for r in native)
        bodies=[reader.request(r['context'],r['provider']) for r in native]
        assert bodies[0]['state']==bodies[1]['state'] and bodies[0]['questions']==bodies[1]['questions']
        requests.append({'item_id':row['item_id'],'input_digest':digest([bodies[0]['state'],bodies[0]['questions']]),
            'reading_ids':[r['id'] for r in native]})
    models=student.status(stack);served=[]
    # Exact same source in both students, then repeated with no model reload.
    selected=next(r for r in rows if r['partition']=='validation')
    for provider in student.PROVIDERS:
        first=student.read(stack,selected['target'],provider);replay=student.read(stack,selected['target'],provider)
        inspected=reader.inspect(stack,first['id'])
        assert replay['cache_hit'] and first['id']==replay['id'] and not inspected['reviews']
        assert not first['reading']['clinical_authority'] and not first['reading']['automatic_acceptance']
        assert not first['requirement_bridge']['requirements_satisfied'] and not first['current_clinical_state_established']
        assert first['new_paid_api_calls']==0 and first['usage']['hosted_tokens']==0
        served.append({'provider':provider,'reading_id':first['id'],'labels':first['reading']['labels'],
            'target':selected['target'],'usage':first['usage'],'exact_replay':True})
    export=reader.learning_export(stack)
    assert not any(r['reading_id'] in {s['reading_id'] for s in served} for r in export['rows'])
    report={'synthetic':True,'source_identity':plan['source_identity'],'target_count':len(targets),
        'paired_request_state_and_questions_identical':True,'source_and_family_partitions_verified':True,
        'plan_sha256':sd.queue.sha(plan_dir/'plan.json'),'dataset_sha256':sd.queue.sha(root/'dataset/manifest.json'),
        'native_readers':status,'models':models,'served_readings':served,
        'teacher_class_counts':{p:{axis:dict(Counter(r['labels'][axis] for r in rows if r['partition']==p)) for axis in reader.AXES} for p in student.PARTITIONS},
        'human_reviewed_labels_created':0,'new_paid_api_calls':0,'foundation_weights_updated':False,
        'default_reader_replaced':False,'winner_selected':False,'limits':sd.LIMITS}
    sd.queue.write(root/'paired-inputs.json',requests);sd.queue.write(root/'verification.json',report)
    print(json.dumps({'native':{k:v['successful_readings'] for k,v in status['providers'].items()},
        'model_counts':{k:v['counts'] for k,v in models['models'].items()},
        'models':{k:{'teacher_agreement':v['validation']['exact_match'],'accepted':v['validation']['selected_rows']} for k,v in models['models'].items()},
        'paid_calls':0,'verified':True},indent=2))


if __name__=='__main__':main()
