"""Model-free checks for the Bridge Call prototype: who the call goes to, what is asked, and what the answers turn into."""
import unittest

from clinical_core.agere.tiered import bridge_call, readmission


def sheet(markers=None, **facts):
    base = {'pharmacy fills since discharge (to day 2)': '1', 'next appointment': '2029-08-20', 'interpreter needed': 'no', 'inpatient admissions in the prior 12 months': '0'}
    return {'patient': 'P1', 'admit': '2029-08-09', 'discharge': '2029-08-12', 'call_date': '2029-08-14', 'facts': {**base, **facts}, 'reason': 'heart failure',
            'instruction': 'Home heart-failure medicines to be reviewed at follow up.', 'went': 'home',
            'markers': {d: (markers or {}).get(d, []) for d in readmission.DRIVERS if d != 'emergency department threshold'}}


class BridgeCall(unittest.TestCase):
    def test_a_plain_discharge_gets_three_questions_and_no_task_when_all_is_well(self):
        s = sheet(); r = bridge_call.route(s)
        self.assertEqual((r['to'], [k for k, _ in r['questions']], r['priority']), ('the patient', ['worse', 'medicines', 'appointment'], 'standard'))
        self.assertEqual(bridge_call.triage({'worse': 'no', 'medicines': 'yes', 'appointment': 'yes'}, s), [])

    def test_a_facility_discharge_is_a_call_to_the_facility_nurse_about_transfer_back(self):
        s = sheet({'sent back from a facility': ['discharged to a skilled nursing facility']}); s['went'] = 'skilled nursing facility'
        r = bridge_call.route(s)
        self.assertIn('charge nurse', r['to']); self.assertEqual(r['priority'], 'first')
        self.assertEqual([k for k, _ in r['questions']], ['worse', 'seen', 'medicines', 'plan'])
        tasks = bridge_call.triage({'worse': 'no', 'seen': 'no', 'medicines': 'yes', 'plan': 'no'}, s)
        self.assertEqual([t['to'] for t in tasks], ["the facility's medical director", "the facility's clinician"])

    def test_advanced_illness_self_care_and_social_need_each_add_their_question(self):
        s = sheet({'advanced illness': ['heart failure'], 'self-care at home': ['dementia'], 'social need': ['low food access']})
        r = bridge_call.route(s)
        self.assertEqual([k for k, _ in r['questions']], ['worse', 'medicines', 'appointment', 'plan', 'help', 'need'])
        self.assertEqual(r['to'], 'the patient, or the person helping at home')
        tasks = bridge_call.triage({'worse': 'yes', 'medicines': 'no', 'appointment': 'yes', 'plan': 'no', 'help': 'no', 'need': 'yes'}, s)
        self.assertEqual([t['to'] for t in tasks], ['transitions nurse', 'pharmacist', "the patient's own clinician", 'home health or care management', 'social work'])
        self.assertIn('2 hours', tasks[0]['when'])

    def test_the_record_can_contradict_a_yes_and_a_missed_call_is_never_closed_silently(self):
        s = sheet(**{'pharmacy fills since discharge (to day 2)': '0', 'next appointment': 'none on record'})
        self.assertIn('no pharmacy fill on record since discharge', bridge_call.route(s)['flags'])
        tasks = bridge_call.triage({'worse': 'no', 'medicines': 'yes', 'appointment': 'yes'}, s)
        self.assertEqual([t['to'] for t in tasks], ['pharmacist', 'scheduling'])
        missed = bridge_call.triage({}, s, reached=False)
        self.assertEqual([t['to'] for t in missed], ['transitions nurse'])
        self.assertIn('not reached', bridge_call.card(s, {}, missed, reached=False))


if __name__ == '__main__':
    unittest.main()
