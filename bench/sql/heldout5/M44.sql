-- Community service referral outcomes by service: referrals = distinct community_service_referrals records (record_id); delivered = those with ANY community_service_outcomes record (matched on referral_record_id) where service_delivered = true (scheduled/waitlisted/declined/unreachable are not delivered). Columns: service, referrals, delivered.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('community_service_referrals','community_service_outcomes')),
ref AS (SELECT json_extract(payload,'$.record_id') rid, json_extract(payload,'$.service_requested') svc FROM r WHERE stream='community_service_referrals'),
dl AS (SELECT DISTINCT json_extract(payload,'$.referral_record_id') rid FROM r WHERE stream='community_service_outcomes' AND json_extract(payload,'$.service_delivered')=1)
SELECT svc AS service, COUNT(*) AS referrals, SUM(rid IN (SELECT rid FROM dl)) AS delivered FROM ref GROUP BY svc ORDER BY svc;
