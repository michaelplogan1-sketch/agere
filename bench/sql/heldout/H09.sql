WITH htn AS MATERIALIZED (
 SELECT DISTINCT patient FROM records WHERE stream='conditions' AND (
 substr(json_extract(payload,'$.icd10'),1,3)='I10' OR json_extract(payload,'$.snomed')='59621000')
), ldl AS MATERIALIZED (
 SELECT patient,json_extract(payload,'$.value') AS v FROM records WHERE stream='labs'
  AND json_extract(payload,'$.name') IN ('LDL cholesterol','LDL') AND json_extract(payload,'$.unit')='mg/dL'
  AND json_type(payload,'$.value') IN ('integer','real')
) SELECT DISTINCT htn.patient AS patient_id FROM htn JOIN ldl USING(patient) WHERE ldl.v>=190 ORDER BY patient_id;
