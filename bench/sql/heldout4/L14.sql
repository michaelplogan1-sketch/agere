-- ED visits per 1,000 by coverage type, trailing 12 months; denominator = roster per coverage type.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('encounters','coverage')), ed AS (SELECT patient, json_extract(payload,'$.encounter_id') eid, min(json_extract(payload,'$.event_date')) d FROM r WHERE stream='encounters' AND json_extract(payload,'$.type') IN ('emergency','ED','ED stroke alert') AND json_extract(payload,'$.event_date') IS NOT NULL GROUP BY patient, eid), cov AS (SELECT patient, json_extract(payload,'$.coverage_type') ctype, json_extract(payload,'$.organization_id') payer FROM r WHERE stream='coverage'),
v AS (SELECT patient, COUNT(*) n FROM ed WHERE d BETWEEN '2028-08-16' AND '2029-08-15' GROUP BY patient)
SELECT cov.ctype AS coverage_type, COUNT(*) AS members, coalesce(SUM(v.n),0) AS ed_visits,
       round(1000.0*coalesce(SUM(v.n),0)/COUNT(*),1) AS ed_per_1000
FROM cov LEFT JOIN v ON v.patient=cov.patient GROUP BY cov.ctype ORDER BY coverage_type;
