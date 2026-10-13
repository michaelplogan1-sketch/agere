-- Distinct inpatient encounter IDs (encounter type 'inpatient', any status) by calendar quarter of source event date, 2026-2029.
SELECT substr(event,1,4) || '-Q' || ((CAST(substr(event,6,2) AS INTEGER)+2)/3) AS quarter,
       COUNT(DISTINCT json_extract(payload,'$.encounter_id')) AS inpatient_stays
FROM records WHERE stream='encounters' AND lower(json_extract(payload,'$.type'))='inpatient'
  AND event BETWEEN '2026-01-01' AND '2029-08-15'
GROUP BY 1 ORDER BY 1;
