"""Bare numeric follow-ups inherit a source-verified measure, never an arbitrary lab."""
from unittest.mock import Mock,patch
from test_population_forms import Base
from clinical_core.agere.clinical import conversation as C,shared_planner as S,insight_families as I

class MeasureInheritance(Base):
    def setUp(self):
        Base.setUp(self)
        self.stack.compose=lambda q,**kw:S.compose(self.stack,q,model=Mock(side_effect=AssertionError('No model')),**kw)
    def root(self,conv,question='Which practices have the biggest diabetes control gap?'):
        return C.handle_compose(self.stack,{'question':question,'conversation_id':conv})
    def test_arbitrary_thresholds_keep_the_verified_population_and_latest_measure(self):
        for threshold,practice,n in [(7,'PR1',2),(9,'PR1',1),(10,'PR3',1)]:
            conv='threshold'+str(threshold);self.root(conv)
            original=f'and which practice has the most patients above {threshold}?'
            out=C.handle_compose(self.stack,{'question':original,'conversation_id':conv})
            row=out['answer']['table'][0]
            self.assertEqual(row['practice'],practice)
            self.assertEqual(row['patients'],n)
            self.assertEqual(out['conversation']['question'],original)
            self.assertEqual(out['conversation']['method'],'inherited_measure')
            self.assertIn('latest A1c',out['conversation']['resolved_question'])
    def test_explicit_new_measure_is_not_replaced_and_bp_is_ambiguous(self):
        root=self.root('explicit')
        self.assertIsNone(C.measure_reference(self.stack,'and which practice has the most patients with eGFR below 30?',root['action_context']))
        root=self.root('bp','Which practices have the biggest blood pressure control gap?')
        self.assertIsNone(C.measure_reference(self.stack,'and which practice has the most patients above 150?',root['action_context']))
    def test_descriptor_must_match_the_executed_measure_population(self):
        root=self.root('mismatch');real=I.measures
        def changed(ctx):
            measures=real(ctx);measures['diabetes_control']={**measures['diabetes_control'],'noun':'adult diabetics with no A1c result'};return measures
        with patch.object(I,'measures',side_effect=changed):
            self.assertIsNone(C.measure_reference(self.stack,'and which practice has the most patients above 9?',root['action_context']))
