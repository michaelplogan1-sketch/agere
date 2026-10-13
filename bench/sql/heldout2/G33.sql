-- Patients with any social_screens record food_difficulty_reported=true AND >=1 ED encounter (emergency/ED*) event-dated 2028-01-01..2029-08-15.
SELECT COUNT(*) AS count FROM (
  SELECT DISTINCT patient FROM records WHERE stream='social_screens' AND json_extract(payload,'$.food_difficulty_reported')=1
  INTERSECT
  SELECT DISTINCT patient FROM records WHERE stream='encounters' AND event BETWEEN '2028-01-01' AND '2029-08-15'
    AND (lower(json_extract(payload,'$.type'))='emergency' OR json_extract(payload,'$.type') LIKE 'ED%'));
