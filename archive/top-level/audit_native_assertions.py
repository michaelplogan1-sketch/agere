"""Audit actual graph receipts against literal received rows and full denominators."""
import argparse,collections,hashlib,json,sqlite3,zlib
from pathlib import Path
from clinical_core.contracts import digest
from clinical_core.agere.clinical import source_state as ss, interpretation_state as interp, appointment_graph as appointments
ROOT=Path('artifacts/agere-healthcare-v123/stack');OUT=Path('artifacts/agere-native-assertions-v1')
parser=argparse.ArgumentParser();parser.add_argument('--stage',default='final');args=parser.parse_args()
# Audit expectations are explicitly independent of the binding registry/reducer.
FIELDS={'financial hardship':('financial_screens','difficulty_paying_medical_bills'),
        'affordability of care':('social_screens','difficulty_paying_for_care_reported'),
        'food difficulty':('social_screens','food_difficulty_reported'),
        'transport difficulty':('social_screens','transport_difficulty_reported')}
source=sqlite3.connect(ROOT.resolve().joinpath('stack.sqlite3').as_uri()+'?mode=ro',uri=True)
state=sqlite3.connect(ROOT.resolve().joinpath('interpretation-state.sqlite3').as_uri()+'?mode=ro',uri=True)
roster=sorted(r[0] for r in source.execute('SELECT id FROM patients'))
report={};unique_assertions=set()
for case in ('cross_domain','population','social_context'):
 saved=json.loads((OUT/(args.stage+'-'+case+'.json')).read_text());assert saved['status']==200
 result=saved['response'];ids=['P-3002'] if case=='cross_domain' else roster
 members={}
 summary={'patients':len(ids),'source_states':[],'appointments':[],'comparisons':[]}
 assert result['question']==saved['request']['question']
 assert not result['clinical_execution_authorized'];assert not result['answer_coverage']['whole_question_answer_established']
 for item in result['results']:
  node,r=item['node'],item['result']
  if node['operation']=='source_state':
   p=node['parameters'];stream,field=FIELDS[p['concept']];snap=interp.snapshot(ROOT,ss.scope(p));code=ss.code_identity();ic=interp.code_identity()
   statuses=collections.Counter();yes=set();unknown=set();checked=0
   assert r['patients_evaluated']==len(ids) and r['population_digest']==digest(ids)
   for patient in ids:
    ir=ss.receipt_id(snap,patient,interpretation_code=ic);key=digest([code,ir,{k:v for k,v in p.items() if k!='filter'}])
    payload=state.execute('SELECT payload FROM source_states WHERE id=?',(key,)).fetchone();assert payload
    row=json.loads(zlib.decompress(payload[0]));assert row['patient_id']==patient
    records=[json.loads(x[0]) for x in source.execute('SELECT payload FROM records WHERE patient=? AND stream=?',(patient,stream))]
    valid=[r for r in records if type(r.get(field)) is bool and r['available_date']<='2029-08-15']
    assert all(r['respondent'] in ('patient','parent','parent_or_guardian','mother','father') for r in valid)
    latest=max((r['event_date'] for r in valid),default=None);selected=[r for r in valid if r['event_date']==latest]
    values={r[field] for r in selected};expected='unobserved' if not values else 'conflicting' if len(values)>1 else 'documented_affirmed' if True in values else 'documented_negated'
    actual=row['status']+'_'+row['value'] if row['status']=='documented' else row['status'];assert actual==expected,(case,patient,actual,expected)
    assert row['selected_date']==latest
    assert {a['source']['record_id'] for a in row['selected_assertions']}=={r['record_id'] for r in selected}
    ix={r['record_id']:r for r in records}
    for a in row['selected_assertions']:
     raw=ix[a['source']['record_id']];assert a['source']['record_hash']==digest(raw)
     assert a['evidence']=={'field':field,'value':raw[field]};assert a['clocks']['source_date']==raw['event_date'];assert a['clocks']['arrival_date']==raw['available_date'];assert a['clocks']['clinical_date'] is None
     assert a['respondent']==raw['respondent'];assert a['subject']=='patient';assert not a['current_clinical_state_established'];checked+=1;unique_assertions.add(a['id'])
    statuses[actual]+=1
    match=True if p['filter']=='all' else (None if expected in ('unobserved','conflicting') else expected=='documented_'+p['filter'])
    if match is True:yes.add(patient)
    elif match is None:unknown.add(patient)
   members[node['id']]=(yes,unknown)
   assert dict(statuses)==r['status_counts'];assert r['counts']=={'matched':len(yes),'not_matched':len(roster)-len(yes)-len(unknown),'unknown':len(unknown),'evaluated':len(roster)},r['counts']
   summary['source_states'].append({'node':node['id'],'concept':p['concept'],'filter':p['filter'],'statuses':dict(statuses),'selected_fields_hashes_subjects_clocks_checked':checked,'coverage':r['evidence_coverage']})
  elif node['operation']=='appointment_followthrough':
   assert r['patient_ids']==ids
   receipt=appointments.inspect(ROOT,r['appointment_graph_id']);assert sorted(x['patient_id'] for x in receipt['patients'])==ids
   yes=set();unknown=set();f=node['parameters']['filter']
   for row in receipt['patients']:
    if f=='all' or row['status']==f or (f=='unresolved' and row['status'] not in ('explicit_fulfilled','explicit_no_show','explicit_cancelled')):yes.add(row['patient_id'])
    elif f!='unresolved' and row['status'] not in ('explicit_fulfilled','explicit_no_show','explicit_cancelled'):unknown.add(row['patient_id'])
   members[node['id']]=(yes,unknown)
   summary['appointments'].append({'node':node['id'],'status_counts':r['status_counts'],'source_count':r['source_count']})
  elif node['operation']=='cohort':
   members[node['id']]=(set(ids),set())
  elif node['operation'] in ('intersect','union','difference'):
   sets=[members[x] for x in node['inputs']];universe=set(roster)
   if node['operation']=='intersect':
    yes=set.intersection(*(v[0] for v in sets));no=set.union(*(universe-v[0]-v[1] for v in sets))
   elif node['operation']=='union':
    yes=set.union(*(v[0] for v in sets));no=set.intersection(*(universe-v[0]-v[1] for v in sets))
   else:
    yes=sets[0][0] & (universe-sets[1][0]-sets[1][1]);no=(universe-sets[0][0]-sets[0][1]) | sets[1][0]
   unknown=universe-yes-no;members[node['id']]=(yes,unknown)
   assert r['membership_digest']==digest({'matched':sorted(yes),'unknown':sorted(unknown)})
   summary.setdefault('set_composition',[]).append({'node':node['id'],'operation':node['operation'],'counts':r['counts']})
  elif node['operation']=='compare':
   summary['comparisons']=r['comparisons'];assert not r['causal_effect_estimated']
   base=members[node['inputs'][0]][0];ey,eu=members[node['inputs'][1]]
   for comparison in r['comparisons']:
    gy,gu=members[comparison['group_node']];eligible=base-eu-gu;positive=eligible & gy;negative=eligible-gy
    expected={'group_and_endpoint':len(positive & ey),'group_without_observed_endpoint':len(positive-ey),'complement_and_endpoint':len(negative & ey),'complement_without_observed_endpoint':len(negative-ey)}
    assert comparison['cells']==expected and comparison['patients_evaluated']==len(eligible)
 summary.update(result_id=result['result_id'],elapsed_seconds=saved['elapsed_seconds'],new_paid_api_calls=result['new_paid_api_calls'],answer_coverage=result['answer_coverage']['status']);report[case]=summary
with (OUT/('source-audit-'+args.stage+'.json')).open('x') as f:json.dump({'status':'passed','roster':len(roster),'roster_digest':digest(roster),'unique_assertions_checked':len(unique_assertions),'cases':report,'qualification':'documentary engineering development only'},f,indent=2)
print(json.dumps({'status':'passed','unique_assertions_checked':len(unique_assertions),'cases':{k:{'patients':v['patients'],'states':[{x['concept']:x['statuses']} for x in v['source_states']]} for k,v in report.items()}},indent=2))
