"""Population-wide claim-line services, claim money and pharmacy events (population_events.py, semantic-layer/17), the
snapshot rule that keeps adjudication chains (warehouse.adjudication_step) and the governed metrics that read the unified
tables. Own fixtures and phrasings only; no network, no model, no paid call."""
import json, os, sqlite3, tempfile, unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault('AGERE_METRIC_ENCODER', 'off')

from clinical_core.agere import warehouse as W
from clinical_core.agere.clinical import (client_data as CD, metric_router as MR, metrics, nl2sql, population_events as PE,
                                          population_feeds as PF, semantic_layer as SL)

AS_OF = '2030-05-27'
YEAR = lambda y: {'period': {'preset': 'year', 'year': y}}


def rec(rid, pid, stream, d, **payload):
    return {'record_id': rid, 'patient_id': pid, 'source_table': stream, 'event_date': d, 'available_date': d, **payload}


def mc(rid, cid, status, d, paid, billed=10000, deltas=True, **kw):
    """Provider medical_claims adjudication (financial deltas unless a facility DRG row)."""
    extra = {'financial_deltas_cents': {'payer': paid, 'allowed': paid, 'billed': billed}} if deltas else {}
    return rec(rid, 'P1', 'medical_claims', d, claim_id=cid, status=status, service_date=kw.pop('sd', '2029-02-01'), billed_cents=billed,
               payer_paid_cents=abs(paid), allowed_cents=abs(paid), source_system='PAYER-A', **extra, **kw)


def pmc(rid, cid, seq, status, d, paid, pid='M1', sd='2029-04-01', charge=200.0, freq='1', **kw):
    return rec(rid, pid, 'payer_medical_claims', d, payer_claim_id=cid, adjudication_sequence=seq, claim_status=status,
               service_from=sd, total_charge=charge, paid_amount=paid, allowed_amount=abs(paid), paid_date=d,
               claim_received_date=sd, claim_frequency_code=freq, payer_id='PAYER-B', claim_type='professional', **kw)


def line(rid, cid, code, d, pid='M1', system='CPT'):
    return rec(rid, pid, 'payer_medical_claim_lines', d, payer_claim_id=cid, line_number=1, procedure_code=code,
               procedure_code_system=system, service_date=d, charge_amount=50.0, allowed_amount=40.0)


def rx(rid, cid, status, d, plan, pid='M1', **kw):
    kw = {'fill_number': 0, **kw}
    return rec(rid, pid, 'payer_pharmacy_claims', d, rx_claim_id=cid, claim_status=status, plan_paid=plan, drug_name='atorvastatin 20 MG',
               ndc='1', prescription_number='RX9', pharmacy_npi='N1', payer_id='PAYER-B', days_supply=30, **kw)


