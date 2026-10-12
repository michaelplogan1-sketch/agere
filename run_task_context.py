"""Exercise shared context on every observed synthetic patient; no model calls."""
import argparse
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch

from clinical_core.agere.clinical.context_builder import execute, inspect
from clinical_core.agere.clinical.graph_workbench import context_examples
from clinical_core.agere.clinical.graph_planner import propose
from clinical_core.agere.clinical.analysis_graph import catalog


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--stack',type=Path,default=Path('artifacts/agere-population-stack-v3'))
    parser.add_argument('--output',type=Path,default=Path('artifacts/agere-task-context-v1'))
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(args.stack/'stack.sqlite3')
    patients=[r[0] for r in db.execute('SELECT id FROM patients ORDER BY id')];db.close()
    receipts=[]
    for example in context_examples():
        result=execute(args.stack,patients,example['graph']['nodes'][1]['parameters'])
        print(json.dumps({'example':example['title'],'patients':result['patients_evaluated'],
            'statuses':result['statuses'],'counts':result['counts'],'seconds':result['elapsed_seconds']}),flush=True)
        assert sum(result['statuses'].values())==len(patients)
        assert result['counts']['selected_units']+result['counts']['omitted_units']==result['counts']['candidate_units']
        assert result['new_paid_api_calls']==0
        for preview in result['patients']:
            receipt=inspect(args.stack,preview['id'])
            assert receipt['budget']['used']<=receipt['parameters']['max_bytes']
            assert not receipt['dispatch']['source_review_allowed'] or receipt['budget']['omitted_units']==0
        receipts.append(result)
    planner_bytes=[]
    def capture(body,*a,**kw):
        size=len(json.dumps(body).encode());planner_bytes.append(size);assert size<=20000,size
    meta=json.loads((args.stack/'run.json').read_text())
    with patch('clinical_core.agere.clinical.graph_planner.send',side_effect=capture):
        propose('x'*1500,args.stack,None,snapshot=meta['snapshot'],graph_catalog=catalog(args.stack),
                observed_values=json.loads((args.stack/'cohort-values.json').read_text()))
    report={'synthetic':True,'scope':'Complete-population integration receipt; NOT clinical accuracy or benefit validation.',
            'source_digest':meta['source_digest'],'snapshot':meta['snapshot'],'patients':len(patients),
            'context_builds':receipts,'planner_max_ascii_question_bytes':planner_bytes[0],
            'new_paid_api_calls':0}
    (args.output/'run.json').write_text(json.dumps(report,indent=2)+'\n')
    print('Saved '+str(args.output/'run.json'),flush=True)


if __name__=='__main__':main()
