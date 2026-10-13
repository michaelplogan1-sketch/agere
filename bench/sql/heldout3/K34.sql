-- Closed-loop rate for community service referrals (food access / transport): share of community_service_referrals records with at least one
-- community_service_outcomes record (linked by referral_record_id = referral record_id) where service_delivered = true. Percentage 0-100, 1 decimal.
SELECT ROUND(100.0 * SUM(json_extract(r.payload,'$.record_id') IN (
         SELECT json_extract(payload,'$.referral_record_id') FROM records WHERE stream='community_service_outcomes'
         AND json_extract(payload,'$.service_delivered')=1)) / COUNT(*), 1) AS value
FROM records r WHERE r.stream='community_service_referrals';
