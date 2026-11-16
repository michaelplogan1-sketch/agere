"""Pin explicitly inspected public source schemas as literal documentary accounts.

No clinical rules, hidden truth or inferred patient outcomes are created here.
Reference target types are observed transport contracts; absent future types are
unresolved until the adapter is updated deliberately.
"""
from collections import defaultdict
from pathlib import Path
import json,sqlite3

DOMAINS={
 'access':'care_management_contacts care_support_interviews community_service_outcomes community_service_referrals procedure_access_contacts',
 'medication':'facility_medication_supply home_medication_questions inpatient_discharge_reconciliation inpatient_medication_histories pharmacy_prescriber_responses pharmacy_renewal_requests prescription_reassessment_decisions prescription_request_responses received_prescription_requests',
 'care':'functional_mobility_exams home_health_assessments home_health_case_summaries home_health_intake home_health_orders home_health_plan_responses home_health_plans home_health_referral_packets home_health_treatments hospital_care_transitions hospital_fluid_balance hospital_followup_assessments hospital_illness_assessments inpatient_cardiac_reports inpatient_fluid_charts inpatient_volume_assessments received_home_health_documents received_hospital_summaries transition_of_care_contacts',
 'procedure':'endoscopy_reports pathology_accessions pathology_reports pathology_specimens preprocedure_assessments procedure_orders procedure_preparation_contacts procedure_recovery_notes procedure_referral_packets procedure_result_communications received_procedure_reports',
 'finance':'home_health_billing_periods'}
NARRATIVE_FIELDS={'recommendation','homebound_basis','requested_services','initial_frequency','home_medication_plan','working_diagnosis','directions','directions_on_file','prep_instructions','medication_instruction','decision_reason','clinical_question','gross_description','microscopic_description','diagnosis','content','goals','abdominal_exam','lung_exam','perfusion','patient_response','help_available_reported','housing_safety_concern_reported'}
CLOCKS={'endoscopy_reports':['procedure_start'],'inpatient_cardiac_reports':['acquired_time'],'pathology_specimens':['collection_time'],'pathology_accessions':['collection_time']}


def main():
 root=Path('artifacts/agere-warehouse-v123-inventory');inventory=json.loads((root/'inventory.json').read_text());source=Path(inventory['release'])/'warehouse.sqlite';db=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)
 domains={name:domain for domain,names in DOMAINS.items() for name in names.split()};assert set(domains)==set(inventory['new_source_categories'])
 identity={rid:(pid,table) for rid,pid,table in db.execute('SELECT record_id,patient_id,source_table FROM source_records')};links=defaultdict(lambda:defaultdict(set));issues=[]
 try:
  for table in sorted(domains):
   for rid,pid,raw in db.execute('SELECT record_id,patient_id,facts FROM source_records WHERE source_table=?',(table,)):
    facts=json.loads(raw)
    for field,value in facts.items():
     if not (field.endswith('_record_id') or field.endswith('_record_ids')):continue
     links[table][field]  # Preserve declared nullable references, even if no target was received.
     if value is None:continue
     for ref in value if isinstance(value,list) else [value]:
      if not isinstance(ref,str) or ref not in identity:issues.append({'record_id':rid,'field':field,'reason':'unresolved_received_reference'});continue
      target_patient,target_type=identity[ref]
      if target_patient!=pid:issues.append({'record_id':rid,'field':field,'reason':'cross_patient_reference'});continue
      links[table][field].add(target_type)
  definitions={table:{'domain':domain,'relation':table+'_account','fields':sorted(inventory['new_source_fields'][table]),
    'links':{key:sorted(values) for key,values in sorted(links[table].items())},
    'narrative_fields':sorted(set(inventory['new_source_fields'][table])&NARRATIVE_FIELDS),
    'explicit_clinical_clock_fields':CLOCKS.get(table,[])} for table,domain in sorted(domains.items())}
  package={'schema':'received-source-catalog/1','synthetic':True,'basis':'Literal fields and same-patient reference types inspected in public received records; no clinical equivalence or target values.',
   'source_manifest_sha256':inventory['manifest_sha256'],'source_warehouse_sha256':inventory['source_sha256'],'definitions':definitions,
   'limits':['New source accounts preserve literal reported values; no eligibility, care completion or ingestion inference.','Nested source packets remain received copies; nested references need separate qualification.','Reference types are pinned from inspected public transport; novel target types fail explicitly.']}
  out=Path('config/agere/received-source-catalog-v123.json');out.write_text(json.dumps(package,indent=2)+'\n')
  (root/'reference-inventory.json').write_text(json.dumps({'public_reference_type_bindings':sum(len(v) for v in links.values()),'source_catalog':str(out),'unresolved_reference_examples':issues[:30],'unresolved_references':len(issues),'private_files_read':False},indent=2)+'\n')
  print(json.dumps({'source_categories':len(definitions),'link_fields':sum(len(v) for v in links.values()),'unresolved_received_references':len(issues),'config':str(out)}))
 finally:db.close()

if __name__=='__main__':main()
