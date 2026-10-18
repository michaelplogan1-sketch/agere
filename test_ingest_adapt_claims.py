"""Fixture tests for clinical_core/agere/ingest/adapt_claims.py (payer-side canonical records). Synthetic rows only.

    .venv/bin/python -m unittest test_ingest_adapt_claims
"""
import sqlite3
import unittest

from clinical_core.agere.ingest import adapt_claims as A

LINEAGE = ('_feed', '_instance', '_drop', '_file', '_row')


class Landing:
    """In-memory landing db: tables/columns appear as rows are added (like ingest/landing.py)."""

    def __init__(self):
        self.db = sqlite3.connect(':memory:')
        self.cols = {}
        self.n = 0

    def add(self, table, _file, **row):
        self.n += 1
        parts = _file.split('/')
        row = dict(row, _feed=parts[0], _instance=parts[1] if len(parts) > 2 else None, _drop=None, _file=_file,
                   _row=self.n)
        cols = self.cols.setdefault(table, [])
        if not cols:
            self.db.execute(f'CREATE TABLE "{table}" (' + ', '.join(f'"{c}"' for c in LINEAGE) + ')')
            cols.extend(LINEAGE)
        for k in row:
            if k not in cols:
                self.db.execute(f'ALTER TABLE "{table}" ADD COLUMN "{k}"')
                cols.append(k)
        keys = list(row)
        self.db.execute(f'INSERT INTO "{table}" (' + ','.join(f'"{k}"' for k in keys) + ') VALUES (' +
                        ','.join('?' * len(keys)) + ')', [row[k] for k in keys])

    # ---- X12 helpers
    def c837(self, f, isa, key, cid, member, freq='1', tx='837P', lines=(), dx=('I10',), orig=None, charge=100.0,
             idate='2027-01-10', **extra):
        self.add('x12_837_claim', f, isa_control_number=isa, claim_key=key, transaction_type=tx,
                 patient_control_number=cid, subscriber_member_id=member, total_charge=charge, facility_code='11',
                 place_of_service='11' if tx == '837P' else None, frequency_code=freq, original_reference=orig,
                 billing_provider_npi='1111111111', rendering_npi='2222222222', interchange_date=idate, **extra)
        for i, code in enumerate(dx, 1):
            self.add('x12_837_dx', f, isa_control_number=isa, claim_key=key, dx_sequence=i, dx_code=code,
                     is_principal=int(i == 1))
        for i, (code, chg, svc) in enumerate(lines, 1):
            self.add('x12_837_line', f, isa_control_number=isa, claim_key=key, line_number=str(i), line_ordinal=i,
                     procedure_code=code, charge=chg, units=1.0, service_date=svc)

    def c835(self, f, isa, st, key, cid, status, paid, resp=0.0, freq='1', icn=None, eff='2027-02-01', cas=(),
             member='M1', charge=100.0, orig=None):
        self.add('x12_835_claim', f, isa_control_number=isa, st_control_number=st, claim_key=key,
                 patient_control_number=cid, claim_status_code=status, total_charge=charge, paid_amount=paid,
                 patient_responsibility=resp, payer_claim_control_number=icn or f'ICN{self.n}', frequency_code=freq,
                 patient_id=None, patient_id_qualifier=member, original_reference=orig)
        for i, (grp, reason, amt) in enumerate(cas, 1):
            self.add('x12_835_adjustment', f, isa_control_number=isa, claim_key=key, level='claim', cas_sequence=i,
                     group_code=grp, reason_code=reason, amount=amt)
        self.add('x12_835_payment', f, isa_control_number=isa, st_control_number=st, effective_date=eff,
                 interchange_date=eff)


def run(land, people=None):
    people = people or {}
    recs = list(A.records(land.db, lambda k: people.get(k, 'P-' + k) if people is not None else None))
    by = {}
    for r in recs:
        by.setdefault(r['source_table'], []).append(r)
    return by, A.last_report()


def claims(by, cid):
    return sorted((r for r in by.get('payer_medical_claims', []) if r['payer_claim_id'] == cid),
                  key=lambda r: r['adjudication_sequence'])


