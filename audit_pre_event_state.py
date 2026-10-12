"""Independent literal screen/date oracle plus exact source and anchor audit."""
import collections,json,sqlite3,zlib
from pathlib import Path
from clinical_core.contracts import digest
from clinical_core.agere.clinical import pre_event_state as pre, source_state as ss, interpretation_state as interp, outcomes
from verify_pre_event_state import QUESTIONS

ROOT=Path('artifacts/agere-healthcare-v123/stack');OUT=Path('artifacts/agere-pre-event-state-v1')
source=sqlite3.connect(ROOT.resolve().joinpath('stack.sqlite3').as_uri()+'?mode=ro',uri=True)
state=sqlite3.connect(ROOT.resolve().joinpath('interpretation-state.sqlite3').as_uri()+'?mode=ro',uri=True)
odb=sqlite3.connect(ROOT.resolve().joinpath('outcomes.sqlite3').as_uri()+'?mode=ro',uri=True)
roster=sorted(r[0] for r in source.execute('SELECT id FROM patients'))
raw_cache={};screens=collections.defaultdict(list);admissions=collections.defaultdict(list)
for (payload,) in source.execute("SELECT payload FROM records WHERE stream IN ('social_screens','adt')"):
 r=json.loads(payload);raw_cache[r['record_id']]=r
 if r['source_table']=='social_screens':screens[r['patient_id']].append(r)
 elif r.get('event') in ('admission','A01'):admissions[r['patient_id']].append(r)
def raw(rid,pid):
 if rid not in raw_cache:
  found=source.execute('SELECT payload FROM records WHERE id=? AND patient=?',(rid,pid)).fetchone();assert found
  raw_cache[rid]=json.loads(found[0])
 r=raw_cache[rid];assert r['patient_id']==pid;return r

