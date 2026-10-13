-- Urgent care visits in calendar 2026 by the patient's coverage type (single roster coverage record: commercial / medicare / medicaid / uninsured).
-- Urgent care types: 'urgent_care' and 'urgentcare'. One visit = one distinct encounter_id; dated by event_date.
WITH e AS (SELECT patient, json_extract(payload,'$.encounter_id') id, MIN(json_extract(payload,'$.type')) t, MIN(json_extract(payload,'$.event_date')) d
  FROM records WHERE stream='encounters' GROUP BY patient, json_extract(payload,'$.encounter_id')),
cov AS (SELECT patient, json_extract(payload,'$.coverage_type') coverage_type FROM records WHERE stream='coverage')
SELECT cov.coverage_type, COUNT(*) AS visits FROM e JOIN cov ON cov.patient=e.patient
WHERE e.t IN ('urgent_care','urgentcare') AND e.d BETWEEN '2026-01-01' AND '2026-12-31'
GROUP BY 1 ORDER BY 1;
