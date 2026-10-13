-- Patients with a transportation barrier on EITHER social-needs instrument, ever: brief_clinical_needs_interview transport_difficulty_reported = true, OR the other social screen's transport = 'unreliable' ('family_ride' and 'reliable' are not barriers). Distinct patients.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='social_screens')
SELECT COUNT(DISTINCT patient) AS count FROM r WHERE json_extract(payload,'$.transport_difficulty_reported')=1 OR json_extract(payload,'$.transport')='unreliable';
