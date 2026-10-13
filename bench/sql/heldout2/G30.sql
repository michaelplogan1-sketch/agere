-- Patients with an ED encounter (emergency/ED*, event 2028-01-01..2029-08-15) followed by a completed
-- primary_care or office encounter 0-7 days after the ED event date (later encounter ID, different from the ED one).
WITH ed AS MATERIALIZED (
  SELECT DISTINCT patient, event AS d, json_extract(payload,'$.encounter_id') AS enc FROM records
  WHERE stream='encounters' AND event BETWEEN '2028-01-01' AND '2029-08-15'
    AND (lower(json_extract(payload,'$.type'))='emergency' OR json_extract(payload,'$.type') LIKE 'ED%')
), pc AS MATERIALIZED (
  SELECT DISTINCT patient, event AS d, json_extract(payload,'$.encounter_id') AS enc FROM records
  WHERE stream='encounters' AND json_extract(payload,'$.type') IN ('primary_care','office')
    AND json_extract(payload,'$.status')='completed' AND event >= '2028-01-01'
)
SELECT COUNT(DISTINCT ed.patient) AS count FROM ed JOIN pc ON pc.patient=ed.patient AND pc.enc<>ed.enc
  AND julianday(pc.d)-julianday(ed.d) BETWEEN 0 AND 7;