RECORDS = [
    # Provider claim C1: paid, reversed by the payer, repaid. The payer feed carries a copy of it (same patient, date, charge).
    mc('C1-a', 'C1', 'paid', '2029-02-20', 8000),
    mc('C1-b', 'C1', 'reversed', '2029-03-10', -8000, supersedes_record_id='C1-a'),
    mc('C1-c', 'C1', 'paid', '2029-03-20', 8500),
    pmc('COPY-1', 'PC-COPY', 1, 'paid', '2029-02-20', 80.0, pid='P1', sd='2029-02-01', charge=100.0),
    # Facility DRG row: no financial deltas, its paid amount counts.
    mc('C2-a', 'C2', 'paid', '2029-05-01', 50000, billed=70000, deltas=False, sd='2029-04-20'),
    # Population member: denied then paid on a later adjudication sequence (both adjudications stay current).
    pmc('PC1-1', 'PC1', 1, 'denied', '2029-04-10', 0.0),
    pmc('PC1-2', 'PC1', 2, 'paid', '2029-05-01', 150.0, supersedes_record_id='PC1-1'),
    # Paid, voided by the provider, replaced by a frequency-7 '-R' claim that carries the payment.
    pmc('PC2-1', 'PC2', 1, 'paid', '2029-06-05', 300.0, sd='2029-06-01', charge=300.0),
    pmc('PC2-2', 'PC2', 2, 'voided', '2029-07-01', 0.0, sd='2029-06-01', charge=300.0, freq='8', supersedes_record_id='PC2-1'),
    pmc('PC2R-1', 'PC2-R', 1, 'paid', '2029-07-02', 280.0, sd='2029-06-01', charge=300.0, freq='7', original_payer_claim_id='PC2'),
    # Paid then recouped (negative paid on the recoupment row).
    pmc('PC3-1', 'PC3', 1, 'paid', '2029-08-01', 100.0, sd='2029-07-15', charge=100.0),
    pmc('PC3-2', 'PC3', 2, 'recouped', '2029-09-01', -100.0, sd='2029-07-15', charge=100.0, supersedes_record_id='PC3-1'),
    # Claim-line services for the member: flu vaccine, AWV, screening mammogram, HbA1c test; one flu line duplicates the EHR shot.
    pmc('PC4-1', 'PC4', 1, 'paid', '2029-10-20', 60.0, sd='2029-10-15', charge=60.0),
    line('L1', 'PC4', '90662', '2029-10-15'), line('L2', 'PC4', 'G0439', '2029-10-15', system='HCPCS'),
    line('L3', 'PC4', '77067', '2029-10-15'), line('L4', 'PC4', '83036', '2029-10-15'),
    line('L5', 'PC4', '90686', '2029-11-02', pid='P1'),
    rec('IMM-1', 'P1', 'immunizations', '2029-11-01', vaccine='influenza', administration_status='completed'),
    # Voided claim: its lines are not evidence.
    pmc('PC5-1', 'PC5', 1, 'voided', '2029-03-01', 0.0, pid='M2', sd='2029-02-15', freq='8'),
    line('L6', 'PC5', '90662', '2029-02-15', pid='M2'),
    # Pharmacy: our pharmacy's transactions + payer feed (refill-too-soon reject 79, a return-to-stock reversal, a recoupment).
    rec('T1', 'P1', 'pharmacy_claim_transactions', '2029-03-01', claim_id='FILL-1', transaction_status='paid', payer_paid_delta_cents=4000,
        source_system='PAYER-A'),
    rec('T2', 'P1', 'pharmacy_claim_transactions', '2029-03-05', claim_id='FILL-2', transaction_status='rejected',
        reject_code='REFILL_TOO_SOON', payer_paid_delta_cents=0, source_system='PAYER-A'),
    rec('D1', 'P1', 'pharmacy_dispenses', '2029-04-01', fill_id='FILL-3', status='returned_to_stock', medication_name='lisinopril',
        return_reason='patient_declined_collection'),
    rx('R1', 'RXC-1', 'paid', '2029-03-02', 25.0),
    rx('R2', 'RXC-2', 'rejected', '2029-03-03', 0.0, reject_code='79'),
    rx('R3', 'RXC-3', 'paid', '2029-04-02', 12.0, fill_number=1),
    rx('R4', 'RXC-3', 'reversed', '2029-04-12', 0.0, fill_number=1, reversal_reason='return_to_stock', transaction_code='B2',
       supersedes_record_id='R3'),
    rx('R5', 'RXC-4', 'paid', '2029-05-02', 30.0, fill_number=2),
    rx('R6', 'RXC-4', 'recouped', '2029-06-02', -30.0, fill_number=2, supersedes_record_id='R5'),
]