class MedicalClaims(unittest.TestCase):
    def test_original_replacement_void(self):
        L = Landing()
        f1 = 'x12_payer/WH-PAYER-C1/2027-W02/C1_837P_2027-W02.x12'
        L.c837(f1, '100', 'k1', 'WHN-CLM-aaa', 'C1MEMBER01', lines=[('99213', 100.0, '2027-01-03')])
        f2 = 'x12_payer/WH-PAYER-C1/2027-W06/C1_837P_2027-W06.x12'
        L.c837(f2, '101', 'k1', 'WHN-CLM-aaa', 'C1MEMBER01', freq='8', lines=[('99213', 100.0, '2027-01-03')])
        L.c837(f2, '101', 'k2', 'WHN-CLM-aaa-R', 'C1MEMBER01', freq='7', orig='WHN-CLM-aaa',
               lines=[('99214', 150.0, '2027-01-03')], charge=150.0)
        p1 = 'x12_payer/WH-PAYER-C1/2027-W05/C1_835_2027-W05.x12'
        L.c835(p1, '200', '0001', 'e1', 'WHN-CLM-aaa', '1', 80.0, 20.0, eff='2027-02-01', cas=[('CO', '45', 0.0)])
        p2 = 'x12_payer/WH-PAYER-C1/2027-W08/C1_835_2027-W08.x12'
        L.c835(p2, '201', '0001', 'e1', 'WHN-CLM-aaa', '22', 0.0, 0.0, freq='8', eff='2027-02-20', cas=[('CO', '129', 0)])
        L.c835(p2, '201', '0001', 'e2', 'WHN-CLM-aaa-R', '1', 120.0, 30.0, freq='7', eff='2027-02-20')
        by, rep = run(L)
        orig = claims(by, 'WHN-CLM-aaa')
        self.assertEqual([r['claim_status'] for r in orig], ['paid', 'voided'])
        self.assertEqual([r['adjudication_sequence'] for r in orig], [1, 2])
        self.assertEqual(orig[1]['supersedes_record_id'], orig[0]['record_id'])
        self.assertEqual(orig[1]['claim_frequency_code'], '8')
        self.assertEqual(orig[1]['carc'], 'CO-129')
        self.assertIsNone(orig[0]['carc'])  # CO-45 is routine contractual, not a denial reason
        self.assertEqual(orig[0]['allowed_amount'], 100.0)
        self.assertEqual(orig[0]['diagnosis_codes'], ['I10'])
        self.assertEqual(orig[0]['product'], 'commercial')
        self.assertEqual(orig[0]['patient_id'], 'P-member:WH-PAYER-C1:C1MEMBER01')
        repl = claims(by, 'WHN-CLM-aaa-R')
        self.assertEqual(len(repl), 1)
        self.assertEqual(repl[0]['original_payer_claim_id'], 'WHN-CLM-aaa')
        self.assertEqual(repl[0]['claim_frequency_code'], '7')
        self.assertEqual(repl[0]['supersedes_record_id'], orig[0]['record_id'])
        lines = [l for l in by['payer_medical_claim_lines'] if l['payer_claim_id'] == 'WHN-CLM-aaa-R']
        self.assertEqual([(l['procedure_code'], l['procedure_code_system']) for l in lines], [('99214', 'CPT')])
        for r in orig + repl:
            self.assertTrue(r['record_id'].startswith('AGR-'))
            self.assertTrue(r['_lineage'])

    def test_paid_reversal_repay(self):
        L = Landing()
        L.c837('x12_payer/WH-PAYER-MCD2/2028-W20/MCD2_837P_2028-W20.x12', '300', 'k1', 'WHN-CLM-bbb', 'MCD2MEMBER9',
               lines=[('99213', 100.0, '2028-05-01')])
        for n, (status, paid, eff) in enumerate([('1', 79.21, '2028-06-10'), ('22', -79.21, '2028-09-10'),
                                                  ('1', 79.21, '2028-10-20')]):
            L.c835(f'x12_payer/WH-PAYER-MCD2/2028-W{30 + n}/MCD2_835_2028-W{30 + n}.x12', f'40{n}', '0001', 'e1',
                   'WHN-CLM-bbb', status, paid, eff=eff)
        by, _ = run(L)
        ev = claims(by, 'WHN-CLM-bbb')
        self.assertEqual([r['claim_status'] for r in ev], ['paid', 'reversed', 'paid'])
        self.assertEqual([r['paid_amount'] for r in ev], [79.21, -79.21, 79.21])
        self.assertEqual([r['paid_date'] for r in ev], ['2028-06-10', '2028-09-10', '2028-10-20'])
        self.assertEqual([r['event_date'] for r in ev], ['2028-06-10', '2028-09-10', '2028-10-20'])
        self.assertIsNone(ev[0]['supersedes_record_id'])
        self.assertEqual(ev[1]['supersedes_record_id'], ev[0]['record_id'])
        self.assertEqual(ev[2]['supersedes_record_id'], ev[1]['record_id'])
        self.assertEqual(ev[0]['product'], 'medicaid')

    def test_provider_and_payer_copies_collapse_and_resend_dropped(self):
        L = Landing()
        L.c837('x12/837p/WHN-PRACTICE-IN-0/2027-04/WHN-PRACTICE-IN-0_837P_202704.x12', '500', 'k1', 'ccc', 'C2MEMBER77',
               lines=[('99213', 90.0, '2027-04-02')])
        L.c835('x12/835/WHN-PRACTICE-IN-0/2027-05/C2_835_202705.x12', '501', '0001', 'e1', 'ccc', '1', 60.0)
        payer = 'x12_payer/WH-PAYER-C2/2027-W17/C2_837P_2027-W17.x12'
        L.c837(payer, '502', 'k1', 'WHN-CLM-ccc', 'C2MEMBER77', lines=[('99213', 90.0, '2027-04-02')])
        L.c837(payer.replace('.x12', '_resend.x12'), '502', 'k1', 'WHN-CLM-ccc', 'C2MEMBER77',
               lines=[('99213', 90.0, '2027-04-02')])
        L.c835('x12_payer/WH-PAYER-C2/2027-W19/C2_835_2027-W19.x12', '503', '0001', 'e1', 'WHN-CLM-ccc', '1', 60.0,
               eff='2027-05-10')
        by, rep = run(L)
        self.assertEqual(len(by['payer_medical_claims']), 1)
        self.assertEqual(by['payer_medical_claims'][0]['payer_claim_id'], 'WHN-CLM-ccc')
        self.assertEqual(by['payer_medical_claims'][0]['source_billing_claim_id'], 'ccc')
        self.assertEqual(len(by['payer_medical_claim_lines']), 1)
        self.assertEqual(rep['dropped']['provider_837_kept_out_of_payer_streams'], 1)
        self.assertEqual(rep['dropped']['provider_835_kept_out_of_payer_streams'], 1)
        self.assertGreaterEqual(rep['dropped']['resend_file'], 1)

    def test_duplicate_denial_and_crossover_kept(self):
        L = Landing()
        L.c837('x12_payer/WH-PAYER-MSSP/2027-W10/MSSP_837P_2027-W10.x12', '600', 'k1', 'WHN-CLM-eee', '2DX0U77CP79',
               lines=[('99214', 200.0, '2027-03-01')])
        L.c837('x12_payer/WH-PAYER-MSSP/2027-W10/MSSP_837P_2027-W10.x12', '600', 'k2', 'WHN-CLM-eee-D', '2DX0U77CP79')
        L.c835('x12_payer/WH-PAYER-MSSP/2027-W12/MSSP_835_2027-W12.x12', '601', '0001', 'e1', 'WHN-CLM-eee-D', '4', 0.0,
               cas=[('CO', '18', 200.0)])
        L.c837('x12_payer/WH-PAYER-SUP1/2027-W13/SUP1_837P_2027-W13.x12', '602', 'k1', 'WHN-CLM-eee-X', '2DX0U77CP79',
               charge=200.0)
        L.c835('x12_payer/WH-PAYER-SUP1/2027-W15/SUP1_835_2027-W15.x12', '603', '0001', 'e1', 'WHN-CLM-eee-X', '1', 30.0,
               eff='2027-04-10')
        by, rep = run(L)
        dup = claims(by, 'WHN-CLM-eee-D')  # the payer's denial of the duplicate submission is kept as its own claim
        self.assertEqual([(r['claim_status'], r['carc']) for r in dup], [('denied', 'CO-18')])
        self.assertEqual(rep['cross_check']['duplicate_submission_claims_kept'], 1)
        x = claims(by, 'WHN-CLM-eee-X')
        self.assertEqual(len(x), 1)
        self.assertEqual(x[0]['product'], 'medigap')
        self.assertEqual(x[0]['payer_id'], 'WH-PAYER-SUP1')
        self.assertEqual(x[0]['cob'], 'crossover')
        self.assertEqual(x[0]['patient_id'], 'P-member:WH-PAYER-SUP1:2DX0U77CP79')
        self.assertEqual(claims(by, 'WHN-CLM-eee')[0]['product'], 'medicare_ffs')

    def test_mssp_x12_vs_cclf(self):
        L = Landing()
        L.c837('x12_payer/WH-PAYER-MSSP/2027-W10/MSSP_837P_2027-W10.x12', '700', 'k1', 'WHN-CLM-fff', '7Q45WP7HH60',
               lines=[('99213', 120.0, '2027-03-02')])
        L.c835('x12_payer/WH-PAYER-MSSP/2027-W12/MSSP_835_2027-W12.x12', '701', '0001', 'e1', 'WHN-CLM-fff', '1', 25.0,
               5.0, eff='2027-03-20')
        cf = 'cms_cclf_csv/partb_physicians/P.A9999.ACO.ZC5Y27.D270412.T1.csv'
        base = dict(clm_type_cd='71', clm_adjsmt_type_cd='0', clm_disp_cd='01', clm_pos_cd='11', file_date='2027-04-12',
                    clm_line_num='1', clm_line_srvc_unit_qty='1', rndrg_prvdr_npi_num='3333333333')
        L.add('cclf_partb_physicians', cf, cur_clm_uniq_id='1001', bene_mbi_id='7Q45WP7HH60', clm_from_dt='2027-03-02',
              clm_thru_dt='2027-03-02', clm_line_from_dt='2027-03-02', clm_line_hcpcs_cd='99213',
              clm_line_cvrd_pd_amt='70.64', clm_line_alowd_chrg_amt='88.30', clm_cntl_num='20270310C6F550F50B',
              clm_efctv_dt='2027-03-10', clm_idr_ld_dt='2027-03-10', clm_dgns_1_cd='I10', **base)
        # an unmatched CCLF claim: original then cancellation (same clm_cntl_num tail)
        L.add('cclf_partb_physicians', cf, cur_clm_uniq_id='2002', bene_mbi_id='7Q45WP7HH60', clm_from_dt='2027-03-15',
              clm_thru_dt='2027-03-15', clm_line_from_dt='2027-03-15', clm_line_hcpcs_cd='71046',
              clm_line_cvrd_pd_amt='7.65', clm_line_alowd_chrg_amt='9.56', clm_cntl_num='202703205F3E59A15E',
              clm_efctv_dt='2027-03-20', clm_idr_ld_dt='2027-03-20', **base)
        L.add('cclf_partb_physicians', cf, cur_clm_uniq_id='3003', bene_mbi_id='7Q45WP7HH60', clm_from_dt='2027-03-15',
              clm_thru_dt='2027-03-15', clm_line_from_dt='2027-03-15', clm_line_hcpcs_cd='71046',
              clm_line_cvrd_pd_amt='-7.65', clm_line_alowd_chrg_amt='-9.56', clm_cntl_num='202704015F3E59A15E',
              clm_efctv_dt='2027-04-01', clm_idr_ld_dt='2027-04-01',
              **dict(base, clm_adjsmt_type_cd='1', clm_disp_cd='03'))
        by, rep = run(L)
        fff = claims(by, 'WHN-CLM-fff')
        self.assertEqual(len(fff), 1)
        self.assertEqual(fff[0]['paid_amount'], 70.64)
        self.assertEqual(fff[0]['allowed_amount'], 88.3)
        self.assertEqual(fff[0]['x12_paid_amount'], 25.0)
        self.assertEqual(fff[0]['cclf_claim_id'], 'B-C6F550F50B')
        self.assertEqual(rep['dropped']['cclf_claim_matched_mssp_x12'], 1)
        line = [l for l in by['payer_medical_claim_lines'] if l['payer_claim_id'] == 'WHN-CLM-fff'][0]
        self.assertEqual(line['allowed_amount'], 88.3)
        own = claims(by, 'CCLF-B-5F3E59A15E')
        self.assertEqual([r['claim_status'] for r in own], ['paid', 'reversed'])
        self.assertEqual(own[1]['supersedes_record_id'], own[0]['record_id'])
        self.assertEqual(own[0]['product'], 'medicare_ffs')
        self.assertEqual(own[0]['patient_id'], 'P-member:WH-PAYER-MSSP:7Q45WP7HH60')
        self.assertFalse(any(r['payer_claim_id'].startswith('CCLF-B-C6F5') for r in by['payer_medical_claims']))


