WITH asthma AS MATERIALIZED (
 SELECT DISTINCT patient FROM records WHERE stream='conditions' AND (
 substr(json_extract(payload,'$.icd10'),1,3)='J45' OR json_extract(payload,'$.snomed') IN ('195967001','233678006'))
) SELECT COUNT(*) AS count FROM asthma a JOIN patients p ON p.id=a.patient
 WHERE json_type(p.payload,'$.age_reported')='integer' AND json_extract(p.payload,'$.age_reported')<18;