summary={};selected_refs=set();clinical_refs=set();anchor_refs=set()
for case in QUESTIONS:
 capture=json.loads((OUT/('candidate-'+case+'.json')).read_text());assert capture['status']==200
 result=capture['response'];assert result['question']==capture['request']['question'];assert not result['clinical_execution_authorized']
 ids=['P-3002'] if case=='patient' else ['SYN-67F2F871-F14'] if case=='indexed_patient' else roster
 nodes={x['node']['id']:x for x in result['results']};states=[]
 for item in result['results']:
  node,detail=item['node'],item['result']
  if node['operation']!='pre_event_state':continue
  assert detail['patients_evaluated']==len(ids) and detail['population_digest']==digest(ids)
  parent=nodes[node['inputs'][0]]['node'];ap=parent['parameters'];assert parent['operation']=='outcome' and ap['filter']=='all'
  prep=outcomes.prepare(ROOT,ap);snap=interp.snapshot(ROOT,ss.scope(node['parameters']));p=node['parameters']
  code=pre.code_identity();ic=interp.code_identity();statuses=collections.Counter();exclusions=collections.Counter();checked=0
  for pid in ids:
   iid=ss.receipt_id(snap,pid,interpretation_code=ic);oid=outcomes.receipt_id(prep,pid)
   key=digest([code,iid,oid,{k:v for k,v in p.items() if k!='filter'}])
   found=state.execute('SELECT payload FROM pre_event_states WHERE id=?',(key,)).fetchone();assert found
   row=json.loads(zlib.decompress(found[0]));anchor=json.loads(zlib.decompress(odb.execute('SELECT payload FROM receipts WHERE id=?',(oid,)).fetchone()[0]))
   assert row['patient_id']==pid and row['outcome_receipt_id']==oid
   assert not row['current_clinical_state_established'] and not row['temporal_scope']['historical_system_knowledge_established']
   index=row['anchor']['index'];day=index['date'] if index and not row['anchor']['problem'] else None
   if prep['contract']['basis']=='source_event':
    candidates=[r for r in admissions[pid] if r['available_date']<=ap['available_by'] and ap['index_from']<=r['event_date']<=ap['index_through']]
    expected=min((r['event_date'] for r in candidates),default=None)
    assert day==expected,(case,pid,day,expected)
   if index:
    for eid in index['evidence_ids']:
     event=anchor['evidence'][eid];r=raw(event['source']['record_id'],pid)
     assert event['record_hash']==digest(r);assert event['arrival_date']<=ap['available_by'];anchor_refs.add(event['id'])
     for ref in event.get('clinical_date_evidence',[]):
      cr=raw(ref['record_id'],pid);assert digest(cr)==ref['record_hash'];assert cr[ref['field']]==ref['value'];assert ref['arrival_date']<=ap['available_by'];clinical_refs.add((cr['record_id'],ref['field']))
      if prep['contract']['basis']=='clinical':assert str(cr[ref['field']])[:10]==day
   actual=row['status']+'_'+row['value'] if row['status']=='documented' else row['status']
   if p['concept'] in ('food difficulty','transport difficulty'):
    field={'food difficulty':'food_difficulty_reported','transport difficulty':'transport_difficulty_reported'}[p['concept']]
    observed=[r for r in screens[pid] if type(r.get(field)) is bool]
    assert all(r['respondent'] in ('patient','parent_or_guardian','parent','mother','father') for r in observed)
    prior=[];unresolved=False;reasons=collections.Counter()
    for r in observed:
     d,a=r.get('event_date'),r.get('available_date')
     if day is None:reason='anchor_unresolved'
     elif a and a>ap['available_by']:reason='after_receipt_cutoff'
     elif d and d>day:reason='documented_after_index'
     elif a and a>day:reason='received_after_index'
     elif not a:reason='unknown_receipt_date';unresolved=True
     elif not d:reason='unknown_documentary_date';unresolved=True
     elif a==day or d==day:reason='same_day_order_unknown';unresolved=True
     else:prior.append(r);continue
     reasons[reason]+=1
    latest=max((r['event_date'] for r in prior),default=None);selected=[r for r in prior if r['event_date']==latest];values={r[field] for r in selected}
    expected=('temporal_anchor_unresolved' if day is None else 'unresolved_temporal_evidence' if unresolved else 'unobserved' if not values else 'conflicting' if len(values)>1 else 'documented_affirmed' if True in values else 'documented_negated')
    assert actual==expected,(case,pid,actual,expected)
    assert {a['source']['record_id'] for a in row['selected_assertions']}=={r['record_id'] for r in selected}
    assert row['selected_date']==latest and row['temporal_evidence']['assertion_reasons']==dict(reasons)
   for a in row['selected_assertions']:
    r=raw(a['source']['record_id'],pid);assert digest(r)==a['source']['record_hash']
    assert a['clocks']['arrival_date']<day and a['clocks']['arrival_date']<=ap['available_by']
    assert a['clocks'][pre.FIELDS[p['clock']]]<day
    assert a['evidence']['value']==r[a['evidence']['field']];selected_refs.add(a['id']);checked+=1
   statuses[actual]+=1;exclusions.update(row['temporal_evidence']['assertion_reasons'])
  assert dict(statuses)==detail['status_counts'];assert dict(exclusions)==detail['temporal_exclusion_counts']
  assert p['filter']=='all' and detail['counts']['matched']==len(ids) and sum(statuses.values())==len(ids)
  states.append({'concept':p['concept'],'statuses':dict(statuses),'temporal_reasons':dict(exclusions),'selected_assertions_audited':checked})
 summary[case]={'patients':len(ids),'result_id':result['result_id'],'states':states,'seconds':capture['elapsed_seconds'],'paid_calls':result['new_paid_api_calls']}
report={'status':'passed','roster_count':len(roster),'roster_digest':digest(roster),'cases':summary,
 'unique_selected_assertions':len(selected_refs),'unique_anchor_events':len(anchor_refs),'clinical_date_source_fields':len(clinical_refs),
 'oracle':'Independent literal source-screen fields and strict date comparisons; independent raw admission-notification index selection. Clinical anchors retain received encounter field/hash lineage. Care completion is unobserved, not independently clinically adjudicated.',
 'qualification':'Development engineering evidence; no clinical/model/generalization or causal claim.'}
with (OUT/'source-audit.json').open('x') as f:json.dump(report,f,indent=2)
print(json.dumps(report,indent=2))
