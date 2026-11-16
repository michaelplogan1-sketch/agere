"""Pinned local bounded-classification experiment; no hosted calls or training.

Preparation/scoring own the separate synthetic oracle. Model adapters receive
only the serialized input contract, never simulator truth or expected labels.
"""
from pathlib import Path
import argparse,collections,hashlib,importlib.metadata,json,math,re,statistics,subprocess,time
from string import Formatter
from clinical_core.bridge import InsightBridge
from clinical_core.contracts import digest
from clinical_core.corpus import load_world,family_mapping,family_split
from clinical_core.inputs import load_sample
from clinical_core.agere.clinical import bounded_evidence as B
from clinical_core.agere.clinical.local_decisions import KevMLX,LiquidD1,sha256,ROOT
from clinical_core.agere.clinical import local_decisions as L

OUT=ROOT/'artifacts/agere-local-decisions-v1'
ENGINE=Path('/Users/mikel/insight-engine')
SOURCE=ROOT/'artifacts/holdout/sample.json'
AS_OF='2026-09-30'


def dump(path,value):
    Path(path).write_text(json.dumps(value,indent=2))


def rows(path):
    return [json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]


def jsonl(path,values):
    Path(path).write_text(''.join(json.dumps(v)+'\n' for v in values))


def manifests():
    source=ROOT/'research/agere/local-decision-sources'
    runtime={p:importlib.metadata.version(p) for p in ['mlx','mlx-lm','torch','transformers','safetensors','numpy','huggingface-hub']}
    def component(path,patterns=None):
        path=Path(path);files=sorted(p for p in path.rglob('*') if p.is_file() and '.cache' not in p.parts and '__pycache__' not in p.parts and '.git' not in p.parts and p.name!='agere-model.json')
        if patterns:files=[path/p for p in patterns]
        return {'path':str(path),'complete':patterns is None,'files':{str(p.relative_to(path)):{'bytes':p.stat().st_size,'sha256':sha256(p)} for p in files}}
    code=source/'kev-code';revision=subprocess.check_output(['git','-C',str(code),'rev-parse','HEAD'],text=True).strip()
    meta=json.loads((source/'kev-4b/head-metadata.json').read_text())
    for name,folder,components in [
        ('kev','kev-4b',[component(ROOT/'models/kev-4b'),component(ROOT/'models/kev-qwen35-base'),component(code,['kev/__init__.py','kev/model.py','kev/mlx_model.py','LICENSE'])]),
        ('d1','d1-3b',[component(ROOT/'models/d1-3b')])]:
        hub=json.loads((source/('kev-4b' if name=='kev' else 'd1-3B')/'hub-info.json').read_text())
        m={'schema':'agere-decision-model/1','id':hub['id'],'revision':hub['revision'],'runtime':runtime,'components':components,
           'base_revision':meta['base_revision'] if name=='kev' else None,'upstream_code':{'path':str(code),'revision':revision} if name=='kev' else None,
           'synthetic_research_only':True,'max_context_tokens':8192,'quantization':'none; native bf16 backbone'}
        target=ROOT/'models'/folder/'agere-model.json'
        if target.exists() and json.loads(target.read_text())!=m:raise ValueError('Refuse overwriting a different model manifest')
        dump(target,m);print(name,'manifest',digest(m),flush=True)


def reference(notes,subject):
    """EVALUATION ONLY: exact planted generator phrases, not lexicon predictions.

    Missing a template match is UNAVAILABLE, never a negative gold label. Thus
    free prose and undocumented simulator states are not miscalled retrieval misses.
    """
    from ie.world import phrases as P
    matches=[]
    for bank in [P.REASON_DEV,P.REASON_EVAL_ONLY]:
        for reason,templates in bank.items():
            if subject=='access':
                if reason!='no_access':continue
                label='access'
            elif subject=='a1c':
                if reason not in ('outside_lab','a1c_declined','a1c_no_access'):continue
                label=reason.removeprefix('a1c_')
            else:
                if reason not in B.labels(subject) or reason in ('none','unknown'):continue
                label=reason
            for template in templates:
                chunks=[]
                for literal,field,_,_ in Formatter().parse(template):
                    chunks.append(re.escape(literal))
                    if field=='drug':chunks.append('(?:'+'|'.join(re.escape(d) for d in P.DRUG_SHORT[subject])+')')
                    elif field in ('k','egfr','cost','a1c'):chunks.append(r'\d+(?:\.\d+)?')
                    elif field=='date':chunks.append(r'\d{4}-\d{2}-\d{2}')
                    elif field:chunks.append(r'[^.\n]{1,100}?')
                pattern=re.compile(''.join(chunks),re.I)
                for note in notes:
                    for m in pattern.finditer(note['text']):
                        matches.append({'label':label,'note_id':note['note_id'],'start':m.start(),'end':m.end(),'quote':m.group()})
    label=next((x for x in B.ORDER if any(m['label']==x for m in matches)),None)
    return {'label':label,'anchors':[m for m in matches if m['label']==label],
            'authority':'exact synthetic generator-phrase oracle; not clinician review','available':label is not None}


