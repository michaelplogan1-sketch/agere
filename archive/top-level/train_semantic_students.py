"""Fit comparable local semantic heads from declared synthetic supervision."""
import argparse
import json
from pathlib import Path
from clinical_core.agere.clinical import semantic_student as student
from clinical_core.agere.clinical import semantic_curriculum as curriculum
from clinical_core.agere.clinical import semantic_learning as learning
from clinical_core.agere.active import stack_path


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--stack',type=Path,default=stack_path())
    source=p.add_mutually_exclusive_group(required=True)
    source.add_argument('--authored-development',action='store_true')
    source.add_argument('--reviewed-dataset',type=Path)
    source.add_argument('--machine-dataset',type=Path,help='Unreviewed teacher imitation; always shadow only')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--attach',action='store_true',help='Attach both readers as optional shadow proposals, never default/automatic acceptance')
    args=p.parse_args()
    if args.machine_dataset:
        from clinical_core.agere.clinical import source_distillation
        rows,manifest=source_distillation.load_snapshot(args.stack,args.machine_dataset)
        supervision='machine_teacher_snapshot'
        provenance={'machine_dataset':str(args.machine_dataset.resolve()),'snapshot_sha256':student.sha(args.machine_dataset/'manifest.json'),
                    'teacher':manifest['teacher'],'human_reviewed_labels_used':False,'frozen_kev_d1_data_used':False}
    elif args.reviewed_dataset:
        prepared,manifest=learning.load_snapshot(args.stack,args.reviewed_dataset)
        rows=prepared['rows'];supervision='source_reviewed_snapshot'
        provenance={'reviewed_dataset':str(args.reviewed_dataset.resolve()),'snapshot_sha256':student.sha(args.reviewed_dataset/'manifest.json'),
                    'review_export_digest':manifest['review_export_digest']}
    else:
        rows=curriculum.build();supervision='authored_synthetic_development'
        provenance={'curriculum_version':curriculum.VERSION,'curriculum_code_sha256':student.sha(curriculum.__file__),
                    'warehouse_review_labels_used':False,'frozen_kev_d1_data_used':False}
    student.validate_rows(rows);args.output.mkdir(parents=True,exist_ok=False)
    summary={'synthetic':True,'supervision':supervision,'models':{},'new_paid_api_calls':0,'winner_selected':False}
    models={}
    for provider,kind in student.PROVIDERS.items():
        path=args.output/kind
        m=student.fit(rows,path,kind=kind,supervision=supervision,provenance=provenance)
        models[provider]=path
        summary['models'][provider]={k:m[k] for k in ('counts','calibration','validation','release','training_seconds','model_artifact_bytes')}
    if args.reviewed_dataset:learning.load_snapshot(args.stack,args.reviewed_dataset)
    if args.machine_dataset:source_distillation.load_snapshot(args.stack,args.machine_dataset)
    if args.attach:summary['attachments']=student.attach(args.stack,models)
    (args.output/'run.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps({k:{'validation_exact_match':v['validation']['exact_match'],'selected_rows':v['validation']['selected_rows'],
        'release':v['release']['status'],'training_seconds':v['training_seconds']} for k,v in summary['models'].items()},indent=2))


if __name__=='__main__':main()
