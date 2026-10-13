-- 30-day return to acute care: index = ADT discharge event (any patient class); a return is an ADT admission,
-- emergency_registration or observation_start for a DIFFERENT encounter of the same patient 1..30 days after the discharge date.
-- Percentage of index discharges with a return.
WITH acute AS MATERIALIZED (
  SELECT patient, json_extract(payload,'$.encounter_id') AS enc, event AS d FROM records
  WHERE stream='adt' AND json_extract(payload,'$.event') IN ('admission','emergency_registration','observation_start')
), dis AS MATERIALIZED (
  SELECT patient, json_extract(payload,'$.encounter_id') AS enc, event AS d FROM records
  WHERE stream='adt' AND json_extract(payload,'$.event')='discharge'
)
SELECT ROUND(100.0 * SUM(EXISTS (SELECT 1 FROM acute a WHERE a.patient=dis.patient AND a.enc<>dis.enc
          AND julianday(a.d)-julianday(dis.d) BETWEEN 1 AND 30)) / COUNT(*), 4) AS value FROM dis;
