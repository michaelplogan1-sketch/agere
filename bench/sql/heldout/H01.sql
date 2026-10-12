WITH htn AS MATERIALIZED (
 SELECT DISTINCT patient FROM records WHERE stream='conditions' AND (
 substr(json_extract(payload,'$.icd10'),1,3)='I10' OR json_extract(payload,'$.snomed')='59621000')
), sbp AS MATERIALIZED (
 SELECT patient,json_extract(payload,'$.value') AS v FROM records WHERE stream='vitals'
  AND json_extract(payload,'$.measurement')='bp_systolic' AND json_extract(payload,'$.unit')='mmHg'
  AND json_type(payload,'$.value') IN ('integer','real')
 UNION ALL
 SELECT patient,json_extract(payload,'$.blood_pressure_systolic') FROM records WHERE stream='vitals'
  AND json_type(payload,'$.blood_pressure_systolic') IN ('integer','real')
  AND json_extract(payload,'$.units.blood_pressure_systolic')='mmHg'
) SELECT COUNT(DISTINCT htn.patient) AS count FROM htn JOIN sbp USING(patient) WHERE sbp.v>=160;
