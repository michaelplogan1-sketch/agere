"""Connected navigation preserves the exact saved context and existing controls."""
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from clinical_core.agere import application_shell, ask_view
from clinical_core.agere.clinical import learning_workbench
from clinical_core.agere.deliverables import action_center


class ConnectedSite(unittest.TestCase):
    def test_ckd_screen_keeps_the_population_bound_and_never_fabricates_agent_runs(self):
        source=Path('clinical_core/agere/assets/command_center.js').read_text()
        script=r'''
const assert=require('node:assert/strict'),esc=s=>String(s??''),recordName=r=>r.patient_id;
const elements={},$=id=>elements[id]||(elements[id]={innerHTML:'',focus(){}}),document={body:{dataset:{}},querySelectorAll:()=>[]};
const columnSpec=()=>[];
'''+source+r'''
const c={id:'ANS-'+'a'.repeat(24),invalidated:true,source:{as_of:'2030-05-27'},original_question:'The original CKD request.',answer:{count:308,table:[{patient_id:'P-1',egfr:null},{patient_id:'P-2',egfr:0}]},patient_profiles:{patients:{'P-1':{display_name:'Synthetic Example'}}}};
const before=JSON.stringify(c);ckdWorkspace.context=c;ckdWorkspace.response={programs:{pharmd:{label:'Other workflow'}},items:[]};
ckdWorkspace.view='agents';renderCKDView();const html=$('#ckd-view').innerHTML;
assert.match(html,/disabled aria-describedby="ckd-agent-reason"/);assert.match(html,/Mock agents are not connected/);assert.doesNotMatch(html,/Patient reached|Enrollment complete|Agent running/);
ckdWorkspace.view='patients';renderCKDView();assert.match($('#ckd-patient-browser').innerHTML,/Synthetic Example/);
assert.equal(ckdFilteredRows()[0],c.answer.table[0]);ckdWorkspace.search='P-2';assert.deepEqual(ckdFilteredRows(),[c.answer.table[1]]);ckdWorkspace.search='no match';assert.equal(ckdFilteredRows().length,0);ckdWorkspace.search='';
assert.match(ckdPatientPreview(c),/Synthetic Example/);assert.match(ckdPatientPreview(c),/Unknown/);assert.match(ckdPatientPreview(c),/>0</);
assert.equal(JSON.stringify(c),before);assert.equal(ckdIsContext('../other'),false);
assert.ok(ckdURL(c.id).includes('?app=ckd&context='+c.id));
const wrong={patient_profiles:{patients:{'P-1':{patient_id:'P-2',display_name:'Wrong person'}}}};assert.equal(ckdIdentity(wrong,{patient_id:'P-1'}),'P-1');
'''
        r=subprocess.run([shutil.which('node'),'-e',script],capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertNotIn("method:'POST'",source)
        self.assertNotIn('/api/stack/action-simulate',source)
        self.assertIn('id="page-program"',ask_view.page())
        navigation=Path('clinical_core/agere/assets/explore.js').read_text()
        self.assertIn("closest('button[data-page],a[data-page]",navigation)

    def test_primary_navigation_is_available_across_product_pages(self):
        pages=[ask_view.page(),action_center.page(),learning_workbench.page()]
        for page in pages:
            with self.subTest(page=page[:80]):
                for label in ['Wollstonecraft','Saved answers','Patients','Sources','Gallery','Source review']:
                    self.assertIn(label,page)
                self.assertNotRegex(page,r'<script[^>]+src=|@import')
                self.assertIn('aria-label="Main navigation"',page)
                for script in re.findall(r'<script>(.*?)</script>',page,re.S):
                    result=subprocess.run([shutil.which('node'),'--check'],input=script,text=True,capture_output=True)
                    self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('id="typed-review"',pages[2])
        for hook in ['ensureEvidence(pid)','data-select-detail','data-cohort-layout','cohort-selection-table']:
            self.assertIn(hook,pages[1])

    def test_shell_and_ask_links_keep_exact_context_without_adopting_patient_selection(self):
        context='ANS-'+'e'*24
        nav=application_shell.navigation('patients',context)
        self.assertIn('/ask?context='+context+'#patients',nav)
        self.assertIn('/ask?context='+context+'#gallery',nav)
        self.assertNotIn('patient=',nav)
        self.assertNotIn('context=',application_shell.navigation('work','malformed-context'))
        source=Path('clinical_core/agere/assets/explore.js').read_text()
        code=source[source.index('function syncAppLinks('):source.index('function renderPatientLibrary(')]
        setup=r'''
const assert=require('node:assert/strict');
const links=['work','deliverables','review'].map(appLink=>({dataset:{appLink}}));
let activeContext={id:'ANS-'+ 'e'.repeat(24)};
const document={querySelectorAll:()=>links};
'''
        checks=r'''
syncAppLinks();assert.equal(links[0].href,'/action-center?context='+activeContext.id);
assert.equal(links[1].href,'/ask?context='+activeContext.id+'#gallery');
assert.ok(links.every(a=>!a.href.includes('patient=')));
activeContext=null;syncAppLinks();assert.equal(links[0].href,'/action-center');
activeContext={id:'bad&context=other'};syncAppLinks();assert.equal(links[1].href,'/ask#gallery');
'''
        result=subprocess.run([shutil.which('node'),'-e',setup+code+checks],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_patient_dossier_waits_for_original_evidence_hydration(self):
        source=Path('clinical_core/agere/deliverables/action_center.js').read_text()
        detail=source[source.index('async function patientDetails('):source.index('function editWork(')]
        self.assertLess(detail.index('await ensureEvidence(pid)'),detail.index('PatientWorkspace.mount'))
        self.assertLess(detail.index('request!==detailRequest'),detail.index('PatientWorkspace.mount'))
        self.assertIn('data-select-detail',detail)
        self.assertIn('data.items.filter(i=>i.patient_id===pid)',detail)

    def test_global_workspace_links_activate_the_requested_view(self):
        source=Path('clinical_core/agere/deliverables/action_center.js').read_text()
        code=source[source.index("window.addEventListener('hashchange'"):]
        setup=r'''
const assert=require('node:assert/strict');let listener,data={},currentView='cohort';
const viewNames={overview:'Overview',cohort:'Patient selection',work:'Work board',studio:'Deliverables'};
const window={addEventListener:(kind,fn)=>{assert.equal(kind,'hashchange');listener=fn}};
const location={hash:'#studio'},calls=[];
function go(view,focus){calls.push([view,focus]);currentView=view}
'''
        checks=r'''
listener();assert.deepEqual(calls,[['studio',false]]);
location.hash='#work';listener();assert.equal(currentView,'work');
location.hash='';listener();assert.equal(currentView,'overview');
const count=calls.length;location.hash='#unknown';listener();assert.equal(calls.length,count);
data=null;location.hash='#studio';listener();assert.equal(calls.length,count);
'''
        result=subprocess.run([shutil.which('node'),'-e',setup+code+checks],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)


if __name__=='__main__':
    unittest.main()
