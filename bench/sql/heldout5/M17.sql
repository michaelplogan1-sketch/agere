-- Percent of 2029 ED visits (distinct ED encounter_ids, type emergency/ED/ED stroke alert, dated 2029-01-01..2029-08-15) followed by an outpatient contact 1-7 days after the ED date: any encounter of type primary_care, office, pediatric_office, wellness, specialist_office, specialty_followup or telephone. Same-day contacts, other ED/urgent care/observation/inpatient do not count. Rounded to 1 decimal.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='encounters'),
e AS (SELECT patient, json_extract(payload,'$.encounter_id') eid, json_extract(payload,'$.type') t, min(json_extract(payload,'$.event_date')) d FROM r GROUP BY 1,2,3),
ed AS (SELECT * FROM e WHERE t IN ('emergency','ED','ED stroke alert') AND d BETWEEN '2029-01-01' AND '2029-08-15'),
fu AS (SELECT DISTINCT ed.eid FROM ed JOIN e f ON f.patient=ed.patient AND f.t IN ('primary_care','office','pediatric_office','wellness','specialist_office','specialty_followup','telephone') AND julianday(f.d)-julianday(ed.d) BETWEEN 1 AND 7)
SELECT ROUND(100.0*(SELECT COUNT(*) FROM fu)/(SELECT COUNT(*) FROM ed),1) AS value;
