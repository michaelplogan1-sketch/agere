-- Medicare annual wellness visits (AWV, HCPCS G0438) completed in 2029: distinct patients with a wellness_visit_services row visit_type 'medicare_annual_wellness', status 'completed', event_date 2029-01-01..2029-12-31 (generic 'preventive_review' visits are not AWVs).
SELECT COUNT(DISTINCT patient) AS count FROM records WHERE stream='wellness_visit_services' AND json_extract(payload,'$.visit_type')='medicare_annual_wellness'
  AND json_extract(payload,'$.status')='completed' AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31';
