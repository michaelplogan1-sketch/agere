"""Execute local documentary reconciliation over the complete synthetic warehouse."""
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch

from clinical_core.agere.clinical import assertion_reconciliation as ar
from clinical_core.agere.clinical.graph_workbench import assertion_examples, SCRIPT
from clinical_core.agere.clinical.graph_planner import propose
from clinical_core.agere.clinical.analysis_graph import catalog


def main():
    root=Path('artifacts/agere-population-stack-v3');output=Path('artifacts/agere-assertion-reconciliation-v1')
    output.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(root/'stack.sqlite3');ids=[r[0] for r in db.execute('SELECT id FROM patients ORDER BY id')];db.close()
    meta=json.loads((root/'run.json').read_text());builds=[]
    for example in assertion_examples():
        result=ar.execute(root,ids,example['graph']['nodes'][-1]['parameters'])
        assert result['patients_evaluated']==len(ids)==sum(result['statuses'].values())
        c=result['counts'];assert c['received_source_units']==c['units_with_in_scope_assertions']+c['units_without_in_scope_assertions']
        builds.append({'title':example['title'],'result':result})
        (output/'run.json').write_text(json.dumps({'synthetic':True,'builds':builds,'source_digest':meta['source_digest']},indent=2))
        print(json.dumps({'title':example['title'],'patients':result['patients_evaluated'],'counts':c,'statuses':result['statuses'],'seconds':result['elapsed_seconds']}),flush=True)
    size=[]
    def capture(body,*a,**kw):size.append(len(json.dumps(body).encode()))
    with patch('clinical_core.agere.clinical.graph_planner.send',side_effect=capture):
        propose('x'*1500,root,None,snapshot=meta['snapshot'],graph_catalog=catalog(root),
                observed_values=json.loads((root/'cohort-values.json').read_text()))
    (output/'run.json').write_text(json.dumps({'synthetic':True,'builds':builds,'source_digest':meta['source_digest'],
         'planner_max_ascii_request_bytes':size[0],'new_paid_api_calls':0,
         'scope':'Source accounting and documentary reconciliation; not clinical accuracy or outcome validation.'},indent=2))
    Path('/tmp/agere-assertion-workbench.js').write_text(SCRIPT)
    print('Planner bytes: '+str(size[0]),flush=True)
    assert size[0]<=20000, 'Planner exceeds provider cap'


if __name__=='__main__':main()
