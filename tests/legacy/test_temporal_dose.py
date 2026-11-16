"""Authored development evidence: source semantics, not clinical adjudication."""
import json
import sqlite3
import unittest

from clinical_core.agere.clinical import dose as D


class TemporalDose(unittest.TestCase):
    def setUp(self):
        self.db = D.register(sqlite3.connect(':memory:'))
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE record(record_id,patient_id,stream,payload)')

    def add(self, rid, day, *, status='active', dose='25 mg', frequency='BID', stream='medication_lists', **extra):
        p = dict(event_date=day, name='carvedilol', dose=dose, frequency=frequency, list_status=status, **extra)
        self.db.execute('INSERT INTO record VALUES(?,?,?,?)', (rid, 'A', stream, json.dumps(p)))

    def current(self):
        return dict(self.db.execute('SELECT * FROM ' + D.current_dose_sql('2030-05-27', 180)).fetchone())

    def test_stopped_and_held_orders_do_not_become_current_target_doses(self):
        for status in ('stopped', 'discontinued', 'held', 'on_hold', 'inactive', 'not_taking'):
            with self.subTest(status=status):
                self.db.execute('DELETE FROM record')
                self.add('old', '2030-01-01')
                self.add('new', '2030-05-01', status=status)
                row = self.current()
                self.assertEqual(row['dose_status'], 'not_active')
                self.assertIsNone(row['daily_mg'])

    def test_label_cannot_replace_regimen(self):
        self.add('order', '2030-01-01', dose='6.25 mg')
        self.add('label', '2030-05-01', stream='medication_label_evidence',
                 product_description='carvedilol 25 mg', strength=25, strength_unit='mg/tablet', directions_text='BID')
        row = self.current()
        self.assertEqual((row['record_id'], row['daily_mg']), ('order', 12.5))

    def test_conflicting_same_date_is_unknown_and_retains_all_sources(self):
        self.add('one', '2030-01-01', dose='6.25 mg')
        self.add('two', '2030-01-01', dose='25 mg')
        row = self.current()
        self.assertEqual(row['dose_status'], 'conflicting')
        self.assertIsNone(row['daily_mg'])
        self.assertEqual(set(json.loads(row['source_record_ids'])), {'one', 'two'})

    def test_undated_evidence_prevents_silent_selection_of_older_regimen(self):
        self.add('dated', '2030-01-01')
        self.add('undated', None, dose='6.25 mg')  # neither event nor recorded date
        self.assertEqual(self.current()['dose_status'], 'unknown_date')

    def test_recorded_date_dates_a_statement_without_event_date(self):
        # Dose rule (2026-10-21): a list/order statement without an event date is dated by its recorded date.
        self.add('dated', '2030-01-01')
        self.add('recorded', None, dose='6.25 mg', recorded_date='2030-05-01')
        row = self.current()
        self.assertEqual((row['dose_date'], row['daily_mg'], row['dose_status']), ('2030-05-01', 12.5, 'below_target'))

    def test_unknown_status_and_missing_dose_are_not_at_target(self):
        self.add('one', '2030-01-01', status=None)
        self.assertEqual(self.current()['dose_status'], 'unknown_status')
        self.db.execute('DELETE FROM record')
        self.add('two', '2030-01-01', dose=None)
        self.assertEqual(self.current()['dose_status'], 'unknown_dose')

    def test_later_active_statement_resolves_prior_conflict_but_not_actual_use(self):
        self.add('one', '2030-01-01', dose='6.25 mg')
        self.add('two', '2030-01-01', status='stopped')
        self.add('three', '2030-05-01', dose='12.5 mg')
        row = self.current()
        self.assertEqual((row['daily_mg'], row['dose_status']), (25.0, 'below_target'))
        self.assertEqual(row['current_use_established'], 0)

    def test_prn_ranges_and_concentrations_are_not_fixed_daily_doses(self):
        for text in ('daily as needed', 'BID PRN', 'every other day', '1-2 times daily', 'daily except Sunday'):
            with self.subTest(text=text): self.assertIsNone(D.per_day(text))
        for text in ('12.5-25 mg', '5 to 10 mg', 'up to 5 mg', '5 mg/mL', '5 mg per mL', '-5 mg', '- 5 mg', '0 mg'):
            with self.subTest(text=text): self.assertIsNone(D.dose_mg(text))

    def test_strength_without_administration_quantity_is_unknown(self):
        self.db.execute('INSERT INTO record VALUES(?,?,?,?)', ('order','A','medication_orders',json.dumps({
            'event_date':'2030-05-01','medication_name':'carvedilol 25 mg tablet','status':'active',
            'strength':25,'strength_unit':'mg/tablet','times_per_day':2})))
        self.assertIsNone(self.current()['daily_mg'])

    def test_decimal_dose_and_numbered_concentration_do_not_change_scale(self):
        self.assertEqual(D.dose_mg('.5 mg'), 0.5)
        self.assertEqual(D.dose_mg('0.5 mg'), 0.5)
        for text in ('25 mg/5 mL', '25 mg per 5 mL', '25 mg in 5 mL', '1,025 mg', '-.5 mg', '.5 to .75 mg'):
            with self.subTest(text=text): self.assertIsNone(D.dose_mg(text))

    def test_explicit_tablet_quantity_and_frequency_are_supported(self):
        self.db.execute('INSERT INTO record VALUES(?,?,?,?)', ('order','A','medication_orders',json.dumps({
            'event_date':'2030-05-01','medication_name':'carvedilol','status':'active',
            'strength':25,'strength_unit':'mg/tablet','dose':0.5,'dose_unit':'tablet','times_per_day':2})))
        self.assertEqual(self.current()['daily_mg'], 25.0)

    def test_future_and_stale_statements_do_not_replace_window_evidence(self):
        self.add('stale', '2028-01-01')
        self.add('active', '2030-01-01', dose='6.25 mg')
        self.add('future', '2030-06-01')
        self.assertEqual(self.current()['record_id'], 'active')

    def test_reference_ranges_are_not_scalar_maximum_tolerated_targets(self):
        self.assertEqual((D.target_mg('losartan'), D.target_max_mg('losartan')), (50,150))
        self.assertEqual((D.target_mg('enalapril'), D.target_max_mg('enalapril')), (20,40))
        self.assertEqual((D.target_mg('carvedilol CR'), D.target_max_mg('carvedilol CR')), (80,80))
        self.add('high', '2030-01-01', dose='100 mg')
        row=self.current()
        self.assertEqual(row['dose_status'],'above_reference')
        self.assertEqual(row['target_reference'],D.REFERENCE['id'])

    def test_unspecified_formulation_is_not_assumed_to_be_tartrate(self):
        self.assertEqual(D.drug_key('metoprolol'), 'metoprolol_unspecified')
        self.assertEqual(D.drug_key('metoprolol tartrate'), 'metoprolol_tartrate')
        self.assertEqual(D.drug_key('metoprolol succinate'), 'metoprolol_succinate')
        self.assertIsNone(D.target_mg('metoprolol'))

    def test_disagreeing_frequency_fields_do_not_silently_choose_one(self):
        self.add('one','2030-01-01',frequency='BID',times_per_day=1)
        self.assertEqual(self.current()['dose_status'],'unknown_dose')

    def test_equal_daily_totals_do_not_resolve_conflicting_regimens(self):
        self.add('one','2030-01-01',dose='12.5 mg',frequency='BID')
        self.add('two','2030-01-01',dose='25 mg',frequency='daily')
        self.assertEqual(self.current()['dose_status'],'conflicting')


if __name__ == '__main__': unittest.main()
