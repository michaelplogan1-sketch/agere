"""Focused presentation invariants: no reinterpreting flags, no loss of retained records."""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from clinical_core.agere import ask_view

class AnswerPresentation(unittest.TestCase):
 def test_complete_script_and_existing_page_contract(self):
  html=ask_view.page()
  script=re.findall(r'<script>(.*?)</script>',html,re.S)[-1]
  result=subprocess.run([shutil.which('node'),'--check'],input=script,text=True,capture_output=True)
  self.assertEqual(result.returncode,0,result.stderr)
  self.assertEqual(html.count('</script>'),2)
  self.assertNotRegex(html,r'<script[^>]+src=|<link[^>]+stylesheet|@import')
  for hook in ['Question scope','result-all-columns','data-record-toggle','Original record · JSON','Verification incomplete','/api/stack/compose']:
   self.assertIn(hook,html)
 def test_unknown_flags_metadata_and_lossless_display(self):
  html=ask_view.page()
  helpers=html[html.index('function cols('):html.index('function csvButton(')]
  presentation=(Path(ask_view.__file__).parent/'assets/answer_presentation.js').read_text()
  script=r'''
const assert=require('node:assert/strict');
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const isNum=v=>typeof v==='number'&&isFinite(v), fmt=String, state={seq:0,csv:{}};
'''+helpers+presentation+r'''
const label=fieldLabel;
const rows=[{patient_id:'P-1',profile:{patient_name:'Ada Example'},has_record:0,has_other:null,amount:0,entries:'[]',category:'coded_and_note_ef'}];
const before=JSON.stringify(rows);
assert.match(displayValue(null,'has_record',{},rows),/>Unknown</);
assert.match(displayValue(0,'has_record',{},rows),/Flag not set.*\(0\)/);
assert.doesNotMatch(displayValue(0,'has_record',{},rows),/absent|untreated|not taking|no medication/i);
assert.equal(displayValue(0,'amount',{},rows),'0');
assert.match(displayValue('[]','entries'),/No entries returned/);
assert.match(displayValue(1,'is_set',{type:'boolean',true_label:'Source confirmed'}),/Source confirmed/);
assert.equal(fieldLabel('facility_id'),'Facility ID');
const meta={table_columns:[{name:'has_record',label:'Received record',type:'boolean',false_label:'No matching source row'}],table:rows};
assert.equal(columnSpec(meta)[0].label,'Received record');
assert.match(presentationTable(rows,meta),/No matching source row/);
let out=presentationTable(rows,{table:rows});
assert.match(out,/Ada Example/);assert.match(out,/all fields/);assert.match(out,/coded_and_note_ef/);
assert.match(out,/a flag of 0 does not establish clinical absence/);
assert.equal(JSON.stringify(rows),before);
const many=Array.from({length:57},(_,i)=>({patient_id:'P-'+i,amount:i}));many[56].late_field='retained';
assert.ok(cols(many).includes('late_field')); // CSV union includes fields beyond the old 50-row scan.
for(let page=0;page<4;page++){
 out=presentationTable(many,{table:many},{page});
 for(let i=page*15;i<Math.min(page*15+15,57);i++)assert.ok(out.includes('P-'+i));
}
const answer={table:rows,patient_ids:['P-1'],verification:'unverified',limitations:['Missing evidence <script>'],facts:{source_meaning:'An exact saved finding.'}};
assert.match(answerSummary(answer),/An exact saved finding/);
assert.match(resultLimitations(answer),/Verification incomplete/);
assert.match(resultLimitations(answer),/&lt;script&gt;/);
assert.doesNotMatch(resultLimitations(answer),/<script>/);
assert.match(sourceDetails({grounding:['Retained native query'],table_binding:{columns:['patient_id']}}),/Retained native query/);
console.log(JSON.stringify({checks:'passed',rows:57,pages:4,unknown_zero_empty_distinct:true,source_values_unchanged:true}));
'''
  result=subprocess.run([shutil.which('node'),'-e',script],text=True,capture_output=True)
  self.assertEqual(result.returncode,0,result.stderr)