class Pharmacy(unittest.TestCase):
    def tx(self, L, f, cid, code, status, dos, fill=0, paid=10.0, pay=5.0, reject=None, rx='7000000001'):
        L.add('ncpdp_claim', f, claim_ref=cid, file_date=f.rsplit('_', 1)[-1][:8], bin_number='990001',
              transaction_code=code, pcn='CMRX', service_provider_id_qualifier='01', service_provider_id='1167942964',
              date_of_service=dos, cardholder_id='C1227F77B3A', group_id='GRPC1', rx_service_ref_number=rx,
              product_service_id_qualifier='03', product_service_id='00093731405', quantity_dispensed=30, fill_number=fill,
              days_supply=30, prescriber_id_qualifier='01', prescriber_id='1241139314', response_status_code=status,
              patient_pay_amount=pay, total_amount_paid=paid, reject_codes=reject)

    def test_paid_rejected_reversal_resubmission(self):
        L = Landing()
        f1 = 'ncpdp/WH-PAYER-C1/2026-09/C1_NCPDP_D0_20261006.txt'
        f2 = 'ncpdp/WH-PAYER-C1/2026-10/C1_NCPDP_D0_20261106.txt'
        self.tx(L, f1, 'WHN-RXC-111', 'B1', 'P', '2026-09-11')
        self.tx(L, f1.replace('.txt', '_RESEND.txt'), 'WHN-RXC-111', 'B1', 'P', '2026-09-11')
        self.tx(L, f2, 'WHN-RXC-111', 'B2', 'A', '2026-10-01', paid=0.0, pay=0.0)
        self.tx(L, f1, 'WHN-RXC-222', 'B1', 'R', '2026-09-20', fill=1, paid=0.0, pay=0.0, reject='79', rx='7000000002')
        self.tx(L, f2, 'WHN-RXC-222-R', 'B1', 'P', '2026-10-02', fill=1, rx='7000000002')
        L.add('payers_pharmacy', 'payers/WH-PAYER-C1/2026-09/C1_PHARMACY_20261005.txt', claim_id='WHN-RXC-111',
              member_id='C1227F77B3A', fill_date='20260911', ndc='00093731405', drug_name='clopidogrel 75 MG Oral Tablet',
              status='paid', plan_paid='10.0', member_paid='5.0', payer='C1', rx_number='7000000001', fill_number='0')
        by, rep = run(L)
        rx = {(r['rx_claim_id'], r['claim_status']): r for r in by['payer_pharmacy_claims']}
        self.assertEqual(set(rx), {('WHN-RXC-111', 'paid'), ('WHN-RXC-111', 'reversed'), ('WHN-RXC-222', 'rejected'),
                                   ('WHN-RXC-222-R', 'paid')})
        paid, rev = rx[('WHN-RXC-111', 'paid')], rx[('WHN-RXC-111', 'reversed')]
        # both events stay; the reversal names the payment and keeps the original fill date
        self.assertIsNone(rev['supersedes_record_id'])
        self.assertEqual(rev['reverses_record_id'], paid['record_id'])
        self.assertEqual((rev['fill_date'], rev['event_date'], rev['transaction_date']), ('2026-09-11', '2026-09-11', '2026-10-01'))
        self.assertEqual(rev['transaction_code'], 'B2')
        self.assertEqual(paid['drug_name'], 'clopidogrel 75 MG Oral Tablet')
        self.assertEqual(paid['plan_paid'], 10.0)
        self.assertEqual(paid['patient_pay'], 5.0)
        self.assertEqual(paid['fill_date'], '2026-09-11')
        self.assertEqual(paid['event_date'], '2026-09-11')
        self.assertEqual(paid['product'], 'commercial')
        self.assertEqual(paid['patient_id'], 'P-member:WH-PAYER-C1:C1227F77B3A')
        self.assertEqual(rx[('WHN-RXC-222', 'rejected')]['reject_code'], '79')
        resub = rx[('WHN-RXC-222-R', 'paid')]
        self.assertEqual(resub['original_rx_claim_id'], 'WHN-RXC-222')
        self.assertIsNone(resub['supersedes_record_id'])  # a separate paid event, not a version of the rejection
        self.assertEqual(resub['drug_name'], 'clopidogrel 75 MG Oral Tablet')  # NDC -> name from the flat file
        self.assertEqual(rep['dropped']['resend_file'], 1)


