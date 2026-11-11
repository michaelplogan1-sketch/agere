"""The authenticated entry point must retain records beyond its old 200-row clip."""
import json
import re
import shutil
import subprocess
import unittest
from clinical_core.agere.hosted import pages


class HostedAnswerPresentation(unittest.TestCase):
    def test_report_distinguishes_aggregate_zero_unknown_and_rows(self):
        from clinical_core.agere.deliverables.patient_dashboard import summary_metrics, brief_overview
        self.assertEqual(summary_metrics({'count':10}, []), [{'value':10,'label':'Count returned'}])
        self.assertEqual(summary_metrics({'count':0}, [])[0]['value'], 0)
        self.assertIsNone(summary_metrics({'count':None}, [])[0]['value'])
        self.assertEqual(summary_metrics({'status':'unsupported'}, []), [])
        rows=[{'patient_id':'P-1'},{'patient_id':'P-1'}]
        self.assertEqual(summary_metrics({}, rows), [
            {'value':1,'label':'Patients in returned records'}, {'value':2,'label':'Complete result rows'}])
        text=brief_overview({'answer':{'count':10},'rows':[]})
        self.assertIn('<strong>10</strong> Count returned', text)
        self.assertIn('no individual result rows',text)
        self.assertNotIn('complete population',text)

    def test_shared_renderer_complete_records_and_source_meanings(self):
        html = pages.app()
        self.assertIn('data-theme="light"', html)
        script = re.findall(r'<script>(.*?)</script>', html, re.S)[-1]
        node = shutil.which('node')
        check = subprocess.run([node, '--check'], input=script, text=True, capture_output=True)
        self.assertEqual(check.returncode, 0, check.stderr)
        # Execute the actual delivered script; only browser boot/navigation is stubbed.
        script = script[:script.rindex('loadMe().then(route)')]
        probe = r'''
const assert=require('node:assert/strict');
global.window={addEventListener(){},fetch:async()=>{throw Error('No network expected')}};
global.document={getElementById(){return null},querySelectorAll(){return []}};
'''+script+r'''
const rows=Array.from({length:237},(_,i)=>({patient_id:'P-'+i,has_record:i===0?null:0,amount:0}));
rows[236].last_field='kept exactly';rows[0].note='<img src=x onerror=alert(1)>';
const d={answer:{table:rows,patient_ids:rows.map(r=>r.patient_id),verification:'unverified',facts:{finding:'Saved finding only'},limitations:['Unassessed source meaning']},status:'partial',cost_usd:null,headline:'Retained result'};
const before=JSON.stringify(d),out=showAnswer(d,'Original question',1);
assert.ok(out.includes('Saved finding only'));
assert.ok(out.includes('Verification incomplete'));
assert.ok(out.includes('API cost unknown'));
assert.ok(out.includes('Explore all 237 records'));
assert.ok(out.includes('last_field'));
assert.ok(out.includes('&lt;img'));
assert.ok(!out.includes('<img src=x'));
assert.equal(JSON.stringify(d),before);
assert.match(ResultView.table(rows,{table:rows},{page:15}),/P-236/);
const csv=answerCSV(rows);assert.match(csv,/last_field/);assert.match(csv,/P-236/);assert.match(csv,/kept exactly/);
assert.match(ResultView.table(rows,{table:rows}),/>Unknown</);
assert.match(ResultView.table(rows,{table:rows}),/Flag not set.*\(0\)/);
const noAnswer=showAnswer({answer:null,status:'unsupported',headline:'The payer attribution is missing'},'Contract question',0);
assert.ok(noAnswer.includes('payer attribution is missing'));
assert.ok(!noAnswer.includes('class="num">0'));
const count=showAnswer({answer:{count:0},status:'answered'},'Count',0);
assert.ok(count.includes('class="num">0'));
const original=answerRows(d.answer,d);assert.equal(original,rows);
const grouped={table:[{keys:['North practice'],value:10}]};
const shown=answerRows(grouped,{});assert.equal(shown[0].group,'North practice');
assert.match(ResultView.table(shown,{table:shown}),/North practice/);
assert.deepEqual(grouped,{table:[{keys:['North practice'],value:10}]});
console.log(JSON.stringify({records:237,late_column_retained:true,unknown_zero_distinct:true,original_answer_unchanged:true}));
'''
        result = subprocess.run([node, '-e', probe], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['records'], 237)
