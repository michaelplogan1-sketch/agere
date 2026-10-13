-- Referral loop, 2028: referrals with a referral_type (dental, vision, audiology, food, transport, orthopedic, etc.) dated (event_date) in 2028,
-- by referral_type: number of referrals, and number with a referral_status_updates record (linked via referral_record_id = referral record_id)
-- of status 'appointment_reported_booked'.
WITH r AS (SELECT json_extract(payload,'$.record_id') rid, json_extract(payload,'$.referral_type') t FROM records
           WHERE stream='referrals' AND json_extract(payload,'$.referral_type') IS NOT NULL
             AND json_extract(payload,'$.event_date') BETWEEN '2028-01-01' AND '2028-12-31'),
b AS (SELECT DISTINCT json_extract(payload,'$.referral_record_id') rid FROM records WHERE stream='referral_status_updates'
      AND json_extract(payload,'$.status')='appointment_reported_booked')
SELECT t AS referral_type, COUNT(*) AS referrals, SUM(rid IN (SELECT rid FROM b)) AS booked FROM r GROUP BY 1 ORDER BY 1;
