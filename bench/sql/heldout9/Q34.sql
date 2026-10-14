-- Patients with transportation barriers documented in 2029: distinct patients with a social_screens row dated 2029-01-01..2029-12-31 where transport_difficulty_reported = true (brief_clinical_needs_interview) or legacy transport = 'unreliable'.
SELECT COUNT(DISTINCT patient) AS count FROM records WHERE stream='social_screens' AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31'
  AND (json_extract(payload,'$.transport_difficulty_reported')=1 OR json_extract(payload,'$.transport')='unreliable');
