-- Patients reporting food insecurity in 2029: distinct patients with a social_screens row food_difficulty_reported = true (brief_clinical_needs_interview) dated 2029-01-01..2029-12-31.
SELECT COUNT(DISTINCT patient) AS count FROM records WHERE stream='social_screens' AND json_extract(payload,'$.food_difficulty_reported')=1
  AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31';