def anchor_in(e,a):
    start=min(e['start'],e['previous_start']) if e.get('previous_start',-1)>=0 else e['start']
    return e['note_id']==a['note_id'] and start<=a['start'] and e['end']>=a['end']


def prepare():
    if OUT.exists():raise ValueError('Experiment directory exists; preserve frozen inputs')
    OUT.mkdir(parents=True)
    bridge=InsightBridge(ENGINE);reserved=load_sample(SOURCE,AS_OF)
    family=family_mapping(json.loads((ENGINE/'data/source/synthetic_patients.json').read_text()))
    forbidden={family[p] for p in reserved.patients}
    development=load_world(ENGINE/'data/worlds/hard_dev',AS_OF)
    pool=collections.defaultdict(list)
    for pid in development.patients:
        if family[pid] not in forbidden and family_split(family[pid])=='calibration':pool[family[pid]].append(pid)
    selected=sorted(pool,key=lambda f:digest(['bounded-classification-calibration-v1',f]))[:32]
    cal={sorted(pool[f])[0]:development.patients[sorted(pool[f])[0]] for f in selected}
    allrefs={};allinputs={}
    for partition,patients in [('calibration',cal),('reserved',reserved.patients)]:
        inputs=[];refs=[]
        for pid,pf in sorted(patients.items()):
            applicable=bridge.judge.judge_patient(pf,AS_OF)['answers']
            for subject in list(applicable)+['access']:
                packet=B.retrieve(bridge.reader,pf['notes'],subject);state,questions=B.request(subject,packet,AS_OF)
                ident=digest([partition,pid,subject])
                inputs.append({'id':ident,'patient_id':pid,'family':family[pid],'subject':subject,'partition':partition,
                    'packet':packet,'state':state,'questions':questions,'input_hash':digest([state,questions]),'synthetic':True})
                ref=reference(pf['notes'],subject);ref['retrieved']=any(anchor_in(e,a) for e in packet['evidence'] for a in ref['anchors'])
                refs.append({'id':ident,**ref})
        allinputs[partition]=inputs;allrefs[partition]=refs
        jsonl(OUT/(partition+'-inputs.jsonl'),inputs);jsonl(OUT/(partition+'-reference.jsonl'),refs)
        dump(OUT/(partition+'-sources.json'),patients)
    # Separate synthetic calibration controls. These are authored labels, not
    # fabricated clinical reviews. No reserved source family is used to build them.
    controls=[
      ('mra','The patient is holding spironolactone because potassium is dangerously high.','contraindicated'),
      ('mra','The patient denies any intolerance to spironolactone and continues it.','none'),
      ('mra','Her father stopped spironolactone because of dizziness. Her own regimen was not discussed.','none'),
      ('mra','Spironolactone was held last year; that hold was resolved and she is now taking it without difficulty.','none'),
      ('sglt2','He stopped empagliflozin after recurrent genital yeast infections and will not try it again.','intolerance'),
      ('sglt2','The prior authorization for empagliflozin was denied.','pa_denied'),
      ('sglt2','Empagliflozin is approved. His hearing-aid request was denied.','none'),
      ('sglt2','If empagliflozin causes a side effect, we may discontinue it.','none'),
      ('arni','She could not afford Entresto and left the prescription at the pharmacy.','cost_abandoned'),
      ('arni','Entresto is obtained as clinic samples and the patient reports using them.','outside_fill'),
      ('bb','He has decided not to start carvedilol after discussion with his clinician.','declined'),
      ('bb','He declined vaccination. Carvedilol was continued.','none'),
      ('statin','Atorvastatin made her muscles ache, so it was discontinued.','intolerance'),
      ('statin','The patient has atorvastatin listed, but we cannot determine if the reported reaction concerns her or her sister.','unknown'),
      ('a1c','The completed HbA1c result from an outside laboratory is 7.4%.','outside_lab'),
      ('a1c','An outside HbA1c test is planned for next week. No result is available.','none'),
      ('a1c','She refused the HbA1c blood draw today.','declined'),
      ('a1c','He missed the HbA1c draw because there was no transport to the lab.','no_access'),
      ('access','She missed her appointment because her only ride was unavailable.','access'),
      ('access','He drives himself and reports no transportation barrier.','none'),
    ]
    control_inputs=[];control_refs=[]
    for i,(subject,text,label) in enumerate(controls):
        note={'note_id':'CAL-'+str(i),'date':'2026-09-01','note_type':'pcp','text':text}
        # Full literal control evidence is provided, independently of retrieval.
        e={'id':'e0','text':text,'previous':'','date':note['date'],'note_id':note['note_id'],'start':0,'end':len(text),'previous_start':-1,'source_hash':digest(note),'section':'body','note_type':'pcp'}
        packet={'evidence':[e],'candidate_count':1,'selected_count':1,'omitted_count':0}
        state,questions=B.request(subject,packet,AS_OF);ident=digest(['authored-calibration',i])
        control_inputs.append({'id':ident,'patient_id':note['note_id'],'family':'authored-calibration-'+str(i),'subject':subject,
            'partition':'calibration_control','packet':packet,'state':state,'questions':questions,'input_hash':digest([state,questions]),'synthetic':True})
        control_refs.append({'id':ident,'label':label,'available':True,'retrieved':True,'anchors':[],
            'authority':'authored synthetic calibration control; not clinician review'})
    jsonl(OUT/'calibration-control-inputs.jsonl',control_inputs);jsonl(OUT/'calibration-control-reference.jsonl',control_refs)
    files=list(OUT.glob('*.json*'))
    plan={'schema':'agere-local-decision-experiment/1','synthetic':True,'frozen_before_inference':True,
        'reserved_source':str(SOURCE),'reserved_source_sha256':sha256(SOURCE),'reserved_patients':len(reserved.patients),
        'reserved_families':sorted(forbidden),'calibration_source_families':sorted(selected),
        'family_overlap':len(forbidden&set(selected)),'counts':{k:len(v) for k,v in allinputs.items()},'authored_calibration_controls':len(controls),
        'selection':'Existing label-independent 20-patient reserved cohort; calibration is 32 separate source families from hard_dev calibration partition.',
        'contract':B.VERSION,'retrieval':{'max_passages':B.MAX_PASSAGES,'method':'Fixed subject lexical anchors, prior-sentence context, exact dedupe, newest candidates first'},
        'acceptance_rule':'Choose largest empirical calibration coverage with <=5% raw classification error among accepted rows, zero accepted errors on exception/negative controls, and at least 8 accepted rows; otherwise accept none. Not a clinical risk guarantee.',
        'reference_limits':'Exact generator phrases identify positive support only. Unmatched prose/undocumented states are unscorable for retrieval/interpretation attribution, not negative labels. Calibration controls add authored negatives/ambiguity.',
        'threshold_targets':'Calibration references only; reserved labels never set thresholds.',
        'historical_qwen_result_used_to_score_candidates':False,
        'files':{p.name:sha256(p) for p in files},
        'code':{str(p.relative_to(ROOT)):sha256(p) for p in [Path(__file__),Path(B.__file__),Path(L.__file__)]},
        'external_code':{str(ENGINE/p):sha256(ENGINE/p) for p in ['ie/engine/reader.py','ie/engine/judge.py','ie/ontology/decisions.json','ie/bench/score.py','ie/world/phrases.py']},
        'model_manifests':{str(p.relative_to(ROOT)):sha256(p) for p in [ROOT/'models/kev-4b/agere-model.json',ROOT/'models/d1-3b/agere-model.json']}}
    dump(OUT/'experiment-plan.json',plan);print(json.dumps({k:plan[k] for k in ['counts','family_overlap','authored_calibration_controls']},indent=2))


