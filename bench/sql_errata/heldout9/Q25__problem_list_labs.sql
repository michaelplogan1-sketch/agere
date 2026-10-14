-- Problem-list diabetics with no dated numeric 2029 A1c lab (case-insensitive names); claims not used.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('conditions','labs')),
dm AS MATERIALIZED (SELECT DISTINCT patient FROM r WHERE stream='conditions' AND substr(json_extract(payload,'$.icd10'),1,3) IN ('E10','E11','E13')),
a AS (SELECT patient FROM r WHERE stream='labs' AND (lower(json_extract(payload,'$.name')) IN ('hba1c','hemoglobin a1c','hemoglobin a1c/hemoglobin.total') OR json_extract(payload,'$.loinc')='4548-4') AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31' AND json_type(payload,'$.value') IN ('integer','real'))
SELECT patient AS patient_id FROM dm WHERE patient NOT IN (SELECT patient FROM a) ORDER BY 1;
