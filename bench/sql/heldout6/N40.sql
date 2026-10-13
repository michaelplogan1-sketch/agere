-- Distinct claim_ids whose original submission has a null member_identifier_submitted.
WITH r AS MATERIALIZED (SELECT payload FROM records WHERE stream='medical_claim_submissions')
SELECT count(DISTINCT json_extract(payload,'$.claim_id')) AS count FROM r
WHERE json_extract(payload,'$.submission_kind')='original' AND json_extract(payload,'$.member_identifier_submitted') IS NULL;
