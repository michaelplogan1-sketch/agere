-- HF patients (88805009 / I50.* / 'heart failure' / 'chf') with no beta-blocker in any medication stream.
WITH c AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='conditions'),
hf AS (SELECT DISTINCT patient FROM c WHERE coalesce(json_extract(payload,'$.snomed'),'')='88805009' OR coalesce(json_extract(payload,'$.icd10'),'') LIKE 'I50%'
       OR lower(coalesce(json_extract(payload,'$.description'),'')) LIKE '%heart failure%' OR lower(coalesce(json_extract(payload,'$.description'),'')) LIKE '%chf%'),
m AS (SELECT patient, lower(coalesce(json_extract(payload,'$.medication_name'), json_extract(payload,'$.name'),'')) n FROM records
      WHERE stream IN ('medication_lists','medication_orders','pharmacy_dispenses','pharmacy_claims','pharmacy_history') AND patient IN (SELECT patient FROM hf)),
bb AS (SELECT DISTINCT patient FROM m WHERE (n LIKE '%metoprolol%' OR n LIKE '%carvedilol%' OR n LIKE '%bisoprolol%' OR n LIKE '%atenolol%' OR n LIKE '%propranolol%' OR n LIKE '%nebivolol%' OR n LIKE '%labetalol%' OR n LIKE '%toprol%'))
SELECT patient AS patient_id FROM hf WHERE patient NOT IN (SELECT patient FROM bb) ORDER BY 1;
