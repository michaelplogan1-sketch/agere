"""Local runtime parity, cache replay and unrelated-context sensitivity audit."""
from pathlib import Path
import json,tempfile,time
from evaluate_context_reader import predictions
from clinical_core.agere.clinical.evidence_reader import AXES
from clinical_core.agere.clinical.context_reader import ContextReader
from clinical_core.agere.clinical.scoped_reader import noise_variants
from clinical_core.agere.clinical.relation_learning import write
ROOT=Path(__file__).resolve().parent;OUT=ROOT/'artifacts/agere-evidence-reader-v4'

def main():
    rows=json.loads((OUT/'fresh-cases.json').read_text());frozen=json.loads((OUT/'fresh-predictions-before-labels.json').read_text())
    with tempfile.TemporaryDirectory() as tmp:
        reader=ContextReader(OUT,Path(tmp)/'predictions.sqlite3')
        try:
            before=time.perf_counter();results=reader.predict([r['input'] for r in rows]);cold=time.perf_counter()-before
            assert all({a:p['axes'][a]['label'] for a in AXES}==frozen['typed_context'][r['example_id']] for p,r in zip(results,rows))
            before=time.perf_counter();warm=reader.predict([r['input'] for r in rows]);seconds=time.perf_counter()-before
            assert warm==results
            assert all(p['decision']=='abstain' and p['decision_authority'] is False and p['care_gap_established'] is False for p in results)
            report={'synthetic':True,'runtime_matches_frozen_predictions':len(rows),'cache_replay_identical':len(rows),'cold_seconds':cold,'warm_seconds':seconds,'new_paid_api_calls':0,'scope':'Engineering identity and cache checks; no clinical validity claim.'}
            evaluation=json.loads((OUT/'fresh-evaluation.json').read_text())
            assert evaluation['review_only_connection_gate_passed'] is True
            metrics={a:{'teacher_agreement_examples':m['teacher_consensus_cases'],'local_matches_teacher_consensus':m['matches']['typed_context']} for a,m in evaluation['metrics'].items()}
            write(OUT/'warehouse-transfer.json',{'synthetic':True,'model_identity':reader.identity,'examples':len(rows),'metrics':metrics,'review_candidates':len(json.loads((OUT/'fresh-review-queue.json').read_text())),
                'scope':'Frozen primary model; new patient/clause groups in the same synthetic warehouse. Machine agreement is not clinical validation.'})
        finally:reader.close()
    # Append unrelated items to bounded diagnostic excerpts. These are no longer
    # fresh evidence; invariance is about model stability, not semantic accuracy.
    changed=[]
    for r in rows:
        for i,x in enumerate(noise_variants(r['input'])[1:]):changed.append({**r,'example_id':r['example_id']+':noise:'+str(i),'input':x})
    result=predictions(changed);sensitivity={}
    for variant in result:
        sensitivity[variant]={a:sum(result[variant][r['example_id']][a]!=frozen[variant][r['example_id'].split(':noise:')[0]][a] for r in changed) for a in AXES}
    report['unrelated_context_mutations']={'pairs':len(changed),'changed_axis_predictions':sensitivity,'scope':'Unrelated additions should not change target semantics. Stability does not prove correctness; original cases already exposed.'}
    write(OUT/'runtime-audit.json',report);print(json.dumps(report,indent=2))

if __name__=='__main__':main()