def build_fixture(path):
    out = sqlite3.connect(path)
    out.executescript(SL.DDL)
    out.executemany('INSERT INTO record VALUES (?,?,?,?,?,?,?,?)',
                    [(r['record_id'], r['patient_id'], r['source_table'], r['event_date'], r['event_date'], r['available_date'], None,
                      json.dumps(r)) for r in RECORDS])
    out.execute('CREATE INDEX record_stream ON record(stream, patient_id)')
    out.executescript("""CREATE TABLE immunization(record_id TEXT, patient_id TEXT, vaccine TEXT, product TEXT, cvx TEXT, mvx TEXT, status TEXT,
          event_date TEXT, available_date TEXT);
        INSERT INTO immunization SELECT record_id, patient_id, json_extract(payload,'$.vaccine'), NULL, NULL, NULL,
          json_extract(payload,'$.administration_status'), event_date, available_date FROM record WHERE stream = 'immunizations';""")
    SL._add(out, 'measurement', 'loinc TEXT', 'loinc_source TEXT')
    with mock.patch.object(SL, '_normalize', lambda out: None):
        SL._derived(out)
    SL._settle_claims(out)
    SL._claim_codes(out)
    out.executescript(PF.DDL)
    PF._payer_claims(out)
    CD.build_claims(out)
    summary = PE.build(out)
    out.commit(); out.close()
    return summary


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name) / 'semantic.sqlite3'
        cls.summary = build_fixture(cls.path)
        cls.db = sqlite3.connect(cls.path)
        cls.lib = MR.routable()

    @classmethod
    def tearDownClass(cls):
        cls.db.close(); cls.tmp.cleanup()

    def one(self, sql, *args):
        return self.db.execute(sql, args).fetchone()[0]

    def metric(self, mid, question='', **slots):
        m = self.lib[mid]
        node = MR.compile_metric(mid, m, MR.validate_slots(m, slots), AS_OF, question)
        return self.db.execute(node['parameters']['query']).fetchall()[0][0], node['parameters']['query']


class Snapshot(unittest.TestCase):
    def test_adjudication_steps_keep_their_parent(self):
        paid = {'source_table': 'payer_medical_claims', 'adjudication_sequence': 1, 'payer_claim_id': 'A'}
        self.assertTrue(W.adjudication_step(paid, {**paid, 'adjudication_sequence': 2}))
        self.assertTrue(W.adjudication_step(paid, {**paid, 'payer_claim_id': 'A-R'}))
        self.assertFalse(W.adjudication_step(paid, dict(paid)))  # same sequence, same claim: a correction replaces it
        self.assertTrue(W.adjudication_step({'source_table': 'payer_pharmacy_claims'}, {'source_table': 'payer_pharmacy_claims', 'claim_status': 'recouped'}))
        self.assertFalse(W.adjudication_step({'source_table': 'payer_pharmacy_claims'}, {'source_table': 'payer_pharmacy_claims', 'claim_status': 'paid'}))
        self.assertTrue(W.adjudication_step({'source_table': 'medical_claims'}, {'source_table': 'medical_claims', 'status': 'reversed'}))
        self.assertFalse(W.adjudication_step({'source_table': 'labs'}, {'source_table': 'labs', 'status': 'corrected'}))

    def test_snapshot_keeps_the_chain_and_drops_corrections(self):
        with tempfile.TemporaryDirectory() as tmp:
            wh = W.Warehouse(Path(tmp) / 'w.sqlite3')
            rows = [('r1', 'P1', 'payer_medical_claims', pmc('r1', 'A', 1, 'denied', '2029-01-05', 0.0, pid='P1')),
                    ('r2', 'P1', 'payer_medical_claims', pmc('r2', 'A', 2, 'paid', '2029-02-05', 10.0, pid='P1', supersedes_record_id='r1')),
                    ('r3', 'P1', 'labs', rec('r3', 'P1', 'labs', '2029-01-01', value=1)),
                    ('r4', 'P1', 'labs', rec('r4', 'P1', 'labs', '2029-01-02', value=2, supersedes_record_id='r3'))]
            wh.db.execute("INSERT INTO releases VALUES ('rel', ?)", (json.dumps({'as_of': AS_OF}),))
            wh.db.execute("INSERT INTO patients VALUES ('rel', 'P1', ?)", (json.dumps({'patient_id': 'P1'}),))
            wh.db.executemany("INSERT INTO records VALUES ('rel',?,?,?,?,?, 'matched', ?, 'h', ?)",
                              [(rid, pid, st, p['event_date'], p['available_date'], p.get('supersedes_record_id'), json.dumps(p))
                               for rid, pid, st, p in rows])
            patients, meta = wh.snapshot('rel', AS_OF)
            kept = sorted(r['record_id'] for r in patients['P1']['records'])
            self.assertEqual(kept, ['r1', 'r2', 'r4'])
            self.assertEqual(meta['superseded_by_stream'], {'labs': 1})