class DecisionBrief(unittest.TestCase):
 def test_provenance_keeps_takeover_unchecked_and_preserves_returned_figures(self):
  script=(Path(ask_view.__file__).parent/'assets/answer_presentation.js').read_text()
  setup="const assert=require('node:assert/strict'),esc=s=>String(s??''),isNum=v=>typeof v==='number',fmt=String,state={seq:0},cols=rows=>[...new Set(rows.flatMap(Object.keys))];\n"
  checks=r'''
const t={data:{answer:{verification:'checked',headline:'The result is available.',table:[{reported_count:0,reported_amount:0.000123456}],facts:{}},planner_receipt:{final_tier:'takeover',api_cost_known:true,known_api_estimate_usd:0.000123456},tiered_core:{seconds:1.23456}}};
const before=JSON.stringify(t),html=answerProvenance(t);
assert.match(html,/Unchecked/);assert.match(html,/Frontier takeover/);assert.match(html,/1.23456 s/);assert.match(html,/\$0.000123456/);
assert.ok(!html.includes('<details'));assert.doesNotMatch(html,/Code checks passed/);
assert.doesNotMatch(decisionHero(t),/returned rows|result-metric/);
assert.match(answerSnapshot(t.data.answer),/>0</);assert.match(answerSnapshot(t.data.answer),/>0.000123456</);
assert.equal(JSON.stringify(t),before);
const saved={data:{answer:{verification:'unverified',facts:{answered_by:'Original timing and cost as returned.'}},planner_receipt:{final_tier:'takeover'}}};
assert.match(answerProvenance(saved),/Original timing and cost as returned/);
assert.doesNotMatch(answerProvenance({data:{answer:{}}}),/✓/);
'''
  r=subprocess.run([shutil.which('node'),'-e',setup+script+checks],capture_output=True,text=True)
  self.assertEqual(r.returncode,0,r.stderr)

 def test_questions_share_one_conversation_and_failure_is_not_retried(self):
  html=ask_view.page();code=html[html.index('async function ask('):html.index('function loadConversation(')]
  script=r'''
const assert=require('node:assert/strict');let ids=0;const requests=[];
const state={turns:[],busy:false,cid:null},fields={};let activeTab,activeContext=null;
const $=key=>fields[key]||(fields[key]={value:'warehouse'}),store={set(){}},performance={now:()=>0};
function newId(){return 'conversation-'+(++ids)}function progressKey(){return 'p'}
function renderTurn(i){state.active=i}function beginWork(){return ()=>{}}
function completeWork(){}function syncContractDemo(){}function loadEcon(){}function loadSaved(){}
const document={dispatchEvent(){}};
async function fetch(url,opt){requests.push([url,JSON.parse(opt.body)]);const bad=requests.length===3;return {ok:!bad,status:bad?503:200,text:async()=>bad?'The stack stopped.\nOriginal failure.':JSON.stringify({answer:{status:'answered'},conversation:{resolved_question:'Returned question'}})}}
'''+code+r'''
(async()=>{
 await ask('Count the patients.');await ask('And those patients?');const failed=await ask('A hard question.');
 assert.equal(ids,1);assert.equal(requests.length,3);
 for(const [url,body] of requests){assert.equal(url,'/api/stack/compose');assert.deepEqual(Object.keys(body).sort(),['conversation_id','question']);assert.equal(body.conversation_id,'conversation-1')}
 assert.equal(failed.error,'The stack stopped.\nOriginal failure.');
})().catch(e=>{console.error(e);process.exitCode=1});
'''
  r=subprocess.run([shutil.which('node'),'-e',script],capture_output=True,text=True)
  self.assertEqual(r.returncode,0,r.stderr)

 def test_actions_remain_bound_to_returned_suggestions_and_workflow_requirements(self):
  script=(Path(ask_view.__file__).parent/'assets/answer_presentation.js').read_text()
  setup=r'''
const assert=require('node:assert/strict');
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const isNum=v=>typeof v==='number'&&isFinite(v),fmt=String,state={seq:0};
const cols=rows=>[...new Set(rows.flatMap(r=>Object.keys(r)))];
'''
  checks=r'''
const t={question:'Who needs follow-up?',archiveBlocked:true,data:{action_context:{id:'ANS-000000000000000000000001',invalidated:true},answer:{status:'partial',verification:'unverified',headline:'Two source records returned.',patient_ids:['A','B'],table:[{patient_id:'A',on_therapy:0},{patient_id:'B',on_therapy:null}],missing:['Responsible team is unknown.']}}};
const before=JSON.stringify(t);
let out=decisionActions(t);
assert.match(out,/Historical result/);
assert.match(resultLimitations(t.data.answer),/Responsible team is unknown/);
assert.match(out,/data-decision-draft="refresh"/);
assert.doesNotMatch(out,/data-suggestion-index|Start treatment|Enroll now/);
assert.doesNotMatch(decisionHero(t),/>2<\/strong>/); // Row count is not a returned clinical measure.
assert.match(decisionHero(t),/Two source records returned/);
assert.equal(decisionState(t).blocked,true);
assert.equal(JSON.stringify(t),before);
// These are recorded operational suggestions; the UI must retain the exact scope and qualification.
t.archiveBlocked=false;t.data.action_context.invalidated=false;
t.data.answer.table=[{patient_id:'A',suggested_action:'review_access',reason_detail:'Source reports a transport barrier.'},{patient_id:'B',suggested_action:'review_access',reason_detail:'Source needs verification.'},{patient_id:'B',suggested_action:'check_records',reason_detail:null}];
const actionBefore=JSON.stringify(t.data.answer);
assert.equal(recordedActionGroups(t.data.answer).length,2);
assert.equal(recordedActionGroups(t.data.answer)[0].rows.length,2);
out=decisionActions(t);
assert.match(out,/Evidence review is still required/);
assert.match(out,/2 records/);
assert.match(out,/Review work/);
assert.doesNotMatch(out,/Recommended for all|Approved|Verified intervention|eligible/);
assert.match(suggestionDetails(t,0),/Source reports a transport barrier/);
assert.match(suggestionDetails(t,0),/Source needs verification/);
assert.doesNotMatch(suggestionDetails(t,0),/check_records/);
assert.equal(JSON.stringify(t.data.answer),actionBefore);
const programs={evidence_review:{label:'Received review workflow',requirements:['Exact prerequisite <unsafe>']}};
out=workflowDetails(t,programs);
assert.match(out,/Received review workflow/);
assert.match(out,/Exact prerequisite &lt;unsafe&gt;/);
assert.match(out,/availability does not mean they were recommended/);
assert.doesNotMatch(out,/PharmD|Remote monitoring/); // No client-side program catalog is invented.
assert.match(out,/#cohort/);
const selected=workflowDetails(t,{...programs,other:{label:'Other program',requirements:[]}},'evidence_review');
assert.match(selected,/Received review workflow/);assert.doesNotMatch(selected,/Other program/);assert.match(selected,/View all review workflows/);
// Aggregate outputs keep their native value, rather than substituting their row count.
const aggregate={data:{answer:{answer_type:'count',count:10,table:[{count:10}]}}};
assert.match(decisionHero(aggregate),/>10<\/strong>/);
assert.doesNotMatch(answerSummary(aggregate.data.answer,{noMetric:true}),/result-at-a-glance/);
assert.match(decisionHero({data:{answer:{answer_type:'count',count:0}}}),/>0<\/strong>/);
assert.match(decisionHero({data:{answer:{answer_type:'count',count:null}}}),/>—<\/strong>/);
assert.match(decisionHero({data:{answer:{answer_type:'patient_set',count:0,patient_ids:[]}}}),/patients in result/);
console.log('Recorded suggestions, missing plan, workflow prerequisites and exact counts passed.');
'''
  result=subprocess.run([shutil.which('node'),'-e',setup+script+checks],text=True,capture_output=True)
  self.assertEqual(result.returncode,0,result.stderr)
