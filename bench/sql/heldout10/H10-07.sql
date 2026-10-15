WITH lab_sup AS (SELECT supersedes AS id FROM records WHERE stream='labs' AND supersedes IS NOT NULL
              UNION SELECT json_extract(payload,'$.replaces_result_record_id') FROM records WHERE stream='labs' AND json_extract(payload,'$.replaces_result_record_id') IS NOT NULL),
a1c AS (
  SELECT r.patient, r.event AS d, CAST(json_extract(r.payload,'$.value') AS REAL) AS v
  FROM records r
  WHERE r.stream='labs' AND r.event IS NOT NULL AND (lower(json_extract(r.payload,'$.name')) IN ('hba1c','hemoglobin a1c','hemoglobin a1c/hemoglobin.total') OR json_extract(r.payload,'$.loinc')='4548-4')
    AND typeof(json_extract(r.payload,'$.value')) IN ('integer','real')
    AND COALESCE(json_extract(r.payload,'$.result_status'),'') <> 'cancelled'
    AND r.id NOT IN (SELECT id FROM lab_sup)),
y AS (SELECT patient, d, max(v) v FROM a1c WHERE d BETWEEN '2029-01-01' AND '2029-12-31' GROUP BY patient, d),
b AS (SELECT patient, min(d) f, max(d) l FROM y GROUP BY patient HAVING count(*) >= 2)
SELECT COUNT(*) AS count FROM b JOIN y f ON f.patient=b.patient AND f.d=b.f JOIN y l ON l.patient=b.patient AND l.d=b.l WHERE f.v - l.v >= 1.0
