-- 2029 year-to-date ED visits (types 'emergency','ED','ED stroke alert'; distinct encounter_id; event_date in 2029 through as-of)
-- grouped by the patient's attributed practice = organization_id of the patient's assigned primary care relationship
-- (care_team_relationships role 'primary_care' or 'pediatric_primary_care', status 'assigned'). Practices with zero visits are omitted.
WITH e AS (SELECT patient, json_extract(payload,'$.encounter_id') id, MIN(json_extract(payload,'$.type')) t, MIN(json_extract(payload,'$.event_date')) d
  FROM records WHERE stream='encounters' GROUP BY patient, json_extract(payload,'$.encounter_id')),
pcp AS (SELECT patient, json_extract(payload,'$.organization_id') practice FROM records WHERE stream='care_team_relationships'
        AND json_extract(payload,'$.role') IN ('primary_care','pediatric_primary_care') AND json_extract(payload,'$.status')='assigned')
SELECT pcp.practice, COUNT(*) AS ed_visits FROM e JOIN pcp ON pcp.patient=e.patient
WHERE e.t IN ('emergency','ED','ED stroke alert') AND e.d BETWEEN '2029-01-01' AND '2029-12-31'
GROUP BY 1 ORDER BY 1;
