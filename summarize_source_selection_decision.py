"""Seal the predeclared negative G2 activation decision from observed traces."""
from collections import Counter
from datetime import datetime,timezone
import hashlib,json
from pathlib import Path
from clinical_core.contracts import digest
from clinical_core.agere.active import stack_path
from clinical_core.agere.economics import usage_cost,total_cost

OUT=Path('artifacts/agere-source-selection-v1/decision')

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path):return json.loads(Path(path).read_text())

def answer_metrics(wrapper):
    a=wrapper.get('response',{})
    if not a:return {'status':wrapper.get('status','unavailable'),'answer_id':None,'inference_estimate_usd':None,'all_in_cost':total_cost(api_usd=None,local_seconds=None,compute_hour_usd=None,review_minutes=None,review_minute_usd=None)}
    usages={v['request_hash']:v for v in a.get('usage',[]) if isinstance(v,dict) and v.get('request_hash')}
    prices=[usage_cost(v.get('model',a.get('model')),v.get('usage')) for v in usages.values()]
    coverage=a.get('coverage',{})
    return {'status':'executed_unvalidated','answer_id':a['id'],'schema':a['schema'],
        'recorded_usage_requests':len(usages),'inference_estimate_usd':round(sum(v['estimated_usd'] for v in prices),6) if prices and all(prices) else None,
        'cost_basis':'All usage entries in saved answer, source passes plus reconciliation; historical failed alternatives excluded. Recorded prices, not invoices.',
        'received_units':coverage.get('received_units'),'accounted_units':coverage.get('model_accounted_units'),
        'unassessed_units':len(coverage.get('unassessed_units',[])),
        'complete_declared_scope':coverage.get('complete_declared_scope',False),
        'findings':len(a.get('findings',[])),'review_items':len(a.get('review_steps',[])),
        'unanswered':len(a.get('unanswered',[])),
        'review_burden_basis':'Counts only; no measured human review minutes or clinical acceptance.',
        'observed_request_seconds':wrapper.get('seconds',wrapper.get('elapsed_seconds')),
        'timing_comparable':False,
        'all_in_cost':total_cost(api_usd=None,local_seconds=None,compute_hour_usd=None,review_minutes=None,review_minute_usd=None)}

