import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from clinical_core.agere.clinical.context_student import LABELS,feature,fit,normalize_distribution,ContextStudent,verify_batch
from clinical_core.agere.clinical.shadow import rule_label,example_from_fact,run_shadow
from clinical_core.agere.gateway import BudgetLedger

class StudentBoundaryTest(unittest.TestCase):
    def test_features_never_include_supervision_or_split(self):
        row={'concept':'edema','target_quote':'edema','text':'Patient denies edema.',
             'state':'negated','verifier_label':'negated','family_id':'TEST_FAMILY','split':'test','machine_agreement':True}
        x=feature(row);self.assertNotIn('negated',x);self.assertNotIn('TEST_FAMILY',x);self.assertNotIn('test',x)
        changed={**row,'state':'affirmed','split':'train','family_id':'OTHER'};self.assertEqual(feature(changed),x)

    def test_rounded_probability_distribution_preserves_ranking(self):
        ps=dict.fromkeys(LABELS,0.);ps.update(affirmed=.8,negated=.19)
        x=normalize_distribution({'choice':'affirmed','probabilities':ps})
        self.assertAlmostEqual(sum(x.values()),1.);self.assertEqual(max(x,key=x.get),'affirmed');self.assertEqual(sum(ps.values()),.99)

    def test_invalid_probability_distribution_fails_closed(self):
        for value in (float('nan'),float('inf'),True,-.1,1.1,.8):
            ps=dict.fromkeys(LABELS,0.);ps['affirmed']=value
            with self.assertRaises(ValueError):normalize_distribution({'choice':'affirmed','probabilities':ps})
        ps=dict.fromkeys(LABELS,0.);ps['negated']=1.
        with self.assertRaises(ValueError):normalize_distribution({'choice':'affirmed','probabilities':ps})

    def test_verifier_payload_is_blind_to_assignment(self):
        row={'example_id':'12345678901234567890','concept':'edema','target_quote':'edema','text':'Patient denies edema.',
             'state':'negated','family_id':'SECRET_FAMILY','split':'test'}
        with patch('clinical_core.agere.clinical.context_student.send',return_value=([],{})) as send:
            verify_batch([row],None,Path('/tmp'))
        body=send.call_args.args[0]
        self.assertEqual(body['state']['1234567890123456'],{k:row[k] for k in ('concept','target_quote','text')})
        self.assertNotIn('SECRET_FAMILY',json.dumps(body));self.assertNotIn('"split"',json.dumps(body))

    def test_family_or_exact_text_leakage_rejected(self):
        rows=[{'split':'train','family_id':'a','text':'x'},{'split':'test','family_id':'a','text':'y'}]
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,'Family leakage'):fit(rows,Path(d))
            rows[1].update(family_id='b',text='x')
            with self.assertRaisesRegex(ValueError,'Exact text leakage'):fit(rows,Path(d))

    def test_training_disagreement_cannot_supply_missing_class(self):
        rows=[{'split':'train','family_id':k,'text':k,'state':k,'machine_agreement':k!='retracted'} for k in LABELS]
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,'Missing class'):fit(rows,Path(d))

    def test_unknown_subject_is_not_other_person(self):
        f={'subject':'unresolved_pronoun','assertion':'affirmed','temporality':'unspecified'}
        self.assertEqual(rule_label(f),'unknown');f['subject']='other_person';self.assertEqual(rule_label(f),'other_person')
        f.update(subject='patient',temporality='historical');self.assertEqual(rule_label(f),'historical')
        f['assertion']='retracted';self.assertEqual(rule_label(f),'retracted')

    def test_shadow_requires_source_bound_quote(self):
        f={'method':'context_rules','concept':'ef','evidence':[{'quote':'EF 30%','context':'EF unavailable'}]}
        self.assertIsNone(example_from_fact(f));f['evidence'][0]['context']='Prior EF 30%.'
        self.assertEqual(example_from_fact(f)['target_quote'],'EF 30%')

    def test_shadow_cache_keeps_patient_provenance_and_never_changes_facts(self):
        class FakeStudent:
            identity='mock_model'
            def __init__(self,path):pass
            def predict(self,rows):return [{'label':'negated','acceptance':'shadow_only','decision_authority':False} for _ in rows]
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'student.joblib').write_bytes(b'fixture')
            rows=[{'patient_id':pid,'state':{'facts':[{'fact_id':pid+'f','method':'context_rules','concept':'ef',
                'assertion':'affirmed','subject':'patient','temporality':'unspecified',
                'evidence':[{'record_id':pid+'r','quote':'EF 30%','context':'EF 30%.'}]}]}} for pid in ('p','q')]
            original=''.join(json.dumps(r)+'\n' for r in rows);(root/'patients.jsonl').write_text(original)
            (root/'run.json').write_text(json.dumps({'cache_key_digest':'fixture'}))
            with patch('clinical_core.agere.clinical.shadow.ContextStudent',FakeStudent):
                first=run_shadow(root,root);second=run_shadow(root,root)
            self.assertEqual(first['counts']['new_unique_predictions'],1);self.assertEqual(second['counts']['new_unique_predictions'],0)
            self.assertEqual(second['counts']['persistent_prediction_cache_hits'],2);self.assertEqual(second['decisions_changed'],0)
            self.assertEqual((root/'patients.jsonl').read_text(),original)
            overlay=[json.loads(x) for x in (root/'context-shadow.jsonl').read_text().splitlines()]
            self.assertEqual([x['source']['record_id'] for x in overlay],['pr','qr'])
            self.assertTrue(all(x['decision_authority'] is False for x in overlay))

    def test_expanded_authorization_persists_without_implicit_reset(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'db';l=BudgetLedger(path,cap_usd=10)
            l.reserve('old','jev','jev-1.13.0');entry=l.extend_authorization(50,'User explicitly removed the earlier stopping limit; assistant selected operating ceiling')
            self.assertEqual(entry['prior_cap_usd'],10);l.close()
            l=BudgetLedger(path);self.assertEqual(l.cap,50);self.assertEqual(l.receipt()['current_reserved_usd'],.5)
            self.assertEqual(l.db.execute('SELECT COUNT(*) FROM authorization_changes').fetchone()[0],1);l.close()
            with self.assertRaises(ValueError):BudgetLedger(path,cap_usd=10)

if __name__=='__main__':unittest.main()