class ClaimServices(Fixture):
    def test_claim_line_services_join_the_entity_tables(self):
        self.assertEqual(self.one("SELECT count(*) FROM wellness_visit WHERE source = 'payer_claim_line' AND awv = 1 AND patient_id = 'M1'"), 1)
        self.assertEqual(self.one("SELECT awv_kind FROM wellness_visit WHERE source = 'payer_claim_line'"), 'subsequent')
        self.assertEqual(self.one("SELECT count(*) FROM cancer_screening WHERE source = 'payer_claim_line' AND code = '77067'"), 1)
        self.assertEqual(self.one("SELECT count(*) FROM measurement WHERE source_table = 'payer_claim_line' AND concept = 'hba1c'"), 1)

    def test_flu_shot_seen_twice_counts_once_and_voided_claims_are_not_evidence(self):
        # P1: EHR shot 2029-11-01 and a claim line 2029-11-02 (1 day apart) = one shot; M2's line sits on a voided claim.
        self.assertEqual(self.one("SELECT count(*) FROM immunization WHERE patient_id = 'P1'"), 1)
        self.assertEqual(self.one("SELECT count(*) FROM immunization WHERE patient_id = 'M2'"), 0)
        self.assertEqual(self.one("SELECT season FROM immunization WHERE patient_id = 'M1'"), '2029-2030')
        self.assertEqual(self.one("SELECT count(DISTINCT patient_id) FROM immunization WHERE lower(vaccine) LIKE 'influenza%' AND season = '2029-2030'"), 2)

    def test_season_of(self):
        self.assertEqual((PE.season_of('2029-07-01'), PE.season_of('2030-06-30'), PE.season_of(None)), ('2029-2030', '2029-2030', None))


class ClaimMoney(Fixture):
    def test_payer_copy_of_a_provider_claim_is_dropped(self):
        self.assertIsNone(self.db.execute("SELECT 1 FROM claim WHERE claim_id = 'PC-COPY'").fetchone())
        self.assertEqual(self.summary['payer_copies_of_provider_claims'], 1)
        self.assertEqual(self.one("SELECT count(*) FROM claim_adjudication WHERE claim_id = 'PC-COPY'"), 0)

    def test_replacement_folds_into_its_original(self):
        self.assertIsNone(self.db.execute("SELECT 1 FROM claim WHERE claim_id = 'PC2-R'").fetchone())
        self.assertEqual(self.one("SELECT net_payer_paid_cents FROM claim WHERE claim_id = 'PC2'"), 28000)  # 300 - 300 (void) + 280

    def test_net_paid_covers_reversals_recoupments_and_drg_rows(self):
        net = dict(self.db.execute('SELECT claim_id, net_payer_paid_cents FROM claim'))
        self.assertEqual((net['C1'], net['C2'], net['PC1'], net['PC3']), (8500, 50000, 15000, 0))
        self.assertEqual(self.one("SELECT sum(payer_paid_delta_cents) FROM claim_adjudication WHERE claim_id = 'PC3'"), 0)

    def test_first_status_first_paid_and_reversal_dates(self):
        row = self.db.execute("SELECT first_status, final_status, ever_denied, denied_then_paid, first_paid_date FROM claim "
                              "WHERE claim_id = 'PC1'").fetchone()
        self.assertEqual(row, ('denied', 'paid', 1, 1, '2029-05-01'))
        self.assertEqual(self.db.execute("SELECT first_paid_date, first_reversal_date FROM claim WHERE claim_id = 'C1'").fetchone(),
                         ('2029-02-20', '2029-03-10'))

    def test_governed_claim_metrics_read_both_sources(self):
        v, sql = self.metric('days_to_payer_cash', 'Average lag from service to the first payment, for claims first paid during 2029?', **YEAR(2029))
        self.assertNotIn("stream = 'medical_claims'", sql)
        # C1 19 days, C2 11, PC1 30, PC2 4, PC3 17, PC4 5
        self.assertAlmostEqual(v, (19 + 11 + 30 + 4 + 17 + 5) / 6)
        v, _ = self.metric('claims_reversed', 'Count of claims the insurer reversed in 2029', **YEAR(2029))
        self.assertEqual(v, 1)
        v, _ = self.metric('medical_claims', measures=['claims'], anchor='reversal_date', **YEAR(2029))
        self.assertEqual(v, 1)
        v, _ = self.metric('medical_claims', measures=['net_payer_paid_usd'], **YEAR(2029))
        self.assertAlmostEqual(v, (8500 + 50000 + 15000 + 28000 + 0 + 6000) / 100.0)

    def test_metric_definitions_name_the_sources(self):
        lib = metrics.library()
        self.assertEqual(lib['claims_reversed']['time_anchor']['field'], 'claim_adjudication.event_date')
        self.assertIn('payer-feed', lib['net_payer_paid']['summary'])
        for mid in ('claims_reversed', 'days_to_payer_cash', 'net_payer_paid'):
            self.assertEqual(lib[mid]['version'], 2, mid)


