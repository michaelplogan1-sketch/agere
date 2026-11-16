"""Presentation suggestions and passed checks cannot become paid repair loops."""
import copy,importlib,json,unittest
from clinical_core.agere.deliverables.receipts import Bundle
C=importlib.import_module('clinical_core.agere.deliverables.compose')

class Disposition(unittest.TestCase):
    def review(self,value):
        def send(body):
            self.prompt=body['messages'][0]['content']
            return {'content':[{'type':'text','text':json.dumps(value)}]},{'cache_hit':True}
        return C._review_artifact('<p>Saved result</p>',Bundle(),'Show results',None,send,C.Ledger(),{'passed':True})

    def advisory(self):
        return {'coverage':'complete','support':'supported','issues':[
            {'severity':'advisory','requirement':'Optional summary','reason':'Consider an additional visual grouping; the complete data is accessible.'}]}

    def test_complete_supported_artifact_keeps_advisory_without_blocking(self):
        out=self.review(self.advisory());self.assertTrue(out['passed'])
        self.assertEqual(out['advisories'],self.advisory()['issues'])
        self.assertEqual(out['violations'],[])
        self.assertEqual(out['semantic_review_version'],C.PRESENTATION_REVIEW_VERSION)
        self.assertIn('successful checks',self.prompt)
        self.assertIn('never issues',self.prompt)

    def test_contradiction_missing_coverage_or_unknown_never_passes_as_advisory(self):
        for field,values in [('support',('contradicted','not_established','unknown')),('coverage',('incomplete','unknown'))]:
            for value in values:
                with self.subTest(field=field,value=value):
                    review=self.advisory();review[field]=value
                    self.assertFalse(self.review(review)['passed'])

    def test_actual_blocking_and_untyped_legacy_issues_still_fail_closed(self):
        for severity in ('blocking','unknown',None):
            review=self.advisory()
            if severity is None:review['issues'][0].pop('severity')
            else:review['issues'][0]['severity']=severity
            self.assertFalse(self.review(review)['passed'])

    def test_malformed_advisory_cannot_approve_a_page(self):
        for item in ('text',{'severity':'advisory'},{'severity':'advisory','requirement':'','reason':'missing'}):
            review=self.advisory();review['issues']=[item]
            self.assertFalse(self.review(review)['passed'])

    def test_positive_observations_are_retained_without_creating_a_repair(self):
        review={'coverage':'complete','support':'supported','issues':[],
                'observations':['Independent acceptance and machine status are faithfully separated.']}
        out=self.review(review);self.assertTrue(out['passed']);self.assertEqual(out['semantic_review'],review)

if __name__=='__main__':unittest.main()
