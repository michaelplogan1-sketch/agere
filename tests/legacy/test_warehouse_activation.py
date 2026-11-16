import copy,unittest
from unittest.mock import patch
from clinical_core.agere.warehouse_activation import validate_receipts,validate_extension


def fixture():
    source={'as_of':'2029-02-15','release':'fixture'}
    c={'status':'connected','synthetic':True,'snapshot':source,'patients':2,'records':10,'source_categories':3}
    base={'synthetic':True,'source_snapshot':source,'patients':2,'qualification_code_identity':'code','new_paid_api_calls':0}
    g={**base,'original_patient_ids_match':True,'received_records':10,'searchable_records':10,'source_categories':3,'frontier_request_bytes_1500_char_question':19000}
    n={**base,'frontier_request_bytes_1500_ascii_question':19000,'results':[
        {'contract':x,'public_source_patient_sets_match':True} for x in ('native_affordability_report','native_medication_nonuse','native_outside_service','native_advance_document_review')]}
    d={**base,'protocol_positive_measurement_population_matches_public_fields':True}
    return c,g,n,d


class WarehouseActivation(unittest.TestCase):
    def test_matching_verified_received_build_is_eligible(self):
        self.assertTrue(validate_receipts(*fixture(),current_code='code'))

    def test_wrong_snapshot_or_stale_code_cannot_activate(self):
        for i in (1,2,3):
            for key,value in [('source_snapshot',{'release':'other'}),('qualification_code_identity','old')]:
                rows=copy.deepcopy(fixture());rows[i][key]=value
                with self.assertRaises(ValueError):validate_receipts(*rows,current_code='code')

    def test_partial_source_coverage_and_wrong_roster_rejected(self):
        for key,value in [('received_records',9),('searchable_records',9),('patients',3),('original_patient_ids_match',False)]:
            rows=fixture();rows[1][key]=value
            with self.assertRaises(ValueError):validate_receipts(*rows,current_code='code')

    def test_missing_contract_or_diagnostic_checks_rejected(self):
        rows=fixture();rows[2]['results'].pop()
        with self.assertRaises(ValueError):validate_receipts(*rows,current_code='code')
        rows=fixture();rows[3]['protocol_positive_measurement_population_matches_public_fields']=False
        with self.assertRaises(ValueError):validate_receipts(*rows,current_code='code')

    def test_missing_or_invalid_request_measurements_cannot_activate(self):
        for i,key in ((1,'frontier_request_bytes_1500_char_question'),(2,'frontier_request_bytes_1500_ascii_question')):
            for value in (None,True,0,-1,19000.0,'19000'):
                rows=fixture();rows[i][key]=value
                with self.assertRaises(ValueError):validate_receipts(*rows,current_code='code')

    def test_unlimited_policy_accepts_measured_requests_without_imposing_a_cap(self):
        rows=fixture()
        rows[1]['frontier_request_bytes_1500_char_question']=1000000
        rows[2]['frontier_request_bytes_1500_ascii_question']=2000000
        with patch('clinical_core.agere.warehouse_activation.REQUEST_MAX_BYTES',None):
            self.assertTrue(validate_receipts(*rows,current_code='code'))

    def test_explicit_finite_policy_enforces_both_receipts_and_includes_boundary(self):
        with patch('clinical_core.agere.warehouse_activation.REQUEST_MAX_BYTES',19000):
            self.assertTrue(validate_receipts(*fixture(),current_code='code'))
            for i,key in ((1,'frontier_request_bytes_1500_char_question'),(2,'frontier_request_bytes_1500_ascii_question')):
                rows=fixture();rows[i][key]=19001
                with self.assertRaises(ValueError):validate_receipts(*rows,current_code='code')

    def test_source_extension_requires_full_current_contract_qualification(self):
        c=fixture()[0];c['source_coverage']={'new_table':4}
        r={'passed':True,'synthetic':True,'source_snapshot':c['snapshot'],'patients':2,'qualification_code_identity':'code',
           'source_catalog_sha256':'catalog','new_categories':['new_table'],'new_paid_api_calls':0,'checks':{'source_accounts':4},
           'contracts':[{'contract':k,'patients':2,'public_source_sets_match':True} for k in ('prescriber_reassessment_request','renewal_clarification_deferral','transport_support_report','community_service_delivery_report')],
           'prior_reviews_copied':0,'prior_patient_receipts_copied':0}
        args={'current_code':'code','catalog_hash':'catalog','categories':['new_table']}
        self.assertTrue(validate_extension(c,r,**args))
        for key,value in [('source_catalog_sha256','old'),('checks',{'source_accounts':3}),('contracts',[]),('prior_reviews_copied',1)]:
            with self.assertRaises(ValueError):validate_extension(c,{**r,key:value},**args)


if __name__=='__main__':unittest.main()
