WITH lab_sup AS (SELECT supersedes AS id FROM records WHERE stream='labs' AND supersedes IS NOT NULL
              UNION SELECT json_extract(payload,'$.replaces_result_record_id') FROM records WHERE stream='labs' AND json_extract(payload,'$.replaces_result_record_id') IS NOT NULL),
a1c AS (
  SELECT r.patient, r.event AS d, CAST(json_extract(r.payload,'$.value') AS REAL) AS v
  FROM records r
  WHERE r.stream='labs' AND r.event IS NOT NULL AND (lower(json_extract(r.payload,'$.name')) IN ('hba1c','hemoglobin a1c','hemoglobin a1c/hemoglobin.total') OR json_extract(r.payload,'$.loinc')='4548-4')
    AND typeof(json_extract(r.payload,'$.value')) IN ('integer','real')
    AND COALESCE(json_extract(r.payload,'$.result_status'),'') <> 'cancelled'
    AND r.id NOT IN (SELECT id FROM lab_sup)),
dm AS (
  SELECT DISTINCT patient FROM (
    SELECT patient, upper(replace(json_extract(payload,'$.icd10'),'.','')) AS c FROM records WHERE stream='conditions'
    UNION ALL SELECT r.patient, upper(replace(json_extract(d.value,'$.icd10'),'.','')) FROM records r, json_each(r.payload,'$.diagnoses') d WHERE r.stream='encounters'
    UNION ALL SELECT r.patient, upper(replace(d.value,'.','')) FROM records r, json_each(r.payload,'$.diagnosis_codes') d WHERE r.stream IN ('payer_medical_claims','medical_claims')
  ) WHERE substr(c,1,3) IN ('E10','E11')),
y AS (SELECT * FROM a1c WHERE d BETWEEN '2029-01-01' AND '2029-12-31' AND patient IN (SELECT patient FROM dm)),
last AS (SELECT y.patient, max(y.v) AS v FROM y JOIN (SELECT patient, max(d) d FROM y GROUP BY patient) l ON l.patient=y.patient AND l.d=y.d GROUP BY y.patient)
SELECT round(100.0 * sum(v < 8.0) / count(*), 2) AS value FROM last
