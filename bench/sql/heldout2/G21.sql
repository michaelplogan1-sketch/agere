-- Distinct referral_ids with requested_specialty Hematology, referral event dated 2029-01-01..2029-06-30.
SELECT COUNT(DISTINCT json_extract(payload,'$.referral_id')) AS count FROM records
WHERE stream='referrals' AND json_extract(payload,'$.requested_specialty')='Hematology'
  AND event BETWEEN '2029-01-01' AND '2029-06-30';