class Enrollment(unittest.TestCase):
    def loop(self, L, f, mt, member, begin=None, end=None, product='COMMERCIAL', ftype='full', fdate='2026-10-05'):
        L.add('x12_834_member', f, isa_control_number=f[-20:], file_type=ftype, file_date=fdate,
              maintenance_type_code=mt, member_id=member, subscriber_id=member[:-2] + '00',
              eligibility_begin_date=begin if mt != '024' else None, benefit_begin_date=begin,
              eligibility_end_date=end, plan_coverage_description=product, group_policy_number='WH-PAYER-C1-COM-01')

    def test_add_then_retro_term_and_flat_fill(self):
        L = Landing()
        self.loop(L, 'x12_834/WH-PAYER-C1/2026-09/C1_834_FULL_20261005.x12', '030', 'C1ABCDEF123', '2026-09-01')
        self.loop(L, 'x12_834/WH-PAYER-C1/2026-11/C1_834_CHG_20261205.x12', '021', 'C1NEWMEM456', '2026-11-01',
                  ftype='change', fdate='2026-12-05')
        self.loop(L, 'x12_834/WH-PAYER-C1/2026-12/C1_834_FULL_20270105.x12', '030', 'C1ABCDEF123', '2026-09-01',
                  fdate='2027-01-05')
        self.loop(L, 'x12_834/WH-PAYER-C1/2027-01/C1_834_CHG_20270205.x12', '024', 'C1ABCDEF123', '2026-09-01',
                  end='2026-11-30', ftype='change', fdate='2027-02-05')
        for f in ('payers/WH-PAYER-C1/2026-09/C1_ELIGIBILITY_20261005.txt',
                  'payers/WH-PAYER-C1/2026-10/C1_ELIGIBILITY_20261105.txt'):
            L.add('payers_eligibility', f, member_id='C1ABCDEF123', subscriber_id='C1ABCDEF100', product='commercial',
                  plan_id='WH-PAYER-C1-COM-01', eff_date='20260901', term_date='', pcp_npi='1085075368',
                  pcp_name='WHN-PRACTICE-TX-7', payer='C1')
        by, rep = run(L)
        spans = sorted((r for r in by['enrollment_spans'] if r['member_id'] == 'C1ABCDEF123'),
                       key=lambda r: r['span_version'])
        self.assertEqual(len(spans), 2)
        v1, v2 = spans
        self.assertEqual((v1['coverage_start'], v1['coverage_end'], v1['enrollment_reason']), ('2026-09-01', None, 'baseline'))
        self.assertEqual(v2['coverage_end'], '2026-11-30')
        self.assertEqual(v2['termination_reason'], 'retroactive_termination')
        self.assertEqual(v2['supersedes_record_id'], v1['record_id'])
        self.assertEqual(v2['prior_coverage_end'], None)
        self.assertEqual(v2['available_date'], '2027-02-05')
        for v in spans:
            self.assertEqual(v['plan_id'], 'WH-PAYER-C1-COM-01')
            self.assertEqual(v['attribution_npi'], '1085075368')
            self.assertEqual(v['attribution_practice_id'], 'WHN-PRACTICE-TX-7')
            self.assertEqual(v['product'], 'commercial')
            self.assertEqual(v['event_date'], '2026-09-01')
            self.assertEqual(v['patient_id'], 'P-member:WH-PAYER-C1:C1ABCDEF123')
        self.assertGreaterEqual(rep['dropped']['repeat_834_loop'], 1)  # the 2027-01 FULL repeats the open span
        new = [r for r in by['enrollment_spans'] if r['member_id'] == 'C1NEWMEM456']
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0]['enrollment_reason'], 'new_enrollment')

    def test_unresolved_person_kept_with_none(self):
        L = Landing()
        self.loop(L, 'x12_834/WH-PAYER-C1/2026-09/C1_834_FULL_20261005.x12', '030', 'C1ABCDEF123', '2026-09-01')
        recs = list(A.records(L.db, lambda k: None))
        self.assertEqual(len(recs), 1)
        self.assertIsNone(recs[0]['patient_id'])
        self.assertEqual(A.last_report()['emitted']['enrollment_spans'], 1)


