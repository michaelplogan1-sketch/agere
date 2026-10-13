-- Transportation barrier (social_screens transport='unreliable' OR transport_difficulty_reported=true) AND >=1 appointment record with status no_show (any date).
SELECT COUNT(*) AS count FROM (
  SELECT DISTINCT patient FROM records WHERE stream='social_screens'
    AND (json_extract(payload,'$.transport')='unreliable' OR json_extract(payload,'$.transport_difficulty_reported')=1)
  INTERSECT
  SELECT DISTINCT patient FROM records WHERE stream='appointments' AND json_extract(payload,'$.status')='no_show');
