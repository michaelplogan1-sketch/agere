"""Bind the five unchanged task definitions to active-source protected reports."""
import argparse
import json
from pathlib import Path
import sqlite3
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import prediction_registry as R
from clinical_core.agere.clinical.graph_workbench import baseline_examples, landmark_examples, snapshot_examples
from clinical_core.agere.clinical.native_snapshot_cache import install, uninstall


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--attach',action='store_true');a=parser.parse_args()
    stack=stack_path();meta=R.read(stack/'run.json');a.output.mkdir(parents=True,exist_ok=False)
    link=R.read(stack/'learning-queue-link.json');queue=R.read(Path(link['path'])/'run.json')
    if R.sha(Path(link['path'])/'run.json')!=link['run_sha256']:raise ValueError('Attached source-learning queue changed')
    lineage=Path(queue['lineage_root']);R.L.learning_queue.lineage(lineage,meta)
    tasks=[(kind,ex) for kind,examples in [('index_day',baseline_examples()),('receipt_day',landmark_examples())]
           for ex in snapshot_examples(examples,meta['snapshot']['as_of'])]
    R.L.write(a.output/'predeclared-tasks.json',{'tasks':tasks,'fitting_plan':R.L.PLAN,'implementation_identity':R.identity(),
        'definition_change':False,'window_retuning':False,'source_snapshot':meta['snapshot']})
    db=sqlite3.connect(stack/'stack.sqlite3');ids=[r[0] for r in db.execute('SELECT id FROM patients ORDER BY id')];db.close()
    roots=[];install(stack)
    try:
        for index,(adapter,ex) in enumerate(tasks,1):
            data=a.output/f'dataset-{index}';nodes=ex['graph']['nodes'];p=nodes[-1]['parameters']
            if adapter=='index_day':R.B.export_dataset(stack,ids,p,nodes[-2]['parameters'],data)
            else:R.D.export_dataset(stack,ids,p,data)
            root=a.output/f'learning-{index}';r=R.build(stack,lineage,adapter,data,root);roots.append(root)
            R.L.write(a.output/f'question-{index}.json',ex)
            print(json.dumps({'task':r['task'],'status':r['status'],'source_qualification':{k:v for k,v in r['source_qualification'].items() if k!='examples'},'counts':r['counts']}),flush=True)
        result=R.attach(stack,roots) if a.attach else R.status(stack,[{'path':str(p),'sha256':R.sha(p/'run.json')} for p in roots])
        R.L.write(a.output/'run.json',result)
        print('Five source-bound qualification reports verified'+(' and attached.' if a.attach else '.'),flush=True)
    finally:uninstall(stack)


if __name__=='__main__':main()
