"""Missingness, native identifiers and requested periods retain their types."""
import copy
import unittest
from clinical_core.agere.clinical import backstop_agent as B


class ProseReferenceDomains(unittest.TestCase):
    question='Compare allowed, adjudicated paid and separately received cash by payer for services dated in the first half of 2029.'

    def setUp(self):
        self.sql='SELECT payer_id AS payer, received_cash_cents FROM received_cash'
        self.calls=[{'name':'query_sql','input':{'sql':self.sql},'result':{
            'result_view':'result','native_sql_contract':B.NATIVE_SQL_CONTRACT,
            'executed_query':self.sql,'columns':['payer','received_cash_cents'],
            'rows':[['ENTITY-PAYER-0',100],['ENTITY-PAYER-1',None],['ENTITY-PAYER-2',-100]],
            'truncated':False}}]

    def compile(self,text,calls=None,question=None):
        a={'table_binding':{'result_view':'result'},'headline':text}
        B.bind_computed_prose(a,self.calls if calls is None else calls,
                             question=self.question if question is None else question)
        return a

    def test_null_zero_distinction_is_not_an_observed_zero(self):
        text='Their received cash is NULL (unknown), not zero.'
        a=self.compile(text);self.assertEqual(a['headline'],text)
        self.assertEqual(a['computed_prose'][0]['parameters'][0]['kind'],'null_zero_distinction')
        for text in ('Their received cash is zero.','Their received cash is not zero.',
                     'NULL (unknown), not zero; 100 payments were received.'):
            with self.subTest(text=text),self.assertRaises(B.ComputedProseError):self.compile(text)

    def test_abbreviated_native_identity_group_preserves_exact_source_namespace(self):
        text='Received cash is shown for ENTITY-PAYER-0/1/2.'
        a=self.compile(text);self.assertEqual(a['headline'],text)
        proof=a['computed_prose'][0]['parameters'][0]
        self.assertEqual(proof['kind'],'executed_identifier_group')
        self.assertEqual(proof['identifiers'],['ENTITY-PAYER-0','ENTITY-PAYER-1','ENTITY-PAYER-2'])
        self.assertEqual(proof['column'],'payer');self.assertEqual(proof['result_view'],'result')

    def test_identity_group_cannot_hide_an_unreceived_id_foreign_domain_or_measurement(self):
        for text in ('Received cash for ENTITY-PAYER-0/1/3.',
                     'Received cash for FOREIGN-PAYER-0/1/2.',
                     'Dose ENTITY-PAYER-0/1/2 mg.',
                     'There were 0/1/2 patients.',
                     'ENTITY-PAYER-0/1/2 received 100 dollars.'):
            with self.subTest(text=text),self.assertRaises(B.ComputedProseError):self.compile(text)
        for change in ('incomplete','no_contract','different_field','not_dependency'):
            calls=copy.deepcopy(self.calls);r=calls[0]['result']
            if change=='incomplete':r['truncated']=True
            elif change=='no_contract':r.pop('native_sql_contract')
            elif change=='different_field':r['columns']=['description','received_cash_cents']
            else:r['result_view']='unrelated'
            with self.subTest(change=change),self.assertRaises(B.ComputedProseError):
                self.compile('Received cash for ENTITY-PAYER-0/1/2.',calls=calls)

    def test_requested_half_year_is_a_calendar_reference_with_exact_window(self):
        text='Claims with services outside the first half of 2029 remain separate.'
        a=self.compile(text);self.assertEqual(a['headline'],text)
        p=a['computed_prose'][0]['parameters'][0]
        self.assertEqual(p['kind'],'requested_calendar_period')
        self.assertEqual((p['start'],p['end']),('2029-01-01','2029-06-30'))
        self.assertIn('question_sha256',p)
        for text in ('Services outside the second half of 2029.',
                     'Services outside the first half of 2028.',
                     'Services outside the first half of 2029 patients.'):
            with self.subTest(text=text),self.assertRaises(B.ComputedProseError):self.compile(text)

    def test_requested_quarter_preserves_its_own_calendar_window(self):
        a=self.compile('Services in the third quarter of 2029 remain separate.',
                       question='Compare services in the third quarter of 2029.')
        p=a['computed_prose'][0]['parameters'][0]
        self.assertEqual((p['period'],p['index'],p['start'],p['end']),
                         ('quarter',3,'2029-07-01','2029-09-30'))
        with self.assertRaises(B.ComputedProseError):
            self.compile('Services in the fourth quarter of 2029.',
                         question='Compare services in the third quarter of 2029.')

    def test_combined_scope_keeps_ordinals_separate_from_source_id_components(self):
        text=('Cash not attributed: (1) missing links; (2) allocations on ENTITY-PAYER-0/1/2 '
              'with services outside the first half of 2029.')
        self.assertEqual(self.compile(text)['headline'],text)
        with self.assertRaises(B.ComputedProseError):
            self.compile(text+' There were 2 missing links.')

    def test_entity_prose_does_not_inherit_global_reference_authority(self):
        calls=copy.deepcopy(self.calls);r=calls[0]['result'];r['columns'].insert(0,'patient_id')
        for i,row in enumerate(r['rows']):row.insert(0,'A'+str(i))
        for text in ('Cash is NULL (unknown), not zero.','Received cash for ENTITY-PAYER-0/1/2.',
                     'Services in the first half of 2029.'):
            a={'table_from_tool':'result','row_prose':{'keys':['patient_id'],'rows':[{
                'key':{'patient_id':'A'+str(i)},'basis':[{'result_view':'result','row':i}],
                'fields':{'explanation':text}}for i in range(3)]}}
            with self.subTest(text=text),self.assertRaises(B.ComputedProseError):
                B.materialize_table(a,calls);B.bind_computed_prose(a,calls,question=self.question)


if __name__=='__main__':unittest.main()
