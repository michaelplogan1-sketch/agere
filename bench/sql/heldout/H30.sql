WITH refs AS MATERIALIZED (SELECT id FROM records WHERE stream='referrals'),
updates AS MATERIALIZED (
 SELECT DISTINCT json_extract(payload,'$.referral_record_id') AS ref FROM records
 WHERE stream='referral_status_updates' AND json_extract(payload,'$.referral_record_id') IS NOT NULL
) SELECT COUNT(*) AS count FROM refs WHERE id NOT IN (SELECT ref FROM updates);
