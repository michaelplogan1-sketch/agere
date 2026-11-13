"""A method note that fails quantity binding twice is kept as a labelled limitation instead of ending the run."""
import unittest
from types import SimpleNamespace
from clinical_core.agere.clinical import backstop_agent as B

MSG='Repair computed prose: ["Quantitative facts.discharge_absent needs a template with bindings to actual executed scalar cells; unbound literals: [\\"0\\"]", "Numeric prose must use scalar placeholders at facts.los_clock; unbound literals: [\\"3\\"]", "Quantitative headline needs a template with bindings to actual executed scalar cells; unbound literals: [\\"one\\"]"]. For each named literal'


class UnboundFactNotes(unittest.TestCase):
    def answer(self):
        return {'headline':'h','facts':{'discharge_absent':'never as 0 days','los_clock':{'template':'within 3 days','bindings':{}},'kept':'x'},
                'limitations':['a'],'global_prose_basis':[{'path':['facts','discharge_absent'],'basis':[]},{'path':['headline'],'basis':[]}]}

    def test_failing_method_notes_are_relocated_and_other_fields_stay(self):
        a=self.answer()
        moved=B.relocate_unbound_fact_notes(a,ValueError(MSG),SimpleNamespace())
        self.assertEqual([m['field']for m in moved],['facts.discharge_absent','facts.los_clock'])
        self.assertEqual(set(a['facts']),{'kept'});self.assertEqual(a['headline'],'h')
        self.assertIn('Method note (discharge absent), numbers not tied to computed values: never as 0 days',a['limitations'])
        self.assertTrue(any('within 3 days'in l for l in a['limitations']))
        self.assertEqual(a['global_prose_basis'],[{'path':['headline'],'basis':[]}])

    def test_a_headline_only_failure_moves_nothing(self):
        a=self.answer()
        self.assertEqual(B.relocate_unbound_fact_notes(a,ValueError('Quantitative headline needs a template; unbound literals: ["2"]'),SimpleNamespace()),[])
        self.assertEqual(len(a['facts']),3)


if __name__=='__main__':unittest.main()
