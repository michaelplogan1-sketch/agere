-- Patients with a transportation barrier on ANY social screen: 2026 screen transport='unreliable' OR 2028 brief_clinical_needs_interview transport_difficulty_reported = true. 'family_ride' is not a barrier. Distinct patients.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='social_screens')
SELECT COUNT(DISTINCT patient) AS count FROM r WHERE json_extract(payload,'$.transport')='unreliable' OR json_extract(payload,'$.transport_difficulty_reported')=1;
