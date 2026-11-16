"""Received signed event roles, not a clinical/actuarial completion benchmark."""
import sqlite3,unittest
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere.clinical import adjudication_history as H,backstop_agent as B,system_one_gate as G

QUESTION=('Compare service-month payer-paid development for the 2028 and 2029 claim cohorts using received adjudication dates through the snapshot. '
          'Show observed runout, incomplete months and the assumptions of any completion estimate. Keep observed payments separate from estimated ultimate amounts.')
SCHEMA={'payer_adjudication':H.DDL.split('CREATE INDEX')[0].split('(',1)[1].split(');')[0],
        'payer_claim':'payer_claim_id TEXT,patient_id TEXT,paid_cents INTEGER'}
SCHEMA={table:[part.strip().split()[0]for part in columns.split(',')]for table,columns in SCHEMA.items()}
SCHEMA['eligibility']=['patient_id','superseded']

def call(sql,view='agere_result_1',reads=('payer_adjudication',),columns=('observed',)):
    return {'name':'query_sql','input':{'sql':sql},'result':{'result_view':view,'columns':list(columns),
        'rows':[{c:130 for c in columns}],'truncated':False,'read_views':[],
        'source_contracts':{'relations':[{'name':t,'columns':SCHEMA[t]}for t in reads],'definitions':[]}}}

def answer(view='agere_result_1'):
    return {'status':'answered','answer_type':'table','table_from_tool':view,'headline':'Received paid development.'}


