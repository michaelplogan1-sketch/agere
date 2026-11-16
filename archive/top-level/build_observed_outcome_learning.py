"""Build current-cohort target qualification and protected local comparisons."""
import argparse
import json
from pathlib import Path
import sqlite3
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import baseline_evidence as baseline, outcome_learning as learning
from clinical_core.agere.clinical.graph_workbench import baseline_examples, snapshot_examples
from clinical_core.agere.clinical.stack_runtime import Stack


def main():
    p=argparse.ArgumentParser();p.add_argument('--stack',type=Path,default=stack_path())
    p.add_argument('--lineage',type=Path);p.add_argument('--output',required=True,type=Path);p.add_argument('--attach',action='store_true')
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    lineage=a.lineage or a.stack.parent/'source-learning/lineage'
    stack=Stack(a.stack,allow_live=False);db=sqlite3.connect(a.stack/'stack.sqlite3')
    try: ids=[r[0] for r in db.execute('SELECT id FROM patients ORDER BY id')]
    finally: db.close()
    reports=[];paths=[]
    try:
        for i,ex in enumerate(snapshot_examples(baseline_examples(),stack.retriever.meta['snapshot']['as_of']),1):
            answer=stack.compose(ex['question']);learning.write(a.output/f'question-{i}.json',answer)
            op=ex['graph']['nodes'][1]['parameters'];bp=ex['graph']['nodes'][-1]['parameters']
            baseline.export_dataset(a.stack,ids,bp,op,a.output/f'dataset-{i}')
            target=a.output/f'learning-{i}'
            report=learning.build(a.stack,lineage,a.output/f'dataset-{i}',target)
            reports.append(report);paths.append(target)
            print(json.dumps({'contract':op['contract'],'status':report['status'],'counts':report['counts']}),flush=True)
        learning.write(a.output/'run.json',{'synthetic':True,'reports':reports,'new_paid_api_calls':0})
        if a.attach: learning.write(a.output/'attachment-verification.json',learning.attach(a.stack,paths))
    finally: stack.close()


if __name__=='__main__':main()