def calibrate(predictions,references):
    refs={r['id']:r for r in references};valid=[p for p in predictions if refs[p['id']]['available'] and refs[p['id']]['retrieved']]
    thresholds=sorted({0.,1.000001,*[p['interpreted']['confidence'] for p in valid if 'interpreted' in p]})
    candidates=[]
    for t in thresholds:
        accepted=[p for p in valid if 'interpreted' in p and p['interpreted']['accepted'] and p['interpreted']['confidence']>=t]
        wrong=[p for p in accepted if p['interpreted']['raw_choice']!=refs[p['id']]['label']]
        critical=[p for p in wrong if refs[p['id']]['label'] in ('contraindicated','intolerance','outside_fill','outside_lab','none','unknown')]
        candidates.append({'threshold':t,'accepted':len(accepted),'errors':len(wrong),'critical_errors':len(critical)})
    allowed=[c for c in candidates if c['accepted']>=8 and c['errors']/c['accepted']<=.05 and not c['critical_errors']]
    best=max(allowed,key=lambda c:(c['accepted'],c['threshold'])) if allowed else {'threshold':1.000001,'accepted':0,'errors':0,'critical_errors':0}
    return {**best,'calibration_reference_rows':len(valid),'all_candidates':candidates,'clinical_calibration_established':False,
            'scope':'Empirical synthetic calibration cutoff; small dependent examples do not establish clinical error control.'}