class SignedDevelopment(unittest.TestCase):
    def check(self,*calls,question=QUESTION):
        return H.development_source_issues(question,answer(calls[-1]['result']['result_view']),list(calls))

    def test_current_state_cannot_replace_received_payment_history(self):
        for predicate in ('superseded=0','is_last=1','superseded_by IS NULL'):
            c=call('SELECT SUM(paid_cents) AS observed FROM payer_adjudication WHERE '+predicate)
            with self.subTest(predicate=predicate):self.assertTrue(self.check(c))
        self.assertTrue(self.check(call('SELECT SUM(paid_cents) AS observed FROM payer_claim',reads=('payer_claim',))))

    def test_signed_history_and_cumulative_sum_keep_reversals_and_ties(self):
        sql=('SELECT SUM(paid_cents) AS observed FROM payer_adjudication '
             "WHERE service_from>='2028-01-01' AND service_from<'2030-01-01' AND available_date<='2030-05-27'")
        self.assertEqual(self.check(call(sql)),[])
        db=sqlite3.connect(':memory:');self.addCleanup(db.close);db.executescript(H.DDL)
        for rid,seq,amount,old,day,arrival in [('a',1,100,1,'2029-01-10','2029-01-11'),
            ('b',2,-20,1,'2029-02-10','2029-02-11'),('c',2,50,0,'2029-02-10','2029-02-11'),
            ('future',3,900,0,'2030-06-01','2030-06-02')]:
            db.execute('INSERT INTO payer_adjudication(record_id,payer_claim_id,patient_id,sequence,paid_cents,superseded,is_last,adjudication_date,available_date,service_from)VALUES(?,?,?,?,?,?,?,?,?,?)',
                (rid,'claim','patient',seq,amount,old,not old,day,arrival,'2029-01-01'))
        self.assertEqual(db.execute(sql).fetchone()[0],130)
        self.assertEqual(db.execute(sql+' AND superseded=0').fetchone()[0],50)
        cumulative=("WITH cells AS(SELECT substr(adjudication_date,1,7) AS month,SUM(paid_cents) AS paid FROM payer_adjudication "
            "WHERE available_date<='2030-05-27' GROUP BY month)SELECT SUM(paid)OVER(ORDER BY month) AS observed FROM cells")
        self.assertEqual([r[0]for r in db.execute(cumulative)],[100,130])
        self.assertEqual(self.check(call(cumulative)),[])

    def test_lag_of_signed_events_is_not_replacement_state(self):
        sql=('WITH r AS(SELECT payer_claim_id,paid_cents,sequence,LAG(paid_cents)OVER(PARTITION BY payer_claim_id ORDER BY sequence)AS previous '
             'FROM payer_adjudication)SELECT SUM(paid_cents-COALESCE(previous,0)) AS observed FROM r')
        self.assertTrue(any('LAG' in s for s in self.check(call(sql))))
        direct=('SELECT SUM(delta) AS observed FROM(SELECT paid_cents-COALESCE(LAG(paid_cents)OVER('
                'PARTITION BY payer_claim_id ORDER BY sequence),0)AS delta FROM payer_adjudication)')
        self.assertTrue(any('LAG' in s for s in self.check(call(direct))))

    def test_cumulative_state_lag_is_a_different_quantity(self):
        sql=('WITH r AS(SELECT payer_claim_id,adjudication_date,SUM(paid_cents)OVER(PARTITION BY payer_claim_id ORDER BY adjudication_date)AS cumulative_paid '
             'FROM payer_adjudication),d AS(SELECT cumulative_paid-COALESCE(LAG(cumulative_paid)OVER(PARTITION BY payer_claim_id ORDER BY adjudication_date),0)AS delta FROM r)'
             'SELECT SUM(delta)AS observed FROM d')
        self.assertEqual(self.check(call(sql)),[])
        dates=('WITH r AS(SELECT paid_cents,julianday(adjudication_date)-julianday(LAG(adjudication_date)OVER(ORDER BY sequence))AS days '
               'FROM payer_adjudication)SELECT SUM(paid_cents)+COALESCE(MAX(days),0)AS observed FROM r')
        self.assertEqual(self.check(call(dates)),[])  # source role only, not dimensional/method acceptance

    def test_native_alias_chain_and_separate_current_alternative(self):
        a=call('SELECT paid_cents AS amount,superseded AS retired FROM payer_adjudication',columns=('amount','retired'))
        b=call('SELECT SUM(amount) AS observed FROM agere_result_1 WHERE retired=0','agere_result_2',reads=())
        self.assertTrue(self.check(a,b))
        both=call('SELECT SUM(paid_cents)AS observed,SUM(CASE WHEN superseded=0 THEN paid_cents ELSE 0 END)AS current_paid FROM payer_adjudication',columns=('observed','current_paid'))
        self.assertEqual(self.check(both),[])

    def test_unused_diagnostic_counts_and_explicit_alternative_do_not_trigger(self):
        diagnostic=call('SELECT SUM(paid_cents) AS observed FROM payer_adjudication WHERE superseded=0')
        good=call('SELECT SUM(paid_cents)AS observed FROM payer_adjudication','agere_result_2')
        self.assertEqual(self.check(diagnostic,good),[])
        self.assertEqual(self.check(call('SELECT COUNT(paid_cents)AS observed FROM payer_adjudication WHERE superseded=0')),[])
        self.assertEqual(self.check(diagnostic,question='Show paid development for current versions only.'),[])
        self.assertEqual(self.check(diagnostic,question='What is the current payer-paid balance?'),[])

    def test_other_entity_current_filter_does_not_redefine_payment_event_history(self):
        sql=('SELECT SUM(p.paid_cents)AS observed FROM payer_adjudication p '
             'JOIN eligibility e USING(patient_id)WHERE e.superseded=0')
        self.assertEqual(self.check(call(sql,reads=('payer_adjudication','eligibility'))),[])

    def test_unrelated_empty_schema_metadata_does_not_hide_financial_ancestry(self):
        c=call('SELECT SUM(paid_cents)AS observed FROM payer_adjudication WHERE superseded=0')
        c['result']['source_contracts']['relations'].append({'name':'unrelated_documentation','columns':[]})
        self.assertTrue(any('current/last' in s for s in self.check(c)))

    def test_materialized_value_copy_keeps_its_actual_producer_ancestry(self):
        a=call('SELECT SUM(paid_cents)AS observed FROM payer_adjudication WHERE superseded=0')
        b=call('SELECT observed FROM agere_result_1','agere_result_2',reads=())
        b['result']['source_contracts']['definitions']=[{'name':'agere_result_1','query':'SELECT 130 AS observed'}]
        self.assertTrue(any('current/last' in s for s in self.check(a,b)))

    def test_unbound_known_amounts_do_not_inherit_history_from_an_unrelated_read(self):
        c=call('SELECT 130 AS observed_paid_cents',columns=('observed_paid_cents',))
        self.assertTrue(any('no resolved signed-event' in s for s in self.check(c)))
        c['result']['rows']=[{'observed_paid_cents':None}]
        self.assertEqual(self.check(c),[])  # a declared unknown is not a fabricated amount

    def test_static_source_failure_reaches_existing_localized_repair_before_provider(self):
        c=call('SELECT SUM(paid_cents)AS observed FROM payer_adjudication WHERE superseded=0')
        with patch.object(G,'check',side_effect=AssertionError('Native source-role failure needs no model vote')):
            result=B.review_final(SimpleNamespace(),QUESTION,answer(),[c],SimpleNamespace(read={}),ledger=None,as_of='2030-05-27')
        self.assertEqual(result['status'],'repair');self.assertIn('superseded',result['feedback'])


if __name__=='__main__':unittest.main()
