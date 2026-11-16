"""Reproducible receipt-time question, X/y, protected qualification and local fit."""
import argparse
import json
from pathlib import Path
import sqlite3
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import decision_landmarks as D, outcome_learning as Q
from clinical_core.agere.clinical.graph_workbench import landmark_examples, snapshot_examples
from clinical_core.agere.clinical.stack_runtime import Stack


def main():
    p=argparse.ArgumentParser();p.add_argument('--stack',type=Path,default=stack_path())
    p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    frozen=a.output/'predeclared-task.json'
    plan=json.loads(frozen.read_text())
    expected=plan['definitions_sha256']
    if Q.sha(Path(D.__file__).with_name('landmark_contracts.json'))!=expected:raise ValueError('Predeclared landmark definitions changed')
    if (a.output/'run.json').exists():raise ValueError('Completed landmark build is immutable')
    stack=Stack(a.stack,allow_live=False);db=sqlite3.connect(a.stack/'stack.sqlite3')
    try:ids=[r[0] for r in db.execute('SELECT id FROM patients ORDER BY id')]
    finally:db.close()
    meta=json.loads((a.stack/'run.json').read_text())
    policy,lm=Q.learning_queue.lineage(a.stack.parent/'source-learning/lineage',meta);protected=Q.semantic_learning.protections()
    reports=[]
    try:
        for i,ex in enumerate(snapshot_examples(landmark_examples(),meta['snapshot']['as_of']),1):
            answer=stack.compose(ex['question']);Q.write(a.output/f'question-{i}.json',answer)
            dataset=a.output/f'dataset-{i}';parameters=ex['graph']['nodes'][-1]['parameters']
            manifest=D.export_dataset(a.stack,ids,parameters,dataset)
            xs,ys=[[json.loads(line) for line in (dataset/n).read_text().splitlines()] for n in ('features.jsonl','targets.jsonl')]
            qualification=Q.qualify(xs,ys,policy,protected);target=a.output/f'learning-{i}';target.mkdir(exist_ok=False)
            Q.write(target/'qualification.json',qualification);model=Q.fit_rows(qualification,target/'models')
            report={'contract':parameters['contract'],'synthetic':True,'dataset_identity':manifest['identity'],
                'dataset_manifest_sha256':Q.sha(dataset/'manifest.json'),'code_identity':D.code_identity(),'fitter_identity':Q.identity(),
                'source_identity':manifest['source_identity'],'predeclared_plan_sha256':Q.sha(frozen),
                'lineage_policy_sha256':lm['policy_sha256'],'protected_register':protected,
                'counts':{k:qualification[k] for k in ('patients_assessed','eligible_rows','partition_class_counts','excluded_reason_counts')},
                'status':model['status'],'qualification_sha256':Q.sha(target/'qualification.json'),
                'model_report_sha256':Q.sha(target/'models/run.json'),'readiness_gaps':qualification['readiness_gaps'],
                'clinical_prediction_authorized':False,'automatic_promotion':False,'new_paid_api_calls':0}
            Q.write(target/'run.json',report);reports.append(report)
            print(json.dumps({'contract':report['contract'],'status':report['status'],'candidate_labels':manifest['candidate_label_counts'],'counts':report['counts']}),flush=True)
        if protected!=Q.semantic_learning.protections():raise ValueError('Protection register changed during build')
        Q.write(a.output/'run.json',{'synthetic':True,'reports':reports,'new_paid_api_calls':0,'clinical_prediction_authorized':False})
    finally:stack.close()


if __name__=='__main__':main()
