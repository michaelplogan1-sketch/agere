"""Build complete-population chart passes and record provider-ready boundaries."""
import argparse
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch
import zlib

from clinical_core.contracts import digest
from clinical_core.agere.clinical import context_builder as context, context_passes as passes
from clinical_core.agere.clinical.graph_workbench import pass_examples, context_examples
from clinical_core.agere.clinical.graph_planner import propose
from clinical_core.agere.clinical.analysis_graph import catalog
from clinical_core.agere.clinical.context_student import REQUEST_MAX_BYTES  # shared provider request bound


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--stack',type=Path,default=Path('artifacts/agere-population-stack-v3'))
    parser.add_argument('--output',type=Path,default=Path('artifacts/agere-context-passes-v1'))
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(args.stack/'stack.sqlite3');ids=[r[0] for r in db.execute('SELECT id FROM patients ORDER BY id')];db.close()
    prior=json.loads(Path('artifacts/agere-task-context-v1/run.json').read_text())
    old=sqlite3.connect(args.stack/'contexts.sqlite3');new=passes.storage(args.stack)
    builds=[];candidates=[]
    try:
        for index,example in enumerate(pass_examples()):
            p=example['graph']['nodes'][-1]['parameters'];r=passes.execute(args.stack,ids,p)
            checks={'all_ready_requests_within_provider_cap':True,'maximum_provider_request_bytes':0,
                    'prior_protected_overflow_now_fully_partitioned':0,'provider_size_blocked_patients':0}
            old_params=context.validate(context_examples()[index]['graph']['nodes'][-1]['parameters'])
            old_identity=prior['context_builds'][index]['identity'];candidate=None
            for patient in ids:
                key=digest([r['identity'],context.validate(p),patient])
                bundle=json.loads(zlib.decompress(new.execute('SELECT payload FROM bundles WHERE id=?',(key,)).fetchone()[0]))
                assert bundle['counts']['packed_units']+bundle['counts']['blocked_units']==bundle['counts']['source_units']
                ready=bundle['review_ready']
                if ready:
                    try:
                        for page in bundle['passes']:
                            body,_=passes.request(page,'perplexity')
                            checks['maximum_provider_request_bytes']=max(checks['maximum_provider_request_bytes'],len(json.dumps(body).encode()))
                    except ValueError:
                        checks['provider_size_blocked_patients']+=1;checks['all_ready_requests_within_provider_cap']=False;ready=False
                row=old.execute('SELECT payload FROM contexts WHERE id=?',(digest([old_identity,old_params,patient]),)).fetchone()
                if row:
                    previous=json.loads(zlib.decompress(row[0]))
                    if previous['status']=='protected_overflow' and bundle['review_ready']:
                        checks['prior_protected_overflow_now_fully_partitioned']+=1
                        if ready and candidate is None and len(bundle['passes'])<=16:
                            candidate={'patient_id':patient,'bundle_id':key,'passes':len(bundle['passes']),
                                       'question':p['question'],'previous_status':previous['status']}
            builds.append({'title':example['title'],'build':r,'checks':checks})
            if candidate:candidates.append(candidate)
            print(json.dumps({'title':example['title'],'patients':r['patients_evaluated'],'statuses':r['statuses'],
                              'counts':r['counts'],'checks':checks,'seconds':r['elapsed_seconds']}),flush=True)
        size=[]
        def capture(body,*a,**kw):
            size.append(len(json.dumps(body).encode()));assert size[-1]<=REQUEST_MAX_BYTES,size[-1]
        meta=json.loads((args.stack/'run.json').read_text())
        with patch('clinical_core.agere.clinical.graph_planner.send',side_effect=capture):
            propose('x'*1500,args.stack,None,snapshot=meta['snapshot'],graph_catalog=catalog(args.stack),
                    observed_values=json.loads((args.stack/'cohort-values.json').read_text()))
        result={'synthetic':True,'source_digest':meta['source_digest'],'builds':builds,
                'specialist_integration_candidates':candidates,'planner_request_bytes':size[0],'new_paid_api_calls':0,
                'scope':'Source preservation, partitioning and request preflight. Not clinical accuracy, calibration or benefit.'}
        (args.output/'run.json').write_text(json.dumps(result,indent=2)+'\n')
        print('Saved '+str(args.output/'run.json'),flush=True)
    finally:old.close();new.close()


if __name__=='__main__':main()