def run(name):
    plan=json.loads((OUT/'experiment-plan.json').read_text())
    for name_,h in plan['files'].items():
        if sha256(OUT/name_)!=h:raise ValueError('Frozen input changed')
    for name_,h in plan['code'].items():
        if sha256(ROOT/name_)!=h:raise ValueError('Frozen experiment code changed')
    for name_,h in plan['external_code'].items():
        if sha256(name_)!=h:raise ValueError('Frozen upstream engine changed')
    for name_,h in plan['model_manifests'].items():
        if sha256(ROOT/name_)!=h:raise ValueError('Frozen model manifest changed')
    folder=OUT/name;folder.mkdir(exist_ok=True)
    start=time.perf_counter();model=KevMLX() if name=='kev' else LiquidD1()
    dump(folder/'identity.json',{'model_identity':model.identity,'manifest':model.manifest,'load_seconds':model.load_seconds})
    if name=='kev':
        c=rows(OUT/'calibration-control-inputs.jsonl')[0]
        dump(folder/'parity.json',model.parity(c['state'],c['questions']))
    def infer(partitions):
        result=[]
        for partition in partitions:
            path=folder/(partition+'-predictions.jsonl');old={r['id']:r for r in rows(path)} if path.exists() else {}
            for i,case in enumerate(rows(OUT/(partition+'-inputs.jsonl'))):
                if case['id'] in old:
                    saved=old[case['id']]
                    if saved['model_identity']!=model.identity or saved['input_hash']!=case['input_hash']:raise ValueError('Cached result identity changed')
                    result.append(saved);continue
                before=time.perf_counter()
                try:
                    answer=model(case['state'],case['questions']);interpreted=B.interpret(answer,case['packet'],case['subject'])
                    saved={'id':case['id'],'model_identity':model.identity,'input_hash':case['input_hash'],'answer':answer,'interpreted':interpreted,'failed':False}
                except (ValueError,RuntimeError) as exc:
                    saved={'id':case['id'],'model_identity':model.identity,'input_hash':case['input_hash'],'failed':True,'error':str(exc),'failed_seconds':time.perf_counter()-before}
                with path.open('a') as f:f.write(json.dumps(saved)+'\n')
                result.append(saved)
                print(json.dumps({'model':name,'partition':partition,'completed':i+1,'failed':saved['failed']}),flush=True)
        return result
    cal=infer(['calibration','calibration-control'])
    target=folder/'calibration.json'
    fitted=calibrate(cal,rows(OUT/'calibration-reference.jsonl')+rows(OUT/'calibration-control-reference.jsonl'))
    fitted['model_identity']=model.identity;fitted['frozen_before_reserved_inference']=True
    if target.exists() and json.loads(target.read_text())!=fitted:raise ValueError('Calibration changed after freezing')
    dump(target,fitted)
    infer(['reserved'])
    predictions=[r for p in folder.glob('*-predictions.jsonl') for r in rows(p)]
    dump(folder/'run-receipt.json',{'seconds_including_load':time.perf_counter()-start,'model_identity':model.identity,
        'attempted_cases':len(predictions),'failed_calls':sum(r['failed'] for r in predictions),
        'external_calls':0,'paid_api_cost_usd':0,'total_operating_cost_usd':None,'model_weights_trained_here':False})


