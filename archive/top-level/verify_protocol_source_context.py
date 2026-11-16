"""Full original-cohort clinical question → linked documentary evidence → work."""
import copy
import json
from pathlib import Path
from clinical_core.agere.active import stack_path
from clinical_core.agere.clinical import care_protocols as cp,evidence_requirements as req,program_work as work
from clinical_core.agere.clinical import selection_context,analysis_graph
from clinical_core.agere.clinical.graph_workbench import protocol_examples,snapshot_examples
from clinical_core.agere.clinical.stack_runtime import Stack


def write(path,value):path.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')


def main():
    path=stack_path();out=Path('artifacts/agere-protocol-source-context-v1');out.mkdir(parents=True,exist_ok=True)
    stack=Stack(path,allow_live=False)
    try:
        example=snapshot_examples(protocol_examples()[:1],stack.retriever.meta['snapshot']['as_of'])[0]
        question=stack.compose(example['question']);write(out/'question.json',question)
        result=question['results'][-1]['result'];print(json.dumps({'phase':'population','statuses':result['status_counts']}),flush=True)
        previous=json.loads(Path('artifacts/agere-input-fingerprints-v1/protocol-question.json').read_text())['results'][-1]['result']
        assert result['status_counts']==previous['status_counts'] and question['new_paid_api_calls']==0
        expected=json.loads(Path('artifacts/agere-medication-accounts-v1/verification.json').read_text())
        supports={}
        for key,summary in result['supporting_documentary'].items():
            supports[key]={'patients_evaluated':summary['patients_evaluated'],'status_counts':summary['status_counts']}
            assert summary['status_counts']==next(c['counts'] for c in expected['contracts'] if c['contract']==key)
        inspected=[]
        for sample in result['patients']:
            row=cp.inspect(path,sample['id']);documentary=[]
            for link in row['supporting_documentary_evidence']:
                r=req.inspect(path,link['receipt_id']);assert r['patient_id']==row['patient_id']
                assert not link['clinical_trigger_or_readiness_input'];documentary.append(r)
            inspected.append({'protocol':row,'documentary':documentary})
        write(out/'source-examples.json',inspected)
        graph=copy.deepcopy(example['graph']);graph['nodes'][-1]['parameters']['filter']='supported'
        graph['nodes'].append({'id':'work','operation':'worklist','inputs':['protocol'],'parameters':{'program':'hf_review'},
            'purpose':'Prepare review work with immutable clinical and medication-source context; no enrollment or outreach.'})
        proposal=stack.run_graph(graph);write(out/'work-question.json',proposal)
        work_detail=proposal['results'][-1]['result'];db=work.database(path)
        try:
            saved=work.load(db,'work_proposals',work_detail['proposal_id'])
            assessments=[work.load(db,'work_assessments',key) for key in saved['assessment_ids']]
        finally:db.close()
        selection_context.verify(path,[a['decision_context'] for a in assessments])
        for a in assessments:
            links=a['decision_context']['protocols'];assert len(links)==1
            assert len(cp.inspect(path,links[0]['receipt_id'])['supporting_documentary_evidence'])==2
        write(out/'work-assessments.json',assessments)
        report={'synthetic':True,'source':stack.retriever.meta['snapshot'],'protocol_identity':cp.code_identity(),
            'patients_evaluated':result['patients_evaluated'],'protocol_status_counts':result['status_counts'],
            'clinical_trigger_counts_unchanged':True,'documentary_context':supports,'work_proposal':work_detail,
            'program_assessments_with_protocol_and_item_source_provenance':len(assessments),
            'new_paid_api_calls':0,'work_items_created':0,'external_actions_executed':0,'clinical_eligibility_established':False}
        write(out/'verification.json',report);print(json.dumps(report,indent=2))
    finally:stack.close()


if __name__=='__main__':main()
