"""One execution path from rich observable warehouse through Agere's installed layers."""
from __future__ import annotations
import json,time,html
from collections import Counter
from pathlib import Path
import numpy as np
from .warehouse import Warehouse
from .narrative_sources import retrieval_notes
from .operators import VERSION,patient_analysis,process_paths,admission_episodes
from .policy import Program
from .mathematics import claim_development
from .knowledge import catalog
from .training import ActionSpecialist,neural_text
from .gateway import BudgetLedger,PersistentGateway,supported_task
from ..contracts import digest

ROOT=Path(__file__).resolve().parents[2]

def specialist_pass(patients,answers,model_path,output,max_cases):
    from ..local_encoders import LocalEncoder,LocalReranker
    from ..hybrid_retrieval import retrieve
    import joblib
    from scipy.special import softmax
    local=ActionSpecialist(model_path)
    granite=LocalEncoder(ROOT/'models/granite-english-r2',pooling='cls',cache=output.parent/'encoder-cache.sqlite3')
    bio=LocalEncoder(ROOT/'models/bioclinical-modernbert',pooling='mean',cache=output.parent/'encoder-cache.sqlite3')
    ranker=LocalReranker(ROOT/'models/bge-reranker-v2-m3',cache=output.parent/'encoder-cache.sqlite3')
    head=joblib.load(model_path/'bioclinical-head.joblib')
    if bio.identity!=local.manifest['encoder_identity']:raise ValueError('Clinical encoder/head version mismatch')
    groups={}
    for pid,a in answers.items():groups.setdefault(a['learned']['proposed_action'],[]).append(pid)
    selected=[]
    while len(selected)<max_cases and any(groups.values()):
        for action in sorted(groups):
            if groups[action] and len(selected)<max_cases:selected.append(groups[action].pop(0))
    results=[]
    try:
        for pid in selected:
            p=patients[pid];a=answers[pid];cut=a['as_of'];notes=[]
            notes=retrieval_notes(pid,p['records'])
            query='Find evidence supporting or contradicting the proposed next step: '+a['learned']['proposed_action'].replace('_',' ')
            packet=retrieve(granite,pid,notes,query,cut,top_k=24)
            packet['evidence']=ranker.rank(query,packet['evidence'],top_k=8)
            packet['unselected_chunks']=packet['indexed_chunks']-len(packet['evidence'])
            inp={'as_of':cut,'observed_records':p['records']}
            windows=bio.windows(neural_text(inp));vector=bio.encode([w['text'] for w in windows]).mean(axis=0,keepdims=True)
            probability=softmax(head.decision_function(vector)/local.manifest['models']['bioclinical']['temperature'],axis=1)[0]
            results.append({'patient_id':pid,'retrieval':packet,'bioclinical_proposal':str(head.classes_[probability.argmax()]),
                'bioclinical_score':float(probability.max()),'lexical_proposal':a['learned']['proposed_action'],
                'models_agree':str(head.classes_[probability.argmax()])==a['learned']['proposed_action'],
                'meaning':'Action proposals and relevance; not a proof of clinical entailment',
                'source_quotes_verified':all(e['text']==next(n['text'] for n in notes if n['note_id']==e['note_id'])[e['start']:e['end']] for e in packet['evidence'])})
        return {'cases':results,'compute':{'granite':granite.stats,'bioclinical':bio.stats,'bge':ranker.stats},
                'scope':'Bounded cost-aware expensive specialist pass; cheap operators and learned text head run across the entire roster'}
    finally:granite.close();bio.close();ranker.close()

