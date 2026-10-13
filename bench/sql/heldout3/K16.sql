-- ED visits dated in calendar 2026 (distinct encounter_id, types 'emergency','ED','ED stroke alert') that were followed by an in-person
-- clinic visit for the same patient within 0-7 days after the ED date (event_date difference 0..7, encounter on a later or same day but a different encounter).
-- Clinic types: 'primary_care','office','pediatric_office','specialist_office','specialty_followup','ambulatory','wellness','outpatient'.
-- Telephone, virtual, home health and other ED/urgent/inpatient encounters do not count. Count ED visits, not patients.
WITH e AS (SELECT patient, json_extract(payload,'$.encounter_id') id, MIN(json_extract(payload,'$.type')) t, MIN(json_extract(payload,'$.event_date')) d
  FROM records WHERE stream='encounters' GROUP BY patient, json_extract(payload,'$.encounter_id'))
SELECT COUNT(*) AS count FROM e ed
WHERE ed.t IN ('emergency','ED','ED stroke alert') AND ed.d BETWEEN '2026-01-01' AND '2026-12-31'
  AND EXISTS (SELECT 1 FROM e f WHERE f.patient=ed.patient AND f.id<>ed.id
      AND f.t IN ('primary_care','office','pediatric_office','specialist_office','specialty_followup','ambulatory','wellness','outpatient')
      AND julianday(f.d)-julianday(ed.d) BETWEEN 0 AND 7);
