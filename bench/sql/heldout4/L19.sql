-- ED visits per 1,000 attributed patients by PCP practice, trailing 12 months.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('encounters','care_team_relationships')), ed AS (SELECT patient, json_extract(payload,'$.encounter_id') eid, min(json_extract(payload,'$.event_date')) d FROM r WHERE stream='encounters' AND json_extract(payload,'$.type') IN ('emergency','ED','ED stroke alert') AND json_extract(payload,'$.event_date') IS NOT NULL GROUP BY patient, eid), pcp AS (SELECT patient, json_extract(payload,'$.organization_id') practice, json_extract(payload,'$.clinician_id') clin FROM r WHERE stream='care_team_relationships' AND json_extract(payload,'$.role') IN ('primary_care','pediatric_primary_care')),
v AS (SELECT patient, COUNT(*) n FROM ed WHERE d BETWEEN '2028-08-16' AND '2029-08-15' GROUP BY patient)
SELECT pcp.practice AS practice, COUNT(*) AS attributed, coalesce(SUM(v.n),0) AS ed_visits,
       round(1000.0*coalesce(SUM(v.n),0)/COUNT(*),1) AS ed_per_1000
FROM pcp LEFT JOIN v ON v.patient=pcp.patient GROUP BY pcp.practice ORDER BY practice;