def run(release,output,*,model_path,as_of='2026-09-30',max_neural_cases=12,projection=None,document_extractions=None):
    if output.exists():raise ValueError('Use a fresh output directory')
    if not 0<=max_neural_cases<=100:raise ValueError('Specialist limit must be 0..100')
    output.mkdir(parents=True);started=time.perf_counter()
    warehouse=Warehouse(output.parent/'warehouse.sqlite3')
    policy_specs=json.loads((ROOT/'config/agere/programs.json').read_text());programs=[Program(s) for s in policy_specs]
    model=ActionSpecialist(model_path);answers={};hits=0
    try:
        rid,intake=warehouse.ingest(release);patients,snapshot=warehouse.snapshot(rid,as_of)
        extraction_count=0
        if document_extractions:
            envelope=json.loads(Path(document_extractions).read_text())
            bound={e['record_id']:e for e in envelope['records']}
            for p in patients.values():
                for r in p['records']:
                    if r['record_id'] in bound:
                        e=bound[r['record_id']]
                        if r['source_table']!='documents' or r['patient_id']!=e['patient_id']: raise ValueError('OCR identity binding mismatch')
                        r.update(extracted_text=e['text'],extraction_method=e['method'],actual_extraction=e)
                        extraction_count+=1
        operator_hash=digest({name:(Path(__file__).parent/name).read_text() for name in ('operators.py','workflow.py','training.py','policy.py','narrative.py','narrative_sources.py')})
        keys={pid:digest({'records':p,'as_of':as_of,'operators':operator_hash,'policy':[q.hash for q in programs],
                        'model':model.manifest['model_id']}) for pid,p in patients.items()}
        pending=[]
        for pid,p in patients.items():
            existing=warehouse.cached(keys[pid])
            if existing:answers[pid]=existing;hits+=1
            else:pending.append(pid)
        # Bounded feature batches avoid materializing all vectorized charts at once.
        for start in range(0,len(pending),128):
            ids=pending[start:start+128]
            learned=model.predict([{'as_of':as_of,'observed_records':patients[pid]['records']} for pid in ids])
            for pid,proposal in zip(ids,learned):
                p=patients[pid];a=patient_analysis(p['patient'],p['records'],as_of,programs)
                from .workflow import analyze as evidence_workflow
                a.update(as_of=as_of,learned=proposal,admissions=admission_episodes(p['records'],as_of))
                a['source_workflow']=evidence_workflow(p['records'],a,as_of)
                warehouse.save(keys[pid],a);answers[pid]=a
        with (output/'patients.jsonl').open('w') as f:
            for pid,a in sorted(answers.items()):f.write(json.dumps(a,allow_nan=False)+'\n')
        flag_counts=Counter(f['kind'] for a in answers.values() for f in a['flags'])
        cards=[{'id':k,'title':k.replace('_',' ').capitalize(),'patients':v,
                'patient_ids':[pid for pid,a in answers.items() if any(f['kind']==k for f in a['flags'])],
                'scope':'Synthetic observed evidence or review candidates; inspect per-patient status'} for k,v in flag_counts.most_common()]
        (output/'insights.json').write_text(json.dumps(cards,indent=2))
        # Existing adjudication/conditional model remains connected through its explicit adapter.
        legacy={}
        if projection:
            from ..stack import IntegratedStack
            from ..bridge import InsightBridge
            from ..corpus import load_world
            from ..joint_observation import infer_joint,observed_features,SUBJECTS
            stack=IntegratedStack(output.parent/'conditional.sqlite3',InsightBridge(ROOT.parent/'insight-engine'))
            try:
                batch=load_world(projection,as_of)
                if set(batch.patients)!=set(patients): raise ValueError('Legacy projection roster differs from native warehouse')
                base=stack.run(batch,namespace='ecosystem:'+rid,max_packets=0)
                joint=json.loads((ROOT/'artifacts/agere-joint-v1/joint-model.json').read_text())
                hypotheses={c['case_id']:infer_joint(joint,observed_features(c)) for c in base['cases'] if c['subject'] in SUBJECTS}
                (output/'conditional.json').write_text(json.dumps(base))
                (output/'joint-hypotheses.json').write_text(json.dumps(hypotheses))
                legacy={'summary':base['summary'],'compute':base['compute'],'joint_hypotheses':len(hypotheses),
                        'adapter_scope':'Legacy HF/A1c reader; richer source distinctions retained separately in native operators',
                        'joint_domain_shift':'Trained on earlier simulator; no accuracy or calibration claim on this warehouse'}
            finally:stack.close()
        specialists=specialist_pass(patients,answers,model_path,output,max_neural_cases) if max_neural_cases else {'cases':[],'compute':{}}
        (output/'specialists.json').write_text(json.dumps(specialists,indent=2))
        from .graph import patient_graph
        (output/'source-graphs.json').write_text(json.dumps({c['patient_id']:patient_graph(patients[c['patient_id']]['patient'],patients[c['patient_id']]['records']) for c in specialists['cases']}))
        # Prepare exact provider jobs, including Perplexity, without automatically spending across all patients.
        ledger=BudgetLedger(output.parent/'provider-budget.sqlite3',legacy_summary=ROOT/'artifacts/live-provider-pilot/summary.json')
        try:
            gateway=PersistentGateway(ledger);jobs=[]
            for case in specialists['cases'][:3]:
                evidence=case['retrieval']['evidence'][:3]
                from ..providers import EvidenceTask
                task=EvidenceTask('Appropriate next evidence-supported step',as_of,
                    {'unknown':'Absent or contradictory evidence',**{x:x.replace('_',' ') for x in model.manifest['classes']}},
                    [{'id':e['note_id']+':'+str(e['start']),'note_id':e['note_id'],'date':e['event_date'],'text':e['text']} for e in evidence])
                for provider in gateway.MODELS:jobs.append({'patient_id':case['patient_id'],'provider':provider,**gateway.classify(provider,task)})
            (output/'provider-jobs.json').write_text(json.dumps(jobs,indent=2));budget=ledger.receipt()
        finally:ledger.close()
        knowledge=catalog();(output/'research-catalog.json').write_text(json.dumps(knowledge,indent=2))
        records=[r for p in patients.values() for r in p['records']]
        from .evidence_index import build as build_evidence_index
        narrative_index=build_evidence_index(output/'evidence.sqlite3',patients,snapshot)
        (output/'narrative-coverage.json').write_text(json.dumps(narrative_index,indent=2))
        population={'process_paths':process_paths(patients),'claim_development':claim_development([r for r in records if r['source_table']=='medical_claims'],as_of),
            'input_requirements':{
                'creatinine_risk':'Creatinine results are absent in this release; eGFR trend is available',
                'snf_pcp_outcomes':'Needs attributed outcome cohort, eligible denominators, disease/DRG, baseline covariates, independent family groups',
                'sepsis_outcomes':'Needs episode phenotype, onset and intervention times, severity and outcomes; outpatient encounters cannot supply these',
                'causal_intervention':'Needs treatment assignment, follow-up outcome, confounders and independent groups',
                'contract_performance':'Needs signed effective-dated contract rules and measure counts by site',
                'network_leakage':'Structured network status and reasons not present; narrative mentions available',
                'program_eligibility':'Compiler ready; actual payer-specific eligibility clauses must be supplied',
                'futility':'Not an automated label; goals/preferences and outcome evidence needed for clinical deliberation'}}
        (output/'population.json').write_text(json.dumps(population,indent=2))
        result={'schema':'agere-full-stack/1','synthetic':True,'as_of':as_of,'release':rid,
            'summary':{'patients':len(patients),'visible_records':snapshot['visible_records'],'patient_analyses':len(answers),'actual_document_extractions':extraction_count,
                'insight_types':len(cards),'candidate_counts':dict(flag_counts),'learned_action_proposals':dict(Counter(a['learned']['proposed_action'] for a in answers.values())),
                'source_workflow':dict(Counter(a['source_workflow']['status'] for a in answers.values())),
                'routing':dict(Counter(a['learned']['route'] for a in answers.values())),'neural_cases':len(specialists['cases'])},
            'intake':intake,'snapshot':snapshot,'models':model.manifest,'conditional_stack':legacy,'narrative':narrative_index,
            'compute':{'elapsed_seconds':time.perf_counter()-started,'patient_cache_hits':hits,'new_patient_analyses':len(pending),
                'specialists':specialists['compute'],'new_paid_api_calls':0,'total_operating_cost_usd':None},
            'provider_budget':budget,'input_requirements':population['input_requirements'],
            'limitations':['All patients and labels synthetic','Shared-generator family calibration is not external clinical validation',
                           'Local laptop batch runtime; not distributed production infrastructure','Execution agents intentionally excluded',
                           'Narrative assertions use bounded rules; broad clinical semantics still require task-specific validated models',
                           'Full-text search is lexical evidence retrieval, not a population denominator or complete answer']}
        (output/'run.json').write_text(json.dumps(result,indent=2,allow_nan=False));write_report(result,cards,specialists,output/'index.html')
        return result
    finally:warehouse.close()

