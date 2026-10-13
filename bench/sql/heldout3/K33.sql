-- Heart failure patients (K01 definition) who reported food difficulty on any social screen (food_difficulty_reported = true).
SELECT COUNT(DISTINCT patient) AS count FROM records
WHERE stream='social_screens' AND json_extract(payload,'$.food_difficulty_reported')=1
  AND patient IN (SELECT patient FROM records WHERE stream='conditions' AND (
    json_extract(payload,'$.snomed')='88805009' OR json_extract(payload,'$.icd10') LIKE 'I50%'
    OR lower(json_extract(payload,'$.description')) LIKE '%heart failure%' OR json_extract(payload,'$.description') LIKE '%CHF%'));
