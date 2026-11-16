"""One validated download read; existing workspace/work guards stay current."""
import importlib,json,unittest
from pathlib import Path
from unittest.mock import patch
import test_shared_planner as F
import test_saved_report as SR
from clinical_core.agere.deliverables import answer_context as A,saved_report as R,workflow as W
C=importlib.import_module('clinical_core.agere.deliverables.compose')


class ReadBoundary(unittest.TestCase):
    setUp=F.SharedPlanner.setUp
    prepare=SR.SavedReport.prepare

    def capture(self):
        question,result=self.prepare(partial=True)
        return A.capture(self.path,question,result)

    def report(self):
        context=self.capture()
        report=R.create(self.path,{'context_id':context['id'],'template':'brief','request':'PDF'})
        return context,report

    def test_workspace_preserves_its_context_and_workflow_output(self):
        context=self.capture();expected=A.load(self.path,context['id'])
        actual=A.inspect(self.path,context['id'])
        self.assertEqual(actual['context'],expected)
        self.assertEqual(actual['workflow'],{'steps':[],'synthetic':True})

    def test_absent_independent_review_never_builds_a_large_saved_answer_bundle(self):
        context=self.capture();loaded=A.load(self.path,context['id'])
        with patch.object(C.Bundle,'add',side_effect=AssertionError('No review file; no bundle needed')):
            self.assertIsNone(C.context_review(self.path,loaded))

    def test_present_review_keeps_existing_exact_snapshot_binding(self):
        context=self.capture();loaded=A.load(self.path,context['id'])
        (A.root(self.path)/context['id']/'answer-review.json').write_text('{}')
        with patch.object(C.Bundle,'add',autospec=True,return_value='authored')as add:
            self.assertIsNone(C.context_review(self.path,loaded))
        self.assertEqual(add.call_count,1)

    def test_recorded_pdf_validates_current_context_once_and_skips_unused_HTML(self):
        context,report=self.report();folder=A.root(self.path)/context['id']/'deliverables'/report['id'];expected=(folder/'artifact.pdf').read_bytes();read=Path.read_text
        def checked(file,*args,**kwargs):
            if file in(folder/'body.html',folder/'artifact.html'):raise AssertionError('PDF does not consume HTML')
            return read(file,*args,**kwargs)
        with patch.object(A,'load',wraps=A.load)as load,patch.object(Path,'read_text',checked):raw,_,_=R.read_format(self.path,context['id'],report['id'],'pdf')
        self.assertEqual(raw,expected);self.assertEqual(load.call_count,1)
        self.assertTrue(load.call_args.kwargs['current'])

    def test_download_still_rejects_changed_work_and_changed_derivation(self):
        context,report=self.report()
        with patch.object(A,'work',return_value=[{'id':'changed-work'}]),self.assertRaisesRegex(ValueError,'Review work changed'):
            R.read_format(self.path,context['id'],report['id'],'pdf')
        file=A.root(self.path)/context['id']/'answer.json';data=json.loads(file.read_text());data['answer']['headline']='Changed after sealing';file.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError,'invalidated'):R.read_format(self.path,context['id'],report['id'],'pdf')

    def test_report_bundle_cannot_select_a_different_context_or_derivation(self):
        context,report=self.report();file=A.root(self.path)/context['id']/'deliverables'/report['id']/'bundle.json';original=json.loads(file.read_text());rid=next(k for k,r in original['receipts'].items()if r['tool']=='saved_answer')
        for where in('workspace','receipt','result'):
            value=json.loads(json.dumps(original))
            if where=='workspace':value['meta']['workspace']='ANS-'+'f'*24
            elif where=='receipt':value['receipts'][rid]['args']['context_id']='ANS-'+'f'*24
            else:value['results'][rid]['id']='ANS-'+'f'*24
            file.write_text(json.dumps(value))
            with self.subTest(where=where),self.assertRaisesRegex(ValueError,'different saved context'):R.read_format(self.path,context['id'],report['id'],'pdf')
        original['results'][rid]['dependencies']['derivation']='altered';file.write_text(json.dumps(original))
        with self.assertRaisesRegex(ValueError,'sealed derivation'):R.read_format(self.path,context['id'],report['id'],'pdf')


if __name__=='__main__':unittest.main()