class ProviderRevenueCycle(unittest.TestCase):
    """Practice-side x12/837p + x12/835 -> medical_claim_submissions, medical_claim_lines, medical_claims,
    provider_remittance_advice (the payer streams stay as they are)."""

    def test_submissions_lines_adjudications(self):
        L = Landing()
        p837 = 'x12/837p/WHN-PRACTICE-IN-0/2027-04/WHN-PRACTICE-IN-0_837P_202704.x12'
        L.c837(p837, '800', 'k1', 'abc123', 'C2MEMBER77', lines=[('99213', 90.0, '2027-04-02'), ('81002', 10.0, '2027-04-02')],
               dx=('I10', 'E119'), charge=100.0, payer_id='WH-PAYER-C2', idate='2027-04-05')
        L.c837(p837.replace('2027-04', '2027-05').replace('202704', '202705'), '801', 'k1', 'abc123', 'C2MEMBER77', freq='7',
               lines=[('99214', 120.0, '2027-04-02')], charge=120.0, payer_id='WH-PAYER-C2', idate='2027-05-03')
        L.c837(p837.replace('.x12', '_resend.x12'), '800', 'k1', 'abc123', 'C2MEMBER77', lines=[('99213', 90.0, '2027-04-02')],
               payer_id='WH-PAYER-C2', idate='2027-04-05')
        r835 = 'x12/835/WHN-PRACTICE-IN-0/2027-05/C2_835_202705.x12'
        L.c835(r835, '900', '0001', 'e1', 'abc123', '1', 60.0, 20.0, eff='2027-05-10', member='C2MEMBER77', charge=120.0,
               cas=[('CO', '45', 40.0), ('PR', '2', 20.0)])
        L.add('x12_835_claim', r835, isa_control_number='900', st_control_number='0001', claim_key='e2',
              patient_control_number='abc123', claim_status_code='22', total_charge=120.0, paid_amount=-60.0,
              patient_responsibility=-20.0, payer_claim_control_number='WHN-CLM-abc123', patient_id_qualifier='C2MEMBER77',
              payer_name='WH-PAYER-C2')
        L.add('x12_835_payment', r835.replace('202705', '202706').replace('2027-05', '2027-06'), isa_control_number='901',
              st_control_number='0001', effective_date='2027-06-10', interchange_date='2027-06-10')
        r835b = 'x12/835/WHN-PRACTICE-IN-0/2027-06/C2_835_202706.x12'
        L.c835(r835b, '902', '0001', 'e1', 'zzz999', '4', 0.0, eff='2027-06-12', member='C2MEMBER77',
               cas=[('CO', '16', 100.0)])
        for r in L.db.execute("SELECT rowid FROM x12_835_claim WHERE claim_key = 'e2'"):
            L.db.execute("UPDATE x12_835_claim SET _file = ?, isa_control_number = '901' WHERE rowid = ?",
                         (r835.replace('202705', '202706').replace('2027-05', '2027-06'), r[0]))
        by, rep = run(L)
        subs = sorted(by['medical_claim_submissions'], key=lambda r: r['event_date'])
        self.assertEqual([s['submission_kind'] for s in subs], ['original', 'corrected'])
        self.assertEqual(subs[1]['original_submission_record_id'], subs[0]['record_id'])
        s0 = subs[0]
        self.assertEqual((s0['claim_id'], s0['service_organization_id'], s0['claim_type'], s0['billed_cents'], s0['status']),
                         ('abc123', 'WHN-PRACTICE-IN-0', 'professional', 10000, 'submitted'))
        self.assertEqual(s0['diagnosis_codes'], ['I10', 'E119'])
        self.assertEqual(s0['member_identifier_submitted'], 'C2MEMBER77')
        self.assertEqual(s0['patient_id'], 'P-member:WH-PAYER-C2:C2MEMBER77')
        self.assertIsNone(s0['supersedes_record_id'])
        lines = by['medical_claim_lines']
        self.assertEqual([(l['service_code'], l['service_code_system'], l['charge_cents']) for l in lines],
                         [('99214', 'CPT', 12000)])  # current submission's lines only
        self.assertEqual(lines[0]['submission_record_id'], subs[1]['record_id'])
        mc = sorted(by['medical_claims'], key=lambda r: (r['claim_id'], r['event_date']))
        paid, rev, den = [r for r in mc if r['claim_id'] == 'abc123'] + [r for r in mc if r['claim_id'] == 'zzz999']
        self.assertEqual((paid['status'], paid['payer_paid_cents'], paid['allowed_cents'], paid['patient_responsibility_cents']),
                         ('paid', 6000, 8000, 2000))
        self.assertEqual(paid['source_system'], 'WH-PAYER-C2')
        self.assertEqual(paid['service_organization_id'], 'WHN-PRACTICE-IN-0')
        self.assertEqual(paid['adjustments'], [{'group': 'CO', 'reason_code': '45', 'amount_cents': 4000},
                                               {'group': 'PR', 'reason_code': '2', 'amount_cents': 2000}])
        self.assertEqual(paid['financial_deltas_cents'], {'billed': 12000, 'allowed': 8000, 'payer': 6000, 'responsibility': 2000,
                                                          'contractual': 4000, 'deductible': 0, 'copay': 0, 'coinsurance': 2000})
        self.assertEqual(paid['submission_record_id'], subs[1]['record_id'])
        self.assertEqual(paid['service_code'], '99214')
        self.assertEqual(rev['status'], 'reversed')
        self.assertEqual(rev['original_adjudication_record_id'], paid['record_id'])
        self.assertEqual(rev['financial_deltas_cents']['payer'], -6000)
        self.assertIsNone(rev['supersedes_record_id'])
        self.assertEqual((den['status'], den['carc'], den['allowed_cents']), ('denied', 'CO-16', 0))
        pra = {(r['claim_id'], r['adjudication_status']): r for r in by['provider_remittance_advice']}
        self.assertEqual(pra[('abc123', 'paid')]['status'], 'adjudicated')
        self.assertEqual(pra[('abc123', 'paid')]['original_adjudication_record_id'], paid['record_id'])
        self.assertEqual(pra[('abc123', 'paid')]['source_system'], 'WHN-PRACTICE-IN-0')
        self.assertEqual(pra[('zzz999', 'denied')]['status'], 'denied_information_needed')
        # the payer streams are untouched by the practice feed
        self.assertNotIn('payer_medical_claims', by)


