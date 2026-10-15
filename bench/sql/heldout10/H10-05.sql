WITH lab_sup AS (SELECT supersedes AS id FROM records WHERE stream='labs' AND supersedes IS NOT NULL
              UNION SELECT json_extract(payload,'$.replaces_result_record_id') FROM records WHERE stream='labs' AND json_extract(payload,'$.replaces_result_record_id') IS NOT NULL),
k AS (
  SELECT r.patient, r.event AS d, CAST(json_extract(r.payload,'$.value') AS REAL) AS v
  FROM records r
  WHERE r.stream='labs' AND r.event IS NOT NULL AND (lower(json_extract(r.payload,'$.name'))='potassium' OR json_extract(r.payload,'$.loinc')='2823-3')
    AND typeof(json_extract(r.payload,'$.value')) IN ('integer','real')
    AND COALESCE(json_extract(r.payload,'$.result_status'),'') <> 'cancelled'
    AND r.id NOT IN (SELECT id FROM lab_sup)),
w AS (SELECT * FROM k WHERE d BETWEEN '2030-01-01' AND '2030-05-27'),
last AS (SELECT w.patient, max(w.v) v FROM w JOIN (SELECT patient, max(d) d FROM w GROUP BY patient) l ON l.patient=w.patient AND l.d=w.d GROUP BY w.patient)
SELECT patient AS patient_id FROM last WHERE v > 5.0 ORDER BY 1