def main():
    protocol=read(OUT/'protocol.json');score=read('artifacts/agere-workflow-evaluation-v1/scorecard.json');stage={c['id']:c for c in score['cases']}
    assert read(OUT/'execution.json')['status']=='finished'
    delta=read(OUT/'ledger-delta.json');failed=[]
    for call in delta:
        if call['status']=='failed':
            found=list(stack_path().rglob(call['id']+'-failure.json'))
            raw=list(stack_path().rglob(call['id']+'.json'))
            evidence=read(found[0]) if found else {}
            usage=evidence.get('returned_usage')
            if not usage and raw:usage=read(raw[0]).get('response',{}).get('usage')
            call.update(usage=usage,estimate=usage_cost(call['model'],usage),failure_evidence=str(found[0]) if found else None,
                provider_response_evidence=str(raw[0]) if raw else None)
            failed.append(call)
    (OUT/'accounted-ledger-delta.json').write_text(json.dumps(delta,indent=2)+'\n')
    rows=[]
    for case in protocol['cases']:
        cid=case['id'];candidate=read(OUT.parent/(cid+'-http.json'));audit=read(OUT/(cid+'-audit.json'));run=read(OUT/(cid+'-interpretation.json'))
        trace=stage[cid]['trace_links'].get('source_interpretation');baseline=read(trace) if trace else {'status':'blocked_by_scope'}
        bm=answer_metrics(baseline);cm=answer_metrics(run)
        missing=len(audit['additional_reference_closure']['missing_received_record_ids']);off=audit['additional_counts'].get('off_question',0)
        reasons=[]
        if missing:reasons.append(f'{missing} received records in the expanded typed-reference closure are outside the selected scope.')
        if run['status']=='blocked':reasons.append(run['reason'])
        if run['status']=='failed':reasons.append(run['error'])
        if off:reasons.append(f'{off} added records are off-question in the assistant-authored documentary audit.')
        if cid=='appointment':reasons.append('Answer includes a billing finding and an overbroad absence-of-link statement despite the received reply_to_record_id; source review required.')
        if not reasons:reasons.append('Useful added content observed; independent quality, full costs and generalization remain unqualified.')
        rows.append({'case':cid,'patient':case['patient'],'question':case['question'],'selection_id':candidate['id'],
            'baseline':bm,'candidate':cm,'candidate_status':run['status'],
            'candidate_prepare_cached_seconds':read(OUT/(cid+'-prepare.json'))['seconds'],
            'record_counts':audit['counts'],'baseline_record_counts':audit['baseline_counts'],'additional_record_counts':audit['additional_counts'],
            'missing_expanded_reference_records':missing,'groups':len(candidate['source_plan']['contexts']),
            'reasons':reasons,'baseline_trace':trace,'candidate_trace':str(OUT/(cid+'-interpretation.json'))})
    appointment=read(OUT/'appointment-interpretation.json')['response']
    context=read(OUT.parent/'appointment-http.json')['source_plan']['contexts'][0]
    source=next(u for u in context['model_context']['evidence'] if u['source_ref']=='WHL-R-dee7767d1820065becc7d8' and u['kind']=='structured')
    finding=next(f for f in appointment['findings'] if 'No typed link connects these records.' in f['text'])
    audit={'status':'machine_identified_documentary_error_requires_review','answer_id':appointment['id'],'finding_id':finding['id'],
        'claim':finding['text'],'source_context_id':context['id'],'source_unit_id':source['id'],'received_reply_to_record_id':source['reported_fields']['reply_to_record_id'],
        'issue':'At least the incoming and outgoing messages have an explicit reply relationship. The blanket no-link wording is overbroad. Receipt of a syntactically valid cited answer does not validate every assertion.',
        'clinical_validated':False}
    (OUT/'answer-audit.json').write_text(json.dumps(audit,indent=2)+'\n')
    estimates=[v['estimate'] for v in delta]
    report={'schema':'agere-source-selection-decision/1','created_at':datetime.now(timezone.utc).isoformat(),
        'gate':'G2','status':'decision_complete','decision':'retain_default_candidate_not_promoted',
        'summary':'Broader selection recovers useful records but adds off-question content, misses some newly introduced reference dependencies, and cannot yet produce one complete answer across review groups. One single-group reader failed unit accounting; another returned an overbroad linkage claim. Keep it explicit and inspectable.',
        'default':'passage_reference','candidate':'schema_candidate','candidate_promoted':False,
        'snapshot':protocol['snapshot'],'cases':rows,
        'execution_counts':dict(Counter(r['candidate_status'] for r in rows)),
        'cost':{'new_inference_requests':len(delta),'validated_requests':sum(v['status']=='validated' for v in delta),'failed_requests':sum(v['status']=='failed' for v in delta),
            'estimated_api_usd_including_failed':round(sum(v['estimated_usd'] for v in estimates),6) if all(estimates) else None,
            'unpriced_requests':sum(v is None for v in estimates),'price_basis':'Existing recorded price table checked 2026-10-08; estimates, not invoices. Native token-count calls are not inference requests.',
            'human_review_minutes':None,'all_in_cost_usd':None,'cost_per_clinically_accepted_answer_usd':None,
            'limits':'No measured human review/compute rates or clinical acceptance. Multi-group complete answers blocked before inference; their completion cost is unknown, not zero. Baseline timings used older cached/reconciliation paths and are not an isolated latency benchmark.'},
        'useful_observation':'Functional candidate adds caregiver availability and walking-path hazard information with exact citations; no caregiver contact or clinical action authorized.',
        'carry_forward':['G3: durable original question, exact source/answer versions, persistent supported work and follow-up; retain grouped-answer limitation explicitly.',
            'G5: declared fresh development comparison for source-level selection, expanded reference closure and reader/linkage failures; no tuning against frozen evaluations or these saved outputs.'],
        'clinical_validated':False,'independent_quality_evaluation':False,'default_qualified_by_this_run':False,
        'label_authority':protocol['label_authority'],'evaluation_evidence_used_for_runtime_answers':False,
        'code_sha256':{str(p):sha(p) for p in map(Path,['clinical_core/agere/clinical/source_selection.py','clinical_core/agere/clinical/question_context.py','clinical_core/agere/clinical/reconciliation.py','clinical_core/agere/clinical/retrieval_source_plan.py'])},
        'evidence_sha256':{str(p):sha(p) for p in sorted(OUT.glob('*.json')) if p.name not in ('report.json','implementation-receipt.json','preservation.json')},
    }
    report['result_digest']=digest(report)
    (OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'decision':report['decision'],'counts':report['execution_counts'],'cost':report['cost'],
        'comparison':[{k:r[k] for k in ('case','additional_record_counts','missing_expanded_reference_records')}|{'baseline_cost':r['baseline']['inference_estimate_usd'],'candidate_cost':r['candidate']['inference_estimate_usd']} for r in rows]},indent=2))

if __name__=='__main__':main()
