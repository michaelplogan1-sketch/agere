-- Problem-list diabetes only (conditions E10/E11/E13), case-insensitive A1c names; latest 2029 A1c > 9.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('conditions','labs')),
dm AS MATERIALIZED (SELECT DISTINCT patient FROM r WHERE stream='conditions' AND substr(json_extract(payload,'$.icd10'),1,3) IN ('E10','E11','E13')),
a AS (SELECT patient, json_extract(payload,'$.value') v, row_number() OVER (PARTITION BY patient ORDER BY json_extract(payload,'$.event_date') DESC, json_extract(payload,'$.event_time') DESC, json_extract(payload,'$.record_id') DESC) rn
  FROM r WHERE stream='labs' AND (lower(json_extract(payload,'$.name')) IN ('hba1c','hemoglobin a1c','hemoglobin a1c/hemoglobin.total') OR json_extract(payload,'$.loinc')='4548-4') AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31' AND json_type(payload,'$.value') IN ('integer','real'))
SELECT COUNT(*) AS count FROM a WHERE rn=1 AND v>9 AND patient IN (SELECT patient FROM dm);
