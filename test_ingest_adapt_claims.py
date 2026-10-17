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
        self.assertEqual(rep['dropped']['provider_side_837_copy'], 1)
        self.assertEqual(rep['dropped']['provider_side_835_copy'], 1)
        self.assertGreaterEqual(rep['dropped']['resend_file'], 1)

    def test_duplicate_dropped_and_crossover_kept(self):
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
        ids = {r['payer_claim_id'] for r in by['payer_medical_claims']}
        self.assertNotIn('WHN-CLM-eee-D', ids)
        self.assertEqual(rep['dropped']['duplicate_submission_loop_837'], 1)
        self.assertEqual(rep['dropped']['duplicate_submission_835'], 1)
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
        self.tx(L, f2, 'WHN-RXC-111', 'B2', 'A', '2026-09-11', paid=0.0, pay=0.0)
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
        self.assertEqual(rev['supersedes_record_id'], paid['record_id'])
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


if __name__ == '__main__':
    unittest.main()
