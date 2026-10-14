-- Social screens, population-wide: brief_clinical_needs_interview food_difficulty_reported / transport_difficulty_reported (original
-- roster; legacy transport 'unreliable') and AHC-HRSN screens (population) via positive_domains (food_worry / food_ran_out = food
-- insecurity; transportation_barrier = transport barrier).
-- Food referral = referrals referral_type 'food_resource_contact', community_service_referrals service_requested 'food_access'
-- (event_date on/after 2029-01-01), or an AHC-HRSN screen with a food positive domain and referral_offered true on/after 2029-01-01
-- (the population's only referral record).
-- Returns 2029 food-positive patients with no food referral.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('social_screens','referrals','community_service_referrals')),
pos AS (SELECT DISTINCT patient FROM r WHERE stream='social_screens' AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31' AND ((json_extract(payload,'$.food_difficulty_reported')=1) OR EXISTS (SELECT 1 FROM json_each(payload,'$.positive_domains') pd WHERE pd.value IN ('food_worry','food_ran_out')))),
ref AS (SELECT DISTINCT patient FROM r WHERE json_extract(payload,'$.event_date')>='2029-01-01' AND ((stream='referrals' AND json_extract(payload,'$.referral_type')='food_resource_contact')
   OR (stream='community_service_referrals' AND json_extract(payload,'$.service_requested')='food_access')
   OR (stream='social_screens' AND json_extract(payload,'$.referral_offered')=1 AND EXISTS (SELECT 1 FROM json_each(payload,'$.positive_domains') pd WHERE pd.value IN ('food_worry','food_ran_out')))))
SELECT patient AS patient_id FROM pos WHERE patient NOT IN (SELECT patient FROM ref) ORDER BY 1;
