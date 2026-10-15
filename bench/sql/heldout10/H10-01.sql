WITH lab_sup AS (SELECT supersedes AS id FROM records WHERE stream='labs' AND supersedes IS NOT NULL
              UNION SELECT json_extract(payload,'$.replaces_result_record_id') FROM records WHERE stream='labs' AND json_extract(payload,'$.replaces_result_record_id') IS NOT NULL),
a1c AS (
  SELECT r.patient, r.event AS d, CAST(json_extract(r.payload,'$.value') AS REAL) AS v
  FROM records r
  WHERE r.stream='labs' AND r.event IS NOT NULL AND (lower(json_extract(r.payload,'$.name')) IN ('hba1c','hemoglobin a1c','hemoglobin a1c/hemoglobin.total') OR json_extract(r.payload,'$.loinc')='4548-4')
    AND typeof(json_extract(r.payload,'$.value')) IN ('integer','real')
    AND COALESCE(json_extract(r.payload,'$.result_status'),'') <> 'cancelled'
    AND r.id NOT IN (SELECT id FROM lab_sup)),
last AS (SELECT patient, max(d) AS d FROM a1c GROUP BY patient)
SELECT COUNT(*) AS count FROM (
  SELECT a.patient, max(a.v) AS v FROM a1c a JOIN last l ON l.patient=a.patient AND l.d=a.d GROUP BY a.patient) WHERE v > 9.0
