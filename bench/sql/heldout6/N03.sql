-- Roster women age_reported >= 65 whose coverage record is Medicare.
WITH c AS MATERIALIZED (SELECT patient, json_extract(payload,'$.coverage_type') t FROM records WHERE stream='coverage')
SELECT COUNT(DISTINCT p.id) AS count FROM patients p JOIN c ON c.patient=p.id
WHERE json_extract(p.payload,'$.sex_reported')='F' AND json_extract(p.payload,'$.age_reported')>=65 AND c.t='medicare';