def write_report(result,cards,specialists,path):
    esc=html.escape
    card_html=''.join('<article><div class="number">'+str(c['patients'])+'</div><h3>'+esc(c['title'])+'</h3><p>'+esc(c['scope'])+'</p></article>' for c in cards)
    cases=''
    for c in specialists['cases']:
        quotes=''.join('<blockquote>'+esc(e['text'])+'<cite>'+esc(e['note_id'])+' · '+esc(e['event_date'])+'</cite></blockquote>' for e in c['retrieval']['evidence'])
        cases+='<details><summary>'+esc(c['patient_id'])+' · '+esc(c['lexical_proposal'].replace('_',' '))+'</summary><p>BioClinical proposal: '+esc(c['bioclinical_proposal'])+'. Source quotes verified: '+str(c['source_quotes_verified'])+'</p>'+quotes+'</details>'
    page='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Agere | Running stack</title>
<style>:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#0b171b;color:#eaf1ea;font:16px/1.6 system-ui}main{max-width:1220px;margin:auto;padding:64px 30px}h1{font-size:clamp(42px,7vw,86px);line-height:1.02;letter-spacing:-.055em;font-weight:500;max-width:850px}h2{font-weight:500;font-size:30px;margin-top:60px}.eyebrow{color:#86d6be;text-transform:uppercase;letter-spacing:.15em;font-size:12px}.intro{font-size:22px;max-width:820px;color:#b7c7c5}.flow{display:flex;flex-wrap:wrap;gap:8px;margin:32px 0}.flow span{border:1px solid #385558;padding:10px 16px;border-radius:30px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px}article{background:#11252a;padding:25px;border-top:2px solid #87cbbb;border-radius:8px}.number{font-size:48px;letter-spacing:-.04em}h3{font-size:18px}p{color:#b7c7c5}details{padding:20px 0;border-top:1px solid #385558}summary{cursor:pointer}blockquote{white-space:pre-wrap;overflow-wrap:anywhere;border-left:2px solid #d0b88b;padding-left:20px;margin-left:0}cite{display:block;color:#86d6be;font-size:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere;padding:24px;background:#11252a}a{color:#86d6be}</style><main><div class="eyebrow">Agere / actual local execution / synthetic data</div><h1>From scattered records.<br>To supported next steps.</h1><p class="intro">The source ledger, clinical operators, trained specialists and mathematical layers share one runtime. Every result retains its source and its unknowns.</p>
<div class="flow"><span>Source evidence</span><span>Identity + time</span><span>Ontology</span><span>Clinical operators</span><span>Learned specialists</span><span>Conditional reasoning</span><span>Population insights</span></div>'''
    page+='<h2>What this run found</h2><p>Counts are synthetic review candidates, not confirmed missed care.</p><div class="grid">'+card_html+'</div><h2>Inspect the evidence</h2>'+cases
    page+='<h2>Execution receipt</h2><pre>'+esc(json.dumps({'summary':result['summary'],'compute':result['compute'],'snapshot':result['snapshot']},indent=2))+'</pre>'
    page+='<h2>Where additional data is needed</h2><pre>'+esc(json.dumps(result['input_requirements'],indent=2))+'</pre><p><a href="run.json">Full run receipt</a> · <a href="research-catalog.json">Research and mathematics</a> · <a href="population.json">Population operators</a> · <a href="specialists.json">Model evidence</a></p></main></html>'
    path.write_text(page)
