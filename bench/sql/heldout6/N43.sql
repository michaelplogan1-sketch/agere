-- % of transport-need patients with a transport_assistance referral or a community transport service referral.
WITH s AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream IN ('social_screens','financial_screens')),
food AS (SELECT DISTINCT patient FROM s WHERE json_extract(payload,'$.food_difficulty_reported')=1),
trans AS (SELECT DISTINCT patient FROM s WHERE json_extract(payload,'$.transport_difficulty_reported')=1 OR json_extract(payload,'$.transport')='unreliable'),
cost AS (SELECT DISTINCT patient FROM s WHERE json_extract(payload,'$.difficulty_paying_for_care_reported')=1 OR json_extract(payload,'$.difficulty_paying_medical_bills')=1),
ref AS (SELECT DISTINCT patient FROM records WHERE (stream='referrals' AND json_extract(payload,'$.referral_type')='transport_assistance')
        OR (stream='community_service_referrals' AND json_extract(payload,'$.service_requested')='transport'))
SELECT round(100.0*(SELECT count(*) FROM trans WHERE patient IN (SELECT patient FROM ref))/(SELECT count(*) FROM trans),1) AS value;
