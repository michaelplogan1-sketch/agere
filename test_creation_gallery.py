"""Creation discovery and historical previews must not confer work authority."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from clinical_core.agere.deliverables import gallery


class CreationGallery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.context_id = 'ANS-'+'a'*24
        self.context = {'id':self.context_id,'source':{'as_of':'2030-05-27'},
                        'dependencies':{'derivation':'original-binding'},'invalidated':True,'stale':False}

    def creation(self, number, *, status='delivered', title='Report'):
        key = 'DLV-'+format(number,'012X')
        root = gallery.A.root(self.path)/self.context_id
        folder = root/'deliverables'/key
        folder.mkdir(parents=True)
        (root/'answer.json').write_text('{}')
        doc = '<html><body>Original source: &lt;unchanged&gt;</body></html>'
        spec = {'id':key,'workspace':self.context_id,'title':title,'status':status,
                'created_at':'2026-11-12T16:00:00Z','generation':'saved_report','template':'brief',
                'formats':{'html':{'filename':'artifact.html','sha256':hashlib.sha256(doc.encode()).hexdigest()}}}
        (folder/'spec.json').write_text(json.dumps(spec))
        if status=='delivered':(folder/'artifact.html').write_text(doc)
        bundle={'schema':'agere-evidence-bundle/1','meta':{'workspace':self.context_id},
                'results':{'R-aaaaaaaaaa':self.context},'receipts':{'R-aaaaaaaaaa':{'tool':'saved_answer'}}}
        (folder/'bundle.json').write_text(json.dumps(bundle))
        return key, folder

    def test_every_creation_is_discovered_without_loading_the_analysis_or_hiding_failures(self):
        for n in range(101):self.creation(n)
        self.creation(102,status='rejected')
        with patch.object(gallery.A,'load',side_effect=AssertionError('Catalog must use metadata only')):
            result=gallery.catalog(self.path)
        self.assertEqual((result['saved'],result['failed']),(101,1))
        self.assertEqual(len(result['items']),102)
        self.creation(103,title='A new creation <source>')
        result=gallery.catalog(self.path)
        self.assertEqual(result['saved'],102)
        self.assertTrue(any(x['title']=='A new creation <source>' for x in result['items']))
        self.assertTrue(all('context=ANS-' in x['url'] and '#gallery' in x['url'] for x in result['items']))

    def test_program_screen_does_not_upgrade_a_report_into_an_agent_capability(self):
        self.creation(1,title='Virtual CKD nephrology program')
        item=gallery.catalog(self.path)['items'][0]
        self.assertEqual(item['kind'],'brief')
        self.assertEqual(item['generation'],'saved_report')
        self.assertEqual(item['experience']['availability'],'population_workspace')
        self.assertFalse(item['experience']['agent_execution_available'])
        self.assertIn('?app=ckd&context='+self.context_id,item['experience']['url'])
        self.assertIsNone(gallery.presentation_experience(self.context_id,'CKD laboratory report','Summarize the laboratory results'))

    def test_historical_version_opens_read_only_with_exact_bytes_and_current_warning(self):
        key,folder=self.creation(1)
        before=(folder/'artifact.html').read_bytes()
        with patch.object(gallery.A,'load',return_value=self.context) as load:
            result=gallery.preview(self.path,self.context_id,key)
        self.assertTrue(result['historical'])
        self.assertFalse(result['clinical_execution_authorized'])
        self.assertEqual(result['html'].encode(),before)
        self.assertEqual((folder/'artifact.html').read_bytes(),before)
        load.assert_called_once_with(self.path,self.context_id)

    def test_tampering_wrong_context_and_unsaved_attempts_never_return_a_preview(self):
        key,folder=self.creation(1)
        with patch.object(gallery.A,'load',return_value=self.context):
            (folder/'artifact.html').write_text('changed')
            with self.assertRaisesRegex(ValueError,'recorded version'):gallery.preview(self.path,self.context_id,key)
            self.creation(2,status='rejected')
            result=gallery.preview(self.path,self.context_id,'DLV-'+format(2,'012X'))
            self.assertIsNone(result['html'])
            bundle=json.loads((folder/'bundle.json').read_text());bundle['results']['R-aaaaaaaaaa']['id']='ANS-'+'b'*24
            (folder/'bundle.json').write_text(json.dumps(bundle))
            with self.assertRaisesRegex(ValueError,'binding'):gallery.preview(self.path,self.context_id,key)
        for context,artifact in [('../other',key),(self.context_id,'../../file')]:
            with self.assertRaises(ValueError):gallery.preview(self.path,context,artifact)

    def test_preview_sandbox_blocks_network_and_forms_without_discarding_source_text(self):
        source=Path('clinical_core/agere/assets/gallery.js').read_text()
        code=source[source.index('function sandboxedCreation('):source.index('async function openCreation(')]
        script=code+r'''
const assert=require('node:assert/strict');const original='<html><head><style>p{color:red}</style></head><body>Source words<script>window.example=1</script></body></html>';
const result=sandboxedCreation(original);
assert.ok(result.includes("connect-src 'none'"));assert.ok(result.includes("form-action 'none'"));
assert.ok(result.indexOf('Content-Security-Policy')<result.indexOf('<style>p'));
assert.ok(result.includes('Source words'));assert.ok(result.includes('window.example=1'));
'''
        result=subprocess.run([shutil.which('node'),'-e',script],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn("setAttribute('sandbox','allow-scripts allow-downloads')",source)
        self.assertNotIn('allow-same-origin',source)

    def test_browser_filters_keep_failed_attempts_separate_and_do_not_change_membership(self):
        source=Path('clinical_core/agere/assets/gallery.js').read_text()
        code=source[source.index('function galleryRows('):source.index('function renderGallery(')]
        script=r'''
const assert=require('node:assert/strict');let term='';const $=()=>({value:term});
const creationLabel=x=>x.kind;let creationLibrary={filter:'saved',items:Array.from({length:101},(_,i)=>({id:String(i),status:'saved',kind:i%2?'report':'page',title:'Saved '+i,question:'Original population '+i}))};
creationLibrary.items.push({id:'failed',status:'failed',kind:'page',title:'A failed design'});
const before=JSON.stringify(creationLibrary.items);
'''+code+r'''
assert.equal(galleryRows().length,101);creationLibrary.filter='failed';assert.deepEqual(galleryRows().map(x=>x.id),['failed']);
creationLibrary.filter='page';assert.equal(galleryRows().length,51);term='population 100';assert.equal(galleryRows()[0].id,'100');
assert.equal(JSON.stringify(creationLibrary.items),before);
'''
        result=subprocess.run([shutil.which('node'),'-e',script],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_answer_output_links_identify_a_report_without_claiming_agent_execution(self):
        source=Path('clinical_core/agere/assets/gallery.js').read_text()
        code=source[source.index('function requestedOutputs('):source.index('function setupGallery(')]
        script=r'''
const assert=require('node:assert/strict'),esc=String,fieldLabel=String;
const t={data:{action_context:{id:'ANS-'+'a'.repeat(24)},workflow:[{kind:'deliverable',title:'CKD command center',generation:'saved_report',status:'ready',deliverable_id:'DLV-AAAAAAAAAAAA'}]}};
const before=JSON.stringify(t);
'''+code+r'''
const html=requestedOutputs(t);assert.match(html,/Saved report/);assert.match(html,/data-gallery-open="DLV-AAAAAAAAAAAA"/);
assert.doesNotMatch(html,/Launch agents|Enroll now|Command center ready/);assert.equal(JSON.stringify(t),before);
'''
        result=subprocess.run([shutil.which('node'),'-e',script],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)


if __name__=='__main__':unittest.main()
