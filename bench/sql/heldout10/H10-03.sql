WITH lab_sup AS (SELECT supersedes AS id FROM records WHERE stream='labs' AND supersedes IS NOT NULL
              UNION SELECT json_extract(payload,'$.replaces_result_record_id') FROM records WHERE stream='labs' AND json_extract(payload,'$.replaces_result_record_id') IS NOT NULL),
g AS (
  SELECT r.patient, r.event AS d, CAST(json_extract(r.payload,'$.value') AS REAL) AS v
  FROM records r
  WHERE r.stream='labs' AND r.event IS NOT NULL AND (lower(json_extract(r.payload,'$.name')) IN ('egfr','egfr ckd-epi 2021','glomerular filtration rate') OR json_extract(r.payload,'$.loinc')='98979-8')
    AND typeof(json_extract(r.payload,'$.value')) IN ('integer','real')
    AND COALESCE(json_extract(r.payload,'$.result_status'),'') <> 'cancelled'
    AND r.id NOT IN (SELECT id FROM lab_sup))
SELECT COUNT(*) AS count FROM (SELECT patient FROM g WHERE v < 60 GROUP BY patient HAVING julianday(max(d)) - julianday(min(d)) >= 90)