class Pharmacy(Fixture):
    def test_ncpdp_79_is_refill_too_soon_on_both_sources(self):
        rows = self.db.execute("SELECT source, claim_id FROM pharmacy_claim_event WHERE reject_reason = 'REFILL_TOO_SOON'").fetchall()
        self.assertEqual(sorted(rows), [('payer_pharmacy_claims', 'RXC-2'), ('pharmacy_claim_transactions', 'FILL-2')])
        v, _ = self.metric('pharmacy_claim_net_paid', 'How many prescriptions bounced as too early to refill last year?',
                           measure='refill_too_soon_rejections', **YEAR(2029))
        self.assertEqual(v, 2)

    def test_net_pharmacy_spend_takes_back_reversals_and_recoupments(self):
        # 40 (ours) + 25 + 12 - 12 (B2 reversal takes back the reversed claim's payment) + 30 - 30 (recouped)
        v, sql = self.metric('pharmacy_claim_net_paid', 'Net plan spend on prescriptions in 2029?', **YEAR(2029))
        self.assertIn('FROM pharmacy_claim_event r', sql)
        self.assertAlmostEqual(v, 65.0)

    def test_returned_to_stock_counts_both_pharmacy_sources(self):
        rows = self.db.execute('SELECT source, fill_key FROM returned_fill ORDER BY 1').fetchall()
        self.assertEqual(rows, [('payer_pharmacy_claims', 'RXC-3'), ('pharmacy_dispenses', 'FILL-3')])
        v, _ = self.metric('pharmacy_returns_to_stock', 'Fills that were never collected by the patient in 2029?', **YEAR(2029))
        self.assertEqual(v, 2)

    def test_reversed_and_recouped_fills_are_not_pickups(self):
        fills = dict(self.db.execute('SELECT record_id, counts_as_fill FROM payer_pharmacy_claim'))
        self.assertEqual((fills['R1'], fills['R3'], fills['R5']), (1, 0, 0))
        self.assertEqual(self.one("SELECT reversed_by_record_id FROM payer_pharmacy_claim WHERE record_id = 'R5'"), 'R6')


class Upgrade(Fixture):
    def test_rerun_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 's.sqlite3'
            build_fixture(p)
            db = sqlite3.connect(p)
            before = [db.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in ('claim', 'immunization', 'wellness_visit', 'returned_fill')]
            db.close()
            PE.apply(p)
            db = sqlite3.connect(p)
            after = [db.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in ('claim', 'immunization', 'wellness_visit', 'returned_fill')]
            self.assertEqual(before, after)
            self.assertIn('population_events', dict(db.execute('SELECT key, value FROM meta')))
            db.close()

    def test_layer_docs_describe_the_new_tables(self):
        self.assertEqual(SL.VERSION, 'semantic-layer/17')
        for t in ('cancer_screening', 'claim_adjudication', 'pharmacy_claim_event', 'returned_fill'):
            self.assertIn(t, SL.SCHEMA_DOC)
            self.assertIn(t, nl2sql.TABLES)
        self.assertIn('claim.first_paid_date', nl2sql.TABLES)


if __name__ == '__main__':
    unittest.main()
