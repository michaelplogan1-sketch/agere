"""A failed summary check never withholds the analyst's answer: the summary is shown as written, with a label."""
import unittest
from clinical_core.agere.clinical import backstop_agent as B


class UnconfirmedSummary(unittest.TestCase):
    def test_summary_is_shown_with_cells_filled_and_a_plain_label(self):
        calls=[{'name':'query_sql','result':{'result_view':'r1','columns':['patients','stays'],'rows':[[37,52]]}}]
        a={'status':'answered','headline':{'template':'{n} patients had {s} stays; {x} were unknown; target 10%.',
              'bindings':{'n':{'result_view':'r1','row':0,'column':'patients'},'s':{'result_view':'r1','row':0,'column':'stays'},'x':{'result_view':'gone','row':0,'column':'c'}}},
           'facts':{'method':'never as 0 days','count':{'template':'{n} people','bindings':{'n':{'result_view':'r1','row':0,'column':'patients'}}}},'limitations':['kept']}
        out=B.shaped(a,calls)
        self.assertEqual(out['headline'],'37 patients had 52 stays; [value not resolved] were unknown; target 10%.')
        self.assertEqual(out['facts'],{'method':'never as 0 days','count':'37 people'})
        self.assertEqual(out['summary_check'],'not_passed');self.assertTrue(out['uncompiled_prose_fingerprint'])
        self.assertEqual(out['limitations'],['kept',B.UNCONFIRMED_SUMMARY])

    def test_a_compiled_summary_is_untouched(self):
        a={'status':'answered','headline':'37 patients','facts':{'m':'x'}}
        out=B.shaped(dict(a));self.assertEqual(out['headline'],'37 patients');self.assertNotIn('summary_check',out)


if __name__=='__main__':unittest.main()