class PacketReader:
    def __init__(self,reader,answers):self.base=reader;self.answers=answers
    def read_chart(self,notes):return self.base.read_chart(notes)
    def ef_values(self,chart):return self.base.ef_values(chart)
    def problem_text(self,notes):return self.base.problem_text(notes)
    def evidence(self,chart,subject):
        a=self.answers.get(subject)
        if not a or a['choice'] in ('none','unknown') or not a.get('source'):return []
        s=a['source'];return [{'type':a['choice'],'date':s['date'],'text':s['text']}]
    def finalize(self,row):
        for subject,a in self.answers.items():
            if a['choice']=='unknown' and subject in row['answers'] and row['answers'][subject]['answer'] not in ('on','done'):
                row['answers'][subject]={'answer':'review:uncertain','conf':None,'sources':[],'evidence':[]}
        return row


def evaluate():
    bridge=InsightBridge(ENGINE);cases=rows(OUT/'reserved-inputs.jsonl');refs={r['id']:r for r in rows(OUT/'reserved-reference.jsonl')}
    patients=json.loads((OUT/'reserved-sources.json').read_text())
    truth={p['id']:dict(p['truth'],id=p['id']) for p in json.loads(SOURCE.read_text())}
    from ie.bench.score import score_patients,ACTIONABLE
    report={'synthetic':True,'models':{},'systems':{},'source':str(SOURCE),'plan_hash':sha256(OUT/'experiment-plan.json')}
    reference_cases=[c for c in cases if refs[c['id']]['available']]
    report['retrieval']={'cases':len(cases),'exact_positive_reference_cases':len(reference_cases),
        'reference_retrieved':sum(refs[c['id']]['retrieved'] for c in reference_cases),
        'retrieval_misses':sum(not refs[c['id']]['retrieved'] for c in reference_cases),
        'unscorable_reference_cases':len(cases)-len(reference_cases),
        'miss_cases':[{'id':c['id'],'patient_id':c['patient_id'],'subject':c['subject'],'reference':refs[c['id']]} for c in reference_cases if not refs[c['id']]['retrieved']],
        'scope':'Exact generator-phrase support only; absent matches are not classified as missing care or retrieval failure.'}
    def statuses(name,answers):
        result=[]
        for pid,pf in patients.items():
            reader=PacketReader(bridge.reader,{c['subject']:answers[c['id']] for c in cases if c['patient_id']==pid})
            result.append(reader.finalize(bridge.judge.judge_patient(pf,AS_OF,reader=reader)))
        return score_rows(name,result)
    def score_rows(name,result):
        total=correct=missed=0;details=[]
        lookup={r['patient_id']:r for r in result}
        for pid,t in truth.items():
            for subject,target in t['status'].items():
                actual=lookup[pid]['answers'].get(subject,{}).get('answer','not_identified');total+=1;correct+=actual==target
                if target in ('gap:contraindicated','gap:intolerance') and actual!=target:missed+=1
                details.append({'patient_id':pid,'subject':subject,'expected_simulator_status':target,'actual':actual})
        false_gaps=[d for d in details if d['actual'].startswith('gap:') and d['actual'][4:] in ACTIONABLE
                    and not (d['expected_simulator_status'].startswith('gap:') and d['expected_simulator_status'][4:] in ACTIONABLE)]
        value={'exact_status_correct':correct,'exact_status_total':total,'missed_exception_statuses':missed,
          'worklist':score_patients(result,truth),'false_actionable_gap_proposals':len(false_gaps),'false_actionable_gap_detail':false_gaps,
          'review_pairs':sum(a['answer']=='review:uncertain' for r in result for a in r['answers'].values()),
          'scope':'Inherited synthetic benchmark kernel, including never-offered heuristic; not actual clinical eligibility or approved care-gap recommendations.'}
        report['systems'][name]=value;dump(OUT/(name+'-status-detail.json'),details)
    full,_=bridge.analyze(patients,AS_OF);score_rows('lexicon_full_chart',full)
    for name in ['lexicon_packet','kev','d1']:
        raw={};accepted={};durations=[];failure=0;metrics=[]
        if name!='lexicon_packet':
            pred={p['id']:p for p in rows(OUT/name/'reserved-predictions.jsonl')}
            if set(pred)!={c['id'] for c in cases}:raise ValueError('Both models must complete the identical reserved contract')
            cal=json.loads((OUT/name/'calibration.json').read_text());threshold=cal['threshold']
        for case in cases:
            ref=refs[case['id']]
            if name=='lexicon_packet':
                started=time.perf_counter();a=B.lexicon(bridge.reader,case['packet'],case['subject']);selected=a
                durations.append({'seconds':time.perf_counter()-started,'input_tokens':0,'output_tokens':0})
            else:
                p=pred[case['id']]
                if p['failed']:
                    failure+=1;a={'choice':'unknown','raw_choice':'unknown','accepted':False,'confidence':0,'source':None,'support_id':None};selected=a
                else:
                    a=p['interpreted'];selected=B.interpret(p['answer'],case['packet'],case['subject'],threshold);durations.append(p['answer']['stats'])
            raw[case['id']]=a;accepted[case['id']]=selected
            if ref['available'] and ref['retrieved']:
                grounded=bool(a.get('source') and any(anchor_in(a['source'],g) for g in ref['anchors']))
                metrics.append({'id':case['id'],'correct':a['raw_choice']==ref['label'],'accepted':selected['accepted'],
                    'accepted_correct':selected['choice']==ref['label'],'source_grounded':grounded,
                    'exception':ref['label'] in ('contraindicated','intolerance'),'actual':a['raw_choice'],'expected':ref['label']})
        statuses(name+'_raw',raw)
        if name!='lexicon_packet':statuses(name+'_calibrated',accepted)
        report['models'][name]={'reference_retrieved_cases':len(metrics),'raw_interpretation_correct':sum(m['correct'] for m in metrics),
            'interpretation_errors_with_evidence_retrieved':sum(not m['correct'] for m in metrics),
            'missed_exceptions_with_evidence_retrieved':sum(m['exception'] and not m['correct'] for m in metrics),
            'raw_source_support_correct':sum(m['source_grounded'] for m in metrics),
            'accepted_reference_cases':sum(m['accepted'] for m in metrics),'accepted_reference_errors':sum(m['accepted'] and not m['accepted_correct'] for m in metrics),
            'accepted_all_cases':sum(a['accepted'] for a in accepted.values()),'all_cases':len(cases),'failed_calls':failure,
            'median_inference_seconds':statistics.median(s['seconds'] for s in durations) if durations else None,
            'total_inference_seconds':sum(s['seconds'] for s in durations),
            'peak_mlx_memory_bytes':max((s.get('peak_mlx_memory_bytes') or 0 for s in durations),default=0) or None,
            'max_observed_mps_driver_bytes':max((s.get('mps_driver_allocated_bytes') or 0 for s in durations),default=0) or None,
            'sampled_peak_mps_driver_bytes':max((s.get('sampled_peak_mps_driver_bytes') or 0 for s in durations),default=0) or None,
            'process_lifetime_peak_rss_bytes':max((s.get('process_lifetime_peak_rss_bytes') or 0 for s in durations),default=0) or None,
            'input_tokens':sum(s['input_tokens'] for s in durations),'output_tokens':sum(s['output_tokens'] for s in durations),
            'calibration':cal if name!='lexicon_packet' else None}
        jsonl(OUT/(name+'-interpretation-detail.jsonl'),metrics)
    report['winner']=None
    report['limitations']=['All data and references synthetic; no clinician-adjudicated generalization claim.',
        'The existing reserved cohort was already used by an earlier chart-to-JSON experiment; historical developer exposure is disclosed, not reused as evidence against Kev.',
        'The protocol/threshold was fixed for these candidates; no candidate tuned on reserved results.',
        'Exact template support cannot label unmatched prose or undocumented simulator states; those attribution cases remain unscorable.',
        'Model-native prompts/tokenizers differ while visible evidence and semantic choices are identical.',
        'Local operating costs, hardware and review remain unpriced; zero paid API calls is not zero total cost.']
    dump(OUT/'comparison.json',report);print(json.dumps({k:v for k,v in report.items() if k not in ('retrieval','limitations')},indent=2))


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('command',choices=['manifests','prepare','run','evaluate']);ap.add_argument('--model',choices=['kev','d1']);args=ap.parse_args()
    if args.command=='manifests':manifests()
    elif args.command=='prepare':prepare()
    elif args.command=='run':run(args.model)
    else:evaluate()