class FidelityFixes(unittest.TestCase):
    def test_part_a_matches_837i_and_payer1_matches_cclf(self):
        L = Landing()
        L.c837('x12_payer/WH-PAYER-MSSP/2028-W30/MSSP_837I_2028-W30.x12', '1000', 'k1', 'WHN-CLM-inst1', '7Q45WP7HH60',
               tx='837I', charge=21998.64, statement_from='2028-07-06', statement_to='2028-07-13',
               lines=[(None, 21998.64, '2028-07-06')])
        L.c835('x12_payer/WH-PAYER-MSSP/2028-W33/MSSP_835_2028-W33.x12', '1001', '0001', 'e1', 'WHN-CLM-inst1', '1',
               7000.0, eff='2028-08-15', charge=21998.64)
        hf = 'cms_cclf_csv/parta_claims_header/P.A9999.ACO.ZC1Y28.D281012.T1.csv'
        common = dict(bene_mbi_id='7Q45WP7HH60', clm_type_cd='60', clm_from_dt='2028-07-06', clm_thru_dt='2028-07-13',
                      clm_bill_fac_type_cd='1', clm_bill_clsfctn_cd='1', clm_bill_freq_cd='1', clm_blg_prvdr_npi_num='1111111111',
                      clm_mdcr_instnl_tot_chrg_amt='21998.64', prncpl_dgns_cd='R5383', dgns_drg_cd='0948',
                      file_date='2028-10-12')
        # original, then a cancellation whose clm_org_cntl_num carries a different prefix but the same tail
        L.add('cclf_parta_claims_header', hf, cur_clm_uniq_id='5001', clm_adjsmt_type_cd='0', clm_pmt_amt='7533.04',
              clm_cntl_num='20280801DDFA4EB585', clm_efctv_dt='2028-08-01', clm_idr_ld_dt='2028-08-01', **common)
        L.add('cclf_parta_claims_header', hf, cur_clm_uniq_id='5002', clm_adjsmt_type_cd='1', clm_pmt_amt='-7533.04',
              clm_cntl_num='20280901DDFA4EB585', clm_org_cntl_num='20280715DDFA4EB585', clm_efctv_dt='2028-09-01',
              clm_idr_ld_dt='2028-09-01', **common)
        # WH-PAYER-1 Medicare FFS member (MBI) claim that CCLF Part B also carries
        L.c837('x12_payer/WH-PAYER-1/2027-W10/1_837P_2027-W10.x12', '1002', 'k1', 'WHN-PCLM-p1', '2DX0U77CP79',
               lines=[('99213', 120.0, '2027-03-02')])
        L.c835('x12_payer/WH-PAYER-1/2027-W12/1_835_2027-W12.x12', '1003', '0001', 'e1', 'WHN-PCLM-p1', '1', 50.0,
               eff='2027-03-20')
        L.add('cclf_partb_physicians', 'cms_cclf_csv/partb_physicians/P.A9999.ACO.ZC5Y27.D270412.T1.csv',
              cur_clm_uniq_id='6001', clm_line_num='1', bene_mbi_id='2DX0U77CP79', clm_type_cd='71', clm_from_dt='2027-03-02',
              clm_thru_dt='2027-03-02', clm_line_from_dt='2027-03-02', clm_line_hcpcs_cd='99213', clm_line_cvrd_pd_amt='70.64',
              clm_line_alowd_chrg_amt='88.30', clm_adjsmt_type_cd='0', clm_cntl_num='20270310AAAAAAAAAA',
              clm_efctv_dt='2027-03-10', clm_idr_ld_dt='2027-03-10', file_date='2027-04-12')
        by, rep = run(L)
        ids = {r['payer_claim_id'] for r in by['payer_medical_claims']}
        self.assertEqual(ids, {'WHN-CLM-inst1', 'WHN-PCLM-p1'})  # no CCLF-A / CCLF-B copies
        inst = claims(by, 'WHN-CLM-inst1')[0]
        self.assertEqual(inst['cclf_claim_id'], 'A-DDFA4EB585')  # cancel joined the original's chain by tail
        self.assertEqual(inst['ms_drg'] is not None, True)
        self.assertEqual(rep['dropped']['cclf_claim_matched_mssp_x12'], 1)
        p1 = claims(by, 'WHN-PCLM-p1')[0]
        self.assertEqual((p1['paid_amount'], p1['cclf_claim_id']), (50.0, 'B-AAAAAAAAAA'))  # own amounts, linked
        self.assertEqual(rep['dropped']['cclf_claim_matched_payer_x12'], 1)

    def test_same_day_reversal_is_final_and_recoupment_mapped(self):
        L = Landing()
        f8 = 'x12_payer/WH-PAYER-0/2026-W41/0_837P_2026-W41.x12'
        L.c837(f8, '1100', 'k1', 'WHN-PCLM-rr', '05414FCBFB', lines=[('99213', 200.0, '2026-10-01')])
        L.c835('x12_payer/WH-PAYER-0/2026-W42/0_835_2026-W42.x12', '1101', '0001', 'e1', 'WHN-PCLM-rr', '1', 0.0, 141.02,
               eff='2026-10-16')
        f47 = 'x12_payer/WH-PAYER-0/2026-W47/0_835_2026-W47.x12'
        L.c835(f47, '1102', '0001', 'e1', 'WHN-PCLM-rr', '22', 0.0, 141.02, eff='2026-11-20')
        L.c835(f47, '1102', '0001', 'e2', 'WHN-PCLM-rr', '1', 0.0, 131.46, eff='2026-11-20')
        # recoupment: the flat row keeps the original PAID_DATE and arrives in a later file; the 835 sends CLP02 22
        L.c837('x12_payer/WH-PAYER-C2/2028-W02/C2_837P_2028-W02.x12', '1200', 'k1', 'WHN-CLM-rc', 'C2MEMBER11',
               lines=[('99213', 60.0, '2028-01-05')])
        L.c835('x12_payer/WH-PAYER-C2/2028-W03/C2_835_2028-W03.x12', '1201', '0001', 'e1', 'WHN-CLM-rc', '1', 17.41, 4.52,
               eff='2028-01-21')
        L.c835('x12_payer/WH-PAYER-C2/2028-W11/C2_835_2028-W11.x12', '1202', '0001', 'e1', 'WHN-CLM-rc', '22', -17.41,
               4.52, eff='2028-03-17', cas=[('CO', '27', 17.41)])
        for f, st, paid in (('payers/WH-PAYER-C2/2028-01/C2_MEDICAL_20280205.csv', 'paid', '17.41'),
                            ('payers/WH-PAYER-C2/2028-03/C2_MEDICAL_20280405.csv', 'recouped', '-17.41')):
            L.add('payers_medical', f, claim_id='WHN-CLM-rc', claim_line='1', member_id='C2MEMBER11', claim_type='P',
                  paid_date='01/18/2028', claim_status=st, paid=paid, allowed='21.93', billed='60.0', frequency_code='1',
                  procedure_code='99213', network='IN', payer='C2')
        # a recoupment delivered before the payment it takes back
        L.c837('x12_payer/WH-PAYER-C2/2028-W05/C2_837P_2028-W05.x12', '1300', 'k1', 'WHN-CLM-early', 'C2MEMBER11',
               lines=[('99213', 90.0, '2028-02-01')])
        L.c835('x12_payer/WH-PAYER-C2/2028-W07/C2_835_2028-W07.x12', '1301', '0001', 'e1', 'WHN-CLM-early', '22', -66.66,
               eff='2028-02-18', cas=[('CO', '27', 66.66)])
        L.c835('x12_payer/WH-PAYER-C2/2028-W12/C2_835_2028-W12.x12', '1302', '0001', 'e1', 'WHN-CLM-early', '1', 66.66,
               eff='2028-03-24')
        by, rep = run(L)
        rr = claims(by, 'WHN-PCLM-rr')
        self.assertEqual([r['claim_status'] for r in rr], ['paid', 'paid', 'reversed'])
        self.assertEqual(rr[2]['supersedes_record_id'], rr[1]['record_id'])
        rc = claims(by, 'WHN-CLM-rc')
        self.assertEqual([r['claim_status'] for r in rc], ['paid', 'recouped'])  # one recoupment, not an extra flat event
        self.assertEqual(rc[1]['carc'], 'CO-27')
        early = claims(by, 'WHN-CLM-early')
        self.assertEqual([r['claim_status'] for r in early], ['paid', 'reversed'])
        self.assertEqual(rep['cross_check']['takeback_before_payment_reordered'], 1)


