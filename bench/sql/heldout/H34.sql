WITH htn AS MATERIALIZED (
 SELECT DISTINCT patient FROM records WHERE stream='conditions' AND (
 substr(json_extract(payload,'$.icd10'),1,3)='I10' OR json_extract(payload,'$.snomed')='59621000')
), bills AS MATERIALIZED (
 SELECT DISTINCT patient FROM records WHERE stream='financial_screens' AND json_type(payload,'$.difficulty_paying_medical_bills')='true'
) SELECT patient AS patient_id FROM htn WHERE patient IN (SELECT patient FROM bills) ORDER BY patient_id;
