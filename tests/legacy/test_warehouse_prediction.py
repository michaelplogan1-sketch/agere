import unittest
import numpy as np
from clinical_core.agere.clinical.source_blocks import poisson_blocks,source_summary
from clinical_core.agere.clinical.warehouse_prediction import features,grouped_split,FEATURES,observed_training_features


class SignalAndForecastInputs(unittest.TestCase):
    def test_feature_selection_uses_training_only_and_preserves_partial_missingness(self):
        rows=[{'values':{'never':None,'partial':None}},
              {'values':{'never':None,'partial':3}},
              {'values':{'never':12,'partial':4}}]
        retained,omitted=observed_training_features(rows,['never','partial'],np.array([True,True,False]))
        self.assertEqual(retained,['partial']);self.assertEqual(omitted,['never'])

    def test_constant_intensity_is_one_block(self):
        r=poisson_blocks([10]*12,[30]*12)
        self.assertEqual(len(r['blocks']),1)
        self.assertAlmostEqual(r['blocks'][0]['records_per_day'],1/3)

    def test_clear_intensity_change_and_count_conservation(self):
        r=poisson_blocks([0,0,0,100,100,100],[30]*6)
        self.assertEqual(r['change_indices'],[3])
        self.assertEqual(sum(b['count'] for b in r['blocks']),300)
        self.assertEqual(sum(b['calendar_days'] for b in r['blocks']),180)

    def test_count_and_exposure_validation(self):
        for n,t in [([-1],[30]),([1],[0]),([True],[30]),([1],[float('nan')])]:
            with self.assertRaises(ValueError):poisson_blocks(n,t)

    def test_source_names_and_arrival_cutoff_survive_method_metadata(self):
        r=source_summary([{'record_id':'past','source_table':'notes','available_date':'2026-07-01'},
                          {'record_id':'future','source_table':'notes','available_date':'2026-08-01'},
                          {'record_id':'invalid','source_table':'notes','available_date':'2026-07-00'}],
                         '2026-07-31',['notes'])['sources'][0]
        self.assertEqual(r['source'],'notes');self.assertEqual(r['source_record_ids'],['past'])

    def test_future_features_are_excluded_even_when_event_is_backdated(self):
        p={'patient_id':'p','birth_date':'1980-01-01'}
        past={'record_id':'a','source_table':'labs','available_date':'2026-07-01','event_date':'2026-06-30',
              'status':'final','test':'egfr','value':60,'unit':'mL/min/1.73m2'}
        future={**past,'record_id':'b','available_date':'2026-08-01','value':10}
        result=features(p,[past,future],[],'2026-07-31')
        self.assertEqual(set(result['values']),set(FEATURES))
        self.assertEqual(result['values']['egfr:last_recorded_value'],60)
        self.assertNotIn('b',result['feature_evidence']['labs'])
        self.assertEqual(result['latest_feature_arrival'],'2026-07-01')

    def test_missing_and_conflicting_labs_are_not_normal_values(self):
        p={'patient_id':'p','birth_date':'1980-01-01'}
        r={'record_id':'a','source_table':'labs','available_date':'2026-07-01','event_date':'2026-06-30',
           'status':'final','test':'egfr','value':60,'unit':'mL/min/1.73m2'}
        result=features(p,[r,{**r,'record_id':'b','value':20}],[],'2026-07-31')
        self.assertIsNone(result['values']['egfr:last_recorded_value'])
        self.assertIsNone(result['values']['potassium:last_recorded_value'])

    def test_household_members_share_split(self):
        self.assertEqual(grouped_split({'patient_id':'a','household_id':'same'}),
                         grouped_split({'patient_id':'b','household_id':'same'}))


class TabPFNChallengerTests(unittest.TestCase):
    def test_candidate_fits_and_verdict_only_promotes_on_win(self):
        import numpy as np
        from clinical_core.agere.clinical import tabular_challengers as tc
        rng = np.random.default_rng(0); X = rng.normal(size=(300, 6)); y = (X[:, 0] + rng.normal(0, 1, 300) > 0).astype(int); X[::9, 2] = np.nan
        m = tc.TabPFNCandidate(n_estimators=2).fit(X[:200], y[:200])
        p = m.predict_proba(X[200:])
        self.assertEqual(p.shape, (100, 2)); self.assertTrue(np.allclose(p.sum(axis=1), 1, atol=1e-5))
        base = {'boosted_blocks': {'test_calibrated': {'log_loss': .50, 'auroc': .80}}}
        lose = tc.verdict({**base, 'tabpfn': {'test_calibrated': {'log_loss': .49, 'auroc': .81}}})
        win = tc.verdict({**base, 'tabpfn': {'test_calibrated': {'log_loss': .45, 'auroc': .81}}})
        worse_auc = tc.verdict({**base, 'tabpfn': {'test_calibrated': {'log_loss': .45, 'auroc': .79}}})
        self.assertEqual((lose['default_model'], win['default_model'], worse_auc['default_model']), ('boosted_blocks', 'tabpfn', 'boosted_blocks'))


if __name__=='__main__':unittest.main()
