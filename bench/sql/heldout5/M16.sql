-- ED visits per 1,000 roster members by coverage type, trailing 12 months 2028-08-16..2029-08-15. Numerator: distinct ED encounter_ids (type emergency/ED/ED stroke alert) dated in window. Denominator: ALL roster patients with that coverage_type in the coverage stream (commercial/medicaid/medicare/uninsured), visit or not. per_1000 = visits*1000/members rounded to 1 decimal.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('encounters','coverage')),
cov AS (SELECT patient, json_extract(payload,'$.coverage_type') payer FROM r WHERE stream='coverage'),
ed AS (SELECT patient, json_extract(payload,'$.encounter_id') eid, min(json_extract(payload,'$.event_date')) d FROM r WHERE stream='encounters' AND json_extract(payload,'$.type') IN ('emergency','ED','ED stroke alert') GROUP BY 1,2),
v AS (SELECT patient, COUNT(*) n FROM ed WHERE d BETWEEN '2028-08-16' AND '2029-08-15' GROUP BY patient)
SELECT cov.payer AS coverage_type, COALESCE(SUM(v.n),0) AS ed_visits, COUNT(*) AS members, ROUND(COALESCE(SUM(v.n),0)*1000.0/COUNT(*),1) AS per_1000 FROM cov LEFT JOIN v USING(patient) GROUP BY cov.payer ORDER BY cov.payer;
