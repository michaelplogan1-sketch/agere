"""Actual population protocol -> buyer -> work -> outcomes, no provider requests."""
import json, sqlite3, time, urllib.request
from pathlib import Path
from clinical_core.contracts import digest

OUT = Path('artifacts/agere-population-therapy-v1/http-v2')
BASE = 'http://localhost:8885/api/stack/'
BUYER = 'g3-population-therapy-development'
TIMINGS = []

def save(name, row):
    file = OUT/(name+'.json'); file.parent.mkdir(parents=True,exist_ok=True)
    if file.exists():
        assert json.loads(file.read_text()) == row, name
    else: file.write_text(json.dumps(row,indent=2,allow_nan=False)+'\n')
    return row

def call(route, body=None):
    req=urllib.request.Request(BASE+route,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
    start=time.monotonic()
    with urllib.request.urlopen(req,timeout=900) as f: result=json.load(f)
    TIMINGS.append({'route':route.split('?')[0],'seconds':round(time.monotonic()-start,3)})
    result.pop('request_execution_id',None)
    return result

def retained(name, route, body):
    file=OUT/(name+'.json')
    return json.loads(file.read_text()) if file.exists() else save(name,call(route,body))

def ledger():
    with sqlite3.connect('artifacts/agere-full/provider-budget.sqlite3') as db:
        return dict(db.execute('SELECT id,status FROM calls'))

def main():
    before=ledger();save('provider-before',before)
    cfg=call('decision/population-config');active=json.loads(Path('config/agere/active_warehouse.json').read_text())
    path=Path(active['build'])/'stack'; db=sqlite3.connect((path/'stack.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
    roster=sorted(r[0] for r in db.execute('SELECT id FROM patients'));assert len(roster)==1614
    sql="""SELECT DISTINCT patient FROM records WHERE stream IN ('diagnostic_test_reports','received_diagnostic_reports')
        AND json_type(payload,'$.measurements.LVEF_percent') IN ('integer','real')
        AND json_extract(payload,'$.measurements.LVEF_percent')<=40
        AND json_extract(payload,'$.available_date')<=?"""
    expected=sorted(r[0] for r in db.execute(sql,(cfg['as_of'],)))
    expected_med=sorted(r[0] for r in db.execute("SELECT DISTINCT patient FROM records WHERE stream='reported_medication_entries' AND json_extract(payload,'$.available_date')<=?",(cfg['as_of'],)))
    port=call('capabilities?buyer='+BUYER)
    if not port['revision']:
        cap={'id':'therapy-review','name':'Synthetic therapy evidence review','description':'Internal documentary review development workflow',
            'need_tags':['medication_review'],'modality':'virtual','regions':[],'payer_types':[],'plan_ids':[],'pathway_ids':[],
            'owner':{'kind':'team','id':'synthetic-therapy-review-team'},'available':True,'capacity_slots':15,
            'capacity_as_of':cfg['as_of'],'capacity_until':cfg['as_of'],'effective_start':'2026-01-01','effective_end':'2030-01-01',
            'prerequisites':['Clinical indication, regimen, tolerability, benefits, consent and authority require verification.'],
            'provenance':'Fictional engineering declaration, not actual buyer staffing or clinical capacity.'}
        save('buyer',call('capabilities/save',{'buyer_id':BUYER,'expected_revision':0,'actor':'G3 synthetic workflow verification','capabilities':[cap]}))
    req={'question':'Across the full population, which recorded reduced-EF histories nominate therapy evidence review, what is known or unresolved in their medication accounts, and which declared services can own review and follow-up?',
        'program':'protocol:reduced_ef_therapy_review@2','cohort':cfg['cohort'],'buyer_id':BUYER,'as_of':cfg['as_of']}
    d=retained('decision','decision/population-prepare',req)
    assert d==call('decision/population-prepare',req)
    assert d['selection']['patient_ids']==roster
    nominated=sorted(m['patient_id'] for m in d['members'] if m['review_candidate'])
    assert nominated==expected and len(nominated)==15
    assert sorted(m['patient_id'] for m in d['members'] if m['protocol_review']['medications']['item_count'])==expected_med
    assert d['counts']['mapped_for_documentary_review']==15 and d['counts']['unmapped_review_candidates']==0
    assert d['counts']['need_unresolved_without_nomination']==1599
    assert d['protocol_review_counts']['documentary_readiness_unresolved']==1614
    checked_fields=0
    for m in [m for m in d['members'] if m['review_candidate']]:
        detail=retained('member-evidence/'+m['patient_id'],'decision/population-protocol-member',{'decision_id':d['id'],'patient_id':m['patient_id']})
        for item in detail['protocol_review']['medications']['items']:
            assert not item['current_regimen_established'] and not item['ingestion_established']
            for e in item['evidence']:
                raw=json.loads(db.execute('SELECT payload FROM records WHERE id=?',(e['record_id'],)).fetchone()[0])
                assert raw['patient_id']==m['patient_id'] and digest(raw)==e['record_hash']
                value=raw
                for part in e['field_path']:value=value[part]
                assert value==e['value'];checked_fields+=1
    db.close()
    save('independent-source-audit',{'status':'passed','roster':len(roster),'roster_digest':digest(roster),
        'candidate_ids':expected,'candidate_query':sql,'members_with_medication_reports':len(expected_med),'exact_field_witnesses_checked':checked_fields,
        'current_regimen_or_clinical_readiness_inferred':False})
    print(json.dumps({'stage':'population','counts':d['counts'],'protocol_counts':d['protocol_review_counts']}),flush=True)
    for pid in nominated:
        retained('routes/'+pid,'decision/population-route-member',{'decision_id':d['id'],'patient_id':pid})
        print(json.dumps({'stage':'member_route','patient_id':pid}),flush=True)
    a=save('allocation',call('decision/population-allocate',{'decision_id':d['id'],'review_limits':{'therapy-review':3},'assumption_source':'Fictional local review batch of three; no service reservations or verified staffing.'}))
    assert a['routing_complete'] and a['counts']['proposed_internal_review']==3 and a['counts']['waiting_for_internal_review_capacity']==12
    execute={'decision_id':d['id'],'allocation_id':a['result_id'],'actor':'G3 synthetic internal review verifier'}
    work=save('execution',call('decision/population-execute',execute));assert work['prepared_count']==3
    assert call('decision/population-execute',execute)['idempotent_replay']
    view=call('decision?id='+d['id']);assert view['dependencies_current'] and len(view['tasks'])==3
    for t in view['tasks']:
        assert t['binding_current'] and t['task']['state']=='handoff_prepared_for_review'
        assert t['decision_binding']['protocol_review'] and t['decision_binding']['question']==d['question']
        assert not t['task']['clinical_execution_authorized']
    save('work-reopen',{'dependencies_current':True,'task_ids':sorted(t['task']['id'] for t in view['tasks']),'question':d['question'],'protocol_and_medication_context_in_all_bindings':True})
    print(json.dumps({'stage':'internal_work','prepared':3}),flush=True)
    r=save('accountability',call('decision/population-accountability',{'decision_id':d['id'],'accountability_contract':'program_payment_evidence','scenario_id':'',
        'outcome':{'contract':'discharge_admission_clinical_30d','index_from':'2029-01-01','index_through':d['decision_date'],'available_by':d['decision_date'],'filter':'all'}}))
    assert len(r['members'])==1614 and len(r['work'])==3 and r['attributable_program_payment_cents'] is None and r['causal_savings_cents'] is None
    opened=save('accountability-member',call('decision/population-accountability-open',{'decision_id':d['id'],'result_id':r['result_id'],'patient_id':nominated[0]}))
    assert opened['current']
    # Existing accepted populations retain their identities and work bindings.
    old=[]
    with sqlite3.connect(path/'program-work.sqlite3') as w:
        keys=[(k,'preparation_identity' in json.loads(raw)) for k,raw in w.execute('SELECT id,payload FROM question_decisions') if json.loads(raw).get('schema')=='population-question-decision/1' and json.loads(raw)['program']['id'] in ('respiratory_source_review','behavioral_source_review')]
    for key,bound in keys:
        v=call('decision?id='+key)
        if bound:assert v['dependencies_current']
        else:assert not v['dependencies_current'] and 'predates dependency binding' in v['stale_reason']
        old.append({'id':key,'dependencies_current':v['dependencies_current'],'historical_unbound_preserved':not bound})
    assert ledger()==before
    save('verification',{'status':'passed','decision_id':d['id'],'population':1614,'review_candidates':15,'before_routable':0,'after_routable':15,'internal_handoffs':3,'waiting_for_internal_review_capacity':12,
        'accounts_present':864,'accounts_absent':750,'clinical_readiness_unresolved':1614,'outcome_followup_id':r['result_id'],
        'preserved_older_population_ids':old,'provider_calls_added':0,'external_actions':0,'clinical_validation':False})
    save('timings',TIMINGS)
    print('WORKFLOW VERIFIED',flush=True)

if __name__=='__main__':main()