class EnrollmentFidelity(unittest.TestCase):
    def loop(self, L, payer, f, mt, member, begin, end=None, product='COMMERCIAL', fdate='2026-10-05', name=('KELLY', 'CYNTHIA')):
        L.add('x12_834_member', f'x12_834/{payer}/{fdate[:7]}/{f}', isa_control_number=f, file_type='full' if mt == '030' else 'change',
              file_date=fdate, maintenance_type_code=mt, member_id=member, subscriber_id=member[:-2] + '00',
              eligibility_begin_date=begin if mt != '024' else None, benefit_begin_date=begin, eligibility_end_date=end,
              plan_coverage_description=product, last_name=name[0], first_name=name[1], dob='1962-12-25')

    def test_payer_member_id_reissue_links_and_keeps_old_span(self):
        L = Landing()
        self.loop(L, 'WH-PAYER-C1', 'C1_834_FULL_20261005.x12', '030', 'C1OLDID0001', '2026-09-01')
        self.loop(L, 'WH-PAYER-C1', 'C1_834_CHG_20270705.x12', '024', 'C1OLDID0001', '2026-09-01', end='2027-06-14',
                  fdate='2027-07-05')
        self.loop(L, 'WH-PAYER-C1', 'C1_834_CHG_20270705b.x12', '021', 'C1NEWID00012', '2027-06-15', fdate='2027-07-05')
        # same name and DOB but overlapping coverage: a duplicate enrollment, not a re-issue
        self.loop(L, 'WH-PAYER-C1', 'C1_834_FULL_20261005b.x12', '030', 'C1DUPID0002', '2026-09-01')
        by, rep = run(L)
        cur = current(by['enrollment_spans'])
        # the pre-reissue span is kept (native definitions), the continuation only links to it
        self.assertEqual(sorted(r['member_id'] for r in cur), ['C1DUPID0002', 'C1NEWID00012', 'C1OLDID0001'])
        old = [r for r in cur if r['member_id'] == 'C1OLDID0001'][0]
        self.assertEqual((old['coverage_end'], old['termination_reason']), ('2027-06-14', 'member_id_reissued'))
        new = [r for r in cur if r['member_id'] == 'C1NEWID00012'][0]
        self.assertEqual((new['enrollment_reason'], new['previous_member_id'], new['member_id_reissued']),
                         ('member_id_reissued', 'C1OLDID0001', True))
        self.assertEqual(rep['cross_check']['payer_member_id_reissues_linked'], 1)

    def test_cclf_span_skipped_for_834_ffs_and_mbi_reissue(self):
        L = Landing()
        self.loop(L, 'WH-PAYER-1', '1_834_FULL_20261005.x12', '030', '2DX0U77CP79', '2026-08-28', product='MEDICARE_FFS')
        ef = 'cms_cclf_csv/enrollment/ENROLLMENT_20300527.csv'
        for m, start, end in (('2DX0U77CP79', '2026-08-28', ''), ('2H51Q97FQ99', '2026-09-01', '2028-06-16'),
                              ('2HBCAB84FC1F', '2028-06-17', '')):
            L.add('cclf_enrollment', ef, current_bene_mbi_id=m, enrollment_start_date=start, enrollment_end_date=end,
                  file_date='2030-05-27')
        bf = 'cms_cclf_csv/beneficiary_demographics/P.A9999.ACO.ZC8Y28.D280712.T1.csv'
        for m, b, e in (('2H51Q97FQ99', '2026-09-01', '2028-06-16'), ('2HBCAB84FC1', '2028-06-17', '2028-07-31')):
            L.add('cclf_beneficiary_demographics', bf, bene_mbi_id=m, bene_dob='1952-09-24', bene_1st_name='ANN',
                  bene_last_name='RIVERA', bene_dual_stus_cd='NA', bene_orgnl_entlmt_rsn_cd='0', bene_rng_bgn_dt=b,
                  bene_rng_end_dt=e, file_date='2028-07-12')
        L.add('cclf_beneficiary_xref', 'cms_cclf_csv/beneficiary_xref/P.A9999.ACO.ZC9Y28.D280712.T1.csv',
              crnt_num='2HBCAB84FC1', prvs_num='', prvs_id_obslt_dt='2028-06-17', file_date='2028-07-12')
        by, rep = run(L)
        cur = current(by['enrollment_spans'])
        mssp = [r for r in cur if r['payer_id'] == 'WH-PAYER-MSSP']
        self.assertEqual([(r['member_id'], r['enrollment_reason'], r['previous_member_id'], r['termination_reason'])
                          for r in sorted(mssp, key=lambda r: r['coverage_start'])],
                         [('2H51Q97FQ99', 'baseline', None, 'member_id_reissued'),
                          ('2HBCAB84FC1', 'member_id_reissued', '2H51Q97FQ99', None)])
        self.assertEqual(rep['dropped']['cclf_span_covered_by_834_medicare_ffs'], 1)
        self.assertEqual([r['payer_id'] for r in cur if r['member_id'] == '2DX0U77CP79'], ['WH-PAYER-1'])


