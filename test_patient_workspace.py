"""Patient presentation must preserve membership, identity, unknowns and source scope."""
import json
import re
import shutil
import subprocess
import unittest

from clinical_core.agere.deliverables import patient_dashboard as dashboard


class PatientWorkspace(unittest.TestCase):
    def test_patient_switching_and_grouping_preserve_scope_and_original_rows(self):
        script = dashboard.PATIENT_WORKSPACE_JS + r'''
const assert=require('node:assert/strict');
const rows=Array.from({length:57},(_,i)=>({patient_id:'P-'+i,patient_name:'Person '+i,group:i%4,value:i===0?0:null}));
rows.push({...rows[0],duplicate_source:'preserved'});
const input={question:'Original population question',source:{as_of:'2030-05-27'},answer:{status:'partial',verification:'unverified'},rows,
profiles:{patients:{'P-0':{patient_id:'P-0',display_name:'Alex One',preferred_language:'Spanish',interpreter_needed:0,latest_measurements:[{concept:'egfr',value_canonical:0,event_date:'2029-01-01',record_id:'R-1'}]},'P-1':{patient_id:'P-OTHER',display_name:'Wrong profile'}}},
reviews:{'P-1':{patient_id:'P-OTHER',patient_name:'Wrong review',program_review:{rationale:['Wrong person rationale']}}}};
const before=JSON.stringify(input),people=PatientWorkspace.people(input);
assert.equal(people.length,57);assert.equal(people[0].rows.length,2);assert.equal(people[0].rows[0].value,0);assert.equal(people[1].rows[0].value,null);
assert.ok(PatientWorkspace.groupFields(people).includes('group'));
assert.equal(people[1].profile.display_name,undefined);assert.equal(people[1].review.patient_name,undefined);
const host={innerHTML:'',classList:{add(){}},querySelector(){return null},contains(){return true}};
const view=PatientWorkspace.mount(host,input);assert.equal(view.patientIds.length,57);
assert.match(host.innerHTML,/Know who is here/);assert.match(host.innerHTML,/57 patients/);
function click(attrs){const dataset=Object.fromEntries(Object.entries(attrs).map(([k,v])=>[k.replace(/^data-/,'').replace(/-([a-z])/g,(_,x)=>x.toUpperCase()),v]));
 host.onclick({target:{closest:()=>({dataset,hasAttribute:k=>Object.hasOwn(attrs,k)})}})}
const visited=new Set();for(let page=0;page<10;page++){for(const id of host.innerHTML.matchAll(/data-pw-patient="(P-\d+)"/g))visited.add(id[1]);if(page<9)click({'data-pw-page':'1'})}
assert.equal(visited.size,57);
view.openPatient('P-0');assert.match(host.innerHTML,/Alex One/);assert.match(host.innerHTML,/Why this patient is in the deliverable/);
assert.match(host.innerHTML,/>0 /);assert.doesNotMatch(host.innerHTML,/Wrong profile|Wrong review|Wrong person rationale/);
click({'data-pw-tab':'Social & access'});assert.match(host.innerHTML,/Spanish/);assert.match(host.innerHTML,/Interpreter needed flag/);assert.match(host.innerHTML,/current status is unknown/);
view.openPatient('P-1');assert.doesNotMatch(host.innerHTML,/Spanish|R-1|Wrong profile|Wrong review/);
const unchanged=host.innerHTML;view.openPatient('OUTSIDE');assert.equal(host.innerHTML,unchanged);
assert.equal(JSON.stringify(input),before);assert.equal(PatientWorkspace.mount(host,{rows:[{count:10}]}),null);
console.log(JSON.stringify({patients:57,original_rows:58,all_reachable:true,patient_identity_guard:true,zero_unknown_retained:true}));
'''
        result = subprocess.run([shutil.which('node'), '-e', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_social_account_retains_qualification_and_does_not_become_a_care_instruction(self):
        script = dashboard.PATIENT_WORKSPACE_JS + r'''
const assert=require('node:assert/strict');
const host={innerHTML:'',classList:{add(){}},querySelector(){return null},contains(){return true}};
const input={rows:[{patient_id:'A'}],reviews:{A:{patient_id:'A',patient_name:'Alex',additional_documentary_accounts:[{entity:{id:'transport'},relation:'reported_barrier',quote:'Reported a transport concern <script>',polarity:'affirmed',temporality:'historical',subject:'patient',review_status:'Unverified extraction',review_qualification:'Current applicability unknown',sources:[{date:'2027-01-01',record_id:'DOC-7'}]}]}}};
PatientWorkspace.mount(host,input).openPatient('A');
host.onclick({target:{closest:()=>({dataset:{pwTab:'Social & access'},hasAttribute:k=>k==='data-pw-tab'})}});
assert.match(host.innerHTML,/Reported a transport concern &lt;script&gt;/);
assert.match(host.innerHTML,/historical/);assert.match(host.innerHTML,/Current applicability unknown/);assert.match(host.innerHTML,/DOC-7/);
assert.doesNotMatch(host.innerHTML,/<script>|Enroll|Start treatment/);
'''
        result = subprocess.run([shutil.which('node'), '-e', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_report_keeps_native_export_payload_and_well_formed_shared_script(self):
        context = {'id':'ANS-'+'a'*24,'question':'Ten records?', 'source':{'as_of':'2030-05-27'},
                   'answer':{'status':'answered','verification':'unverified','answer_type':'count','count':10,
                             'table':[{'category':'Example','observed':0}]}}
        before = json.dumps(dashboard.payload(context), sort_keys=True)
        html = dashboard.render(context)
        encoded = re.search(r'<script id="data" type="application/json">(.*?)</script>',html,re.S).group(1)
        self.assertEqual(json.dumps(json.loads(encoded),sort_keys=True),before)
        self.assertNotIn('%%PATIENT', html)
        scripts = re.findall(r'<script>(.*?)</script>',html,re.S)
        result = subprocess.run([shutil.which('node'),'--check'],input=scripts[-1],text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('legacy-records',html)


if __name__ == '__main__':
    unittest.main()
