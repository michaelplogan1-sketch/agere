WITH hf AS MATERIALIZED (
 SELECT DISTINCT patient FROM records WHERE stream='conditions' AND (
 substr(json_extract(payload,'$.icd10'),1,3)='I50' OR json_extract(payload,'$.snomed')='88805009')
), bnp AS MATERIALIZED (
 SELECT patient,json_extract(payload,'$.value') AS v FROM records WHERE stream='labs'
  AND json_extract(payload,'$.name')='NT-proBNP' AND json_extract(payload,'$.unit')='pg/mL'
  AND json_type(payload,'$.value') IN ('integer','real')
) SELECT COUNT(DISTINCT hf.patient) AS count FROM hf JOIN bnp USING(patient) WHERE bnp.v>=1000;