class NativeDiagnosisFixes(unittest.TestCase):
    def test_ffs_attribution_practice_from_roster_and_deaths(self):
        L = Landing()
        ef = 'cms_cclf_csv/enrollment/ENROLLMENT_20300527.csv'
        L.add('cclf_enrollment', ef, current_bene_mbi_id='9AA0XQ0EY76', enrollment_start_date='2026-09-01',
              enrollment_end_date='2030-01-01', file_date='2030-05-27')
        for f, death in (('P.A9999.ACO.ZC8Y29.D291212.T1.csv', ''), ('P.A9999.ACO.ZC8Y30.D300112.T1.csv', '2030-01-01')):
            L.add('cclf_beneficiary_demographics', f'cms_cclf_csv/beneficiary_demographics/{f}', bene_mbi_id='9AA0XQ0EY76',
                  bene_dob='1940-01-01', bene_1st_name='AL', bene_last_name='GREEN', bene_dual_stus_cd='NA',
                  bene_orgnl_entlmt_rsn_cd='0', bene_death_dt=death, bene_rng_bgn_dt='2026-09-01',
                  bene_rng_end_dt='2030-01-01', file_date='20' + f.split('.D')[1][:6])
        L.add('programs_mssp_qalr', 'programs/mssp/2029Q4/P.A9999.ACO.QALR.2029Q4.csv', mbi='9AA0XQ0EY76',
              assigned_npi='1712816499', assigned_tin='335530888')
        L.add('rosters_aco_provider_roster', 'rosters/2029-12/ACO_PROVIDER_ROSTER_20291210.csv', group_npi='1712816499',
              provider_npi='1201884073', practice_id='WHN-PRACTICE-AK-0')
        by, rep = run(L)
        span = by['enrollment_spans'][0]
        self.assertEqual((span['attribution_npi'], span['attribution_practice_id'], span['termination_reason']),
                         ('1712816499', 'WHN-PRACTICE-AK-0', 'death'))
        d = by['death_records']
        self.assertEqual(len(d), 1)
        self.assertEqual((d[0]['date_of_death'], d[0]['member_id'], d[0]['patient_id'], d[0]['available_date']),
                         ('2030-01-01', '9AA0XQ0EY76', 'P-mbi:9AA0XQ0EY76', '2030-01-12'))

    def test_inpatient_discharge_from_statement_through(self):
        L = Landing()
        L.c837('x12_payer/WH-PAYER-C1/2028-W30/C1_837I_2028-W30.x12', '1400', 'k1', 'WHN-CLM-ip1', 'C1MEMBER01', tx='837I',
               charge=9000.0, statement_from='2028-07-06', statement_to='2028-07-13', admission_date='2028-07-06',
               admission_type='1', admission_source='7', patient_status='01', lines=[(None, 9000.0, '2028-07-06')])
        L.c835('x12_payer/WH-PAYER-C1/2028-W33/C1_835_2028-W33.x12', '1401', '0001', 'e1', 'WHN-CLM-ip1', '1', 6000.0,
               eff='2028-08-15', charge=9000.0)
        by, _ = run(L)
        c = claims(by, 'WHN-CLM-ip1')[0]
        self.assertEqual((c['type_of_bill'], c['discharge_date'], c['discharge_date_source']),
                         ('0111', '2028-07-13', 'statement_through'))
        self.assertEqual((c['admission_type'], c['admission_source'], c['patient_status'], c['discharge_status']),
                         ('1', '7', '01', 'home'))

    def test_pde_dedupe_with_12_char_cardholder(self):
        L = Landing()
        L.add('ncpdp_claim', 'ncpdp/WH-PAYER-MSSP/2028-07/MSSP_NCPDP_D0_20280806.txt', claim_ref='WHN-RXC-m1',
              file_date='20280806', bin_number='990007', transaction_code='B1', pcn='PARTD', service_provider_id_qualifier='01',
              service_provider_id='1181918479', date_of_service='2028-07-19', cardholder_id='8QCA20817D15', group_id='PDP01',
              rx_service_ref_number='7329015879', product_service_id_qualifier='03', product_service_id='00093092806',
              quantity_dispensed=30, fill_number=0, days_supply=30, response_status_code='P', total_amount_paid=5.0,
              patient_pay_amount=10.0)
        L.add('cclf_partd_claims', 'cms_cclf_csv/partd_claims/P.A9999.ACO.ZC7Y28.D280812.T1.csv', cur_clm_uniq_id='77',
              bene_mbi_id='8QCA20817D1', clm_line_ndc_cd='00093092806', clm_line_from_dt='2028-07-19',
              prvdr_srvc_id_qlfyr_cd='01', clm_srvc_prvdr_gnrc_id_num='1181918479', clm_line_days_suply_qty='30',
              clm_line_bene_pmt_amt='10.00', clm_adjsmt_type_cd='0', clm_line_rx_srvc_rfrnc_num='7329015879',
              clm_line_rx_fill_num='0', clm_phrmcy_srvc_type_cd='01', file_date='2028-08-12')
        by, rep = run(L)
        self.assertEqual([r['rx_claim_id'] for r in by['payer_pharmacy_claims']], ['WHN-RXC-m1'])
        self.assertEqual(rep['dropped']['cclf_pde_matched_mssp_ncpdp'], 1)
        self.assertEqual(by['payer_pharmacy_claims'][0]['pharmacy_type'], 'retail_pharmacy')


def current(recs):
    gone = {r['supersedes_record_id'] for r in recs if r['supersedes_record_id']}
    return [r for r in recs if r['record_id'] not in gone]


if __name__ == '__main__':
    unittest.main()
