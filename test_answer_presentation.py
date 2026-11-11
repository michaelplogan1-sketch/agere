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
assert.match(resultLimitations(answer),/VERIFICATION INCOMPLETE/);
assert.match(resultLimitations(answer),/&lt;script&gt;/);
assert.doesNotMatch(resultLimitations(answer),/<script>/);
assert.match(sourceDetails({grounding:['Retained native query'],table_binding:{columns:['patient_id']}}),/Retained native query/);
console.log(JSON.stringify({checks:'passed',rows:57,pages:4,unknown_zero_empty_distinct:true,source_values_unchanged:true}));
'''
  result=subprocess.run([shutil.which('node'),'-e',script],text=True,capture_output=True)
  self.assertEqual(result.returncode,0,result.stderr)
