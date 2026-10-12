"""Write a current implementation receipt from actual artifacts, without hidden truth."""
from pathlib import Path
import hashlib,json,sqlite3
from clinical_core.agere.economics import usage_cost
from clinical_core.agere.clinical.warehouse_retrieval import representations

ROOT=Path(__file__).resolve().parent;OUT=ROOT/'artifacts/agere-integrated-stack-v1'
def load(name):return json.loads((OUT/name).read_text())

def main():
    run=load('run.json');ops=load('operators.json');clinical=load('clinical-review-link.json')
    db=sqlite3.connect(OUT/'stack.sqlite3');ledger=sqlite3.connect(ROOT/'artifacts/agere-full/provider-budget.sqlite3')
    examples={p.stem:json.loads(p.read_text()) for p in (OUT/'examples').glob('*.json') if p.name!='summary.json'}
    checks=0;keys=set()
    for result in examples.values():
        receipt=result.get('planner_receipt') or result.get('model_proposal')
        if receipt and receipt.get('request_hash'):keys.add(receipt['request_hash'])
        retrievals=[]
        if 'retrieval' in result:retrievals.append(result['retrieval'])
        retrievals.extend(x['result'] for x in result.get('results',[]) if x['step']['operation']=='evidence_search')
        for retrieval in retrievals:
            for row in retrieval['evidence']:
                s=row['source'];r=json.loads(db.execute('SELECT payload FROM records WHERE id=?',(s['record_id'],)).fetchone()[0])
                assert r['patient_id']==s['patient_id'] and r['event_date']==s['event_date'] and r['available_date']==s['available_date']
                assert digest(r)==s['record_hash']
                part=next(p for p in representations(r) if p['field']==s['field'])
                assert digest(part['text'])==s['text_field_hash'] and part['text'][s['start']:s['end']]==row['text']==s['quote']
                checks+=1
    for p in (OUT/'provider-receipts').glob('*.json'):
        r=json.loads(p.read_text())
        if r.get('receipt',{}).get('request_hash'):keys.add(r['receipt']['request_hash'])
    calls=[]
    for key in sorted(keys):
        row=ledger.execute('SELECT provider,model,status,response,seconds FROM calls WHERE id=?',(key,)).fetchone()
        if row:
            provider,model,status,raw,seconds=row;raw=json.loads(raw) if raw else {}
            calls.append({'request_hash':key,'provider':provider,'model':model,'status':status,'seconds':seconds,
                          'usage':raw.get('usage'),'cost_estimate':usage_cost(model,raw.get('usage'))})
    db.close();ledger.close()
    receipt={'synthetic':True,'implementation':'connected warehouse retrieval, computation, question planning and typed evidence review',
       'source_checks':checks,'source_checks_passed':True,'new_model_weights_trained':False,
       'distinct_paid_requests_for_this_build':len(calls),'provider_calls':calls,
       'estimated_api_usd':sum(x['cost_estimate']['estimated_usd'] for x in calls if x['cost_estimate']),
       'invoice_verified':False,'retrieval_counts':{k:run[k] for k in ('patients','visible_records','passages','unique_passage_texts')},
       'renal_patterns':ops['kidney'],'clinical_review_counts':clinical['counts'],
       'clinical_accuracy_measured':False,'novel_mathematics_claim':False,
       'scope':'Actual synthetic warehouse execution. Software/source integrity and working API connectors do not establish clinical accuracy or improved outcomes.'}
    (OUT/'implementation-receipt.json').write_text(json.dumps(receipt,indent=2))
    text=f'''# Agere — connected warehouse stack

October 8, 2026. All patient data are synthetic.

Open http://127.0.0.1:8884/stack. This is a working data/model/operator path.
It is still a research implementation with explicit clinical/data limitations.

## What this build adds

- Full mapped source search: structured renderings, note/free-text windows, and received OCR.
- Offline MiniLM corpus embeddings + FTS5 BM25, reciprocal-rank fusion, BGE cross-encoder reranking, source diversity.
- Existing v4 context annotations attached to matching source spans; its weights and decision authority are unchanged.
- A frontier question planner that selects registered operators. Generated SQL/Python is never executable.
- Renal reconciliation and descriptive Theil–Sen trends; duplicate provenance is retained and conflicting same-day results are excluded visibly.
- Existing HF/affordability review workflows, medication-source comparisons, source completeness, and provisional ADT patterns callable from the same plan.
- Versioned program packages with exact source-clause bindings, effective dates, immutable versions, and unknown inputs. Overlapping active versions require explicit resolution.
- Optional Perplexity and Jev patient-scoped typed reviews of retrieved evidence.
- Content-addressed local vectors, reranker scores, result caches, hosted request replay and the shared usage ledger.

## Actual warehouse execution

| Observation | Value | Artifact |
|---|---:|---|
| Synthetic patients | {run['patients']:,} | run.json / patients |
| Visible records | {run['visible_records']:,} | run.json / visible_records |
| Searchable passages | {run['passages']:,} | run.json / passages |
| Distinct embedded passage texts | {run['unique_passage_texts']:,} | run.json / unique_passage_texts |
| Patients with usable eGFR | {ops['kidney']['patients_with_usable_egfr']:,} | operators.json / kidney |
| Declining eGFR source-date patterns | {ops['kidney']['declining_source_date_pattern']:,} | operators.json / kidney |
| Usable creatinine / UACR patients | {ops['kidney']['patients_with_usable_creatinine']} / {ops['kidney']['patients_with_usable_uacr']} | operators.json / kidney |
| Existing HF review candidates | {clinical['counts']['heart_failure_review:review_candidates']:,} | clinical-review-link.json |
| Existing affordability review candidates | {clinical['counts']['affordability_review:review_candidates']:,} | clinical-review-link.json |

Review counts are existing documentation-based workflows, now connected to the planner.
They are not new diagnoses, undertreatment counts, program eligibility or outcome improvements.
The renal operator runs against the whole observed snapshot; the earlier kidney workflow has a different candidate rule and overlap handling.

## Economics and model roles

The cold corpus build encoded {run['encoder_stats']['input_tokens']:,} tokens locally in
{run['elapsed_seconds']:.2f} seconds with zero paid API calls. The {run['unique_passage_texts']:,}
content-addressed vectors serve {run['passages']:,} source-linked passages. Local compute has a cost;
no energy/hardware price or LLM-versus-stack monetary savings comparison was measured.

MiniLM is the economical corpus retrieval representation in this path; it has not been proved
better than Granite clinically. Granite remains installed in the earlier retrieval path.
BGE scores relevance. The v4 reader annotates chart context. Claude proposes operator plans.
Jev and Perplexity provide interchangeable bounded evidence proposals; no winner is declared.
Qwen is not the default reader. No new foundation weights were trained in this build.

The actual API receipts for this build estimate ${receipt['estimated_api_usd']:.8f} across
{len(calls)} distinct paid requests. This is token-price accounting from the configured prices,
not an invoice. Cached replays do not make another provider call. See implementation-receipt.json.

## What the real questions exposed

See examples/renal-question.json and examples/hf-question.json for the entire generated plans,
operator outputs, missing requirements and usage receipts. Outside-nephrology retrieval and
both typed patient reviews are also preserved. This is live integration evidence, not a new
accuracy benchmark. {checks} returned source spans/renderings were checked against the indexed records.

Risk calculation lacks creatinine/UACR and verified collection dates in this warehouse.
Current HF phenotype, actual ingestion, tolerability and maximum tolerated dose remain unresolved.
Retrieval is a bounded relevance sample, not a complete disease cohort. Broad payer, site,
demographic, clinical-date and causal filters are not implemented by the planner. The existing
source-event-date evidence compiler remains available at /explore.

Program imports accept a document/spec/bindings JSON package at /api/stack/program-import.
This supports changing program specifications without model retraining; it does not yet crawl
CMS/payer updates or prove that quoted predicates correctly implement a real-world measure.
New input names have no implicit bindings and remain unknown. The default two rules are the
previously configured synthetic development review rules, not a comprehensive program library.

Standard retrieval fusion, robust slopes and three-valued logic are established mathematics.
Physics/causal/low-rank research comparators elsewhere in the repository are not automatically
validated or promoted by this integration. Clinical action agents remain future work.

## Run

```sh
.venv/bin/python -m clinical_core.agere serve --run artifacts/agere-clinical-workflows-v2 --annotations artifacts/agere-evidence-reader-v4/warehouse --evidence-queries artifacts/agere-evidence-queries-v1 --stack artifacts/agere-integrated-stack-v1 --allow-live-stack --port 8884
.venv/bin/python run_integrated_stack.py --question "Outside nephrology records requested"
.venv/bin/python run_integrated_stack.py --live --question "Whose kidney function is declining and what data is missing?"
```

Build a fresh warehouse index with build_integrated_stack.py. Recompute analytics after
operator changes. Attach existing reader annotations with link_stack_annotations.py.
Source/model/package identities are part of cache keys. Frozen earlier receipts are preserved.
'''
    (OUT/'README.md').write_text(text)
    print(json.dumps({k:receipt[k] for k in ('source_checks','distinct_paid_requests_for_this_build','estimated_api_usd','retrieval_counts')},indent=2))

from clinical_core.contracts import digest
if __name__=='__main__':main()
