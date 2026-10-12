WITH copd AS MATERIALIZED (
 SELECT DISTINCT patient FROM records WHERE stream='conditions' AND (
 substr(json_extract(payload,'$.icd10'),1,3)='J44' OR json_extract(payload,'$.snomed') IN ('185086009','87433001'))
) SELECT COUNT(*) AS count FROM copd c JOIN patients p ON p.id=c.patient
 WHERE json_type(p.payload,'$.age_reported')='integer' AND json_extract(p.payload,'$.age_reported')>=65;
