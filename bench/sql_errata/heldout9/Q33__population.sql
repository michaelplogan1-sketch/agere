-- Population = every patient in the patients view (original deep-chart roster + population members).
-- Current records only: a record named in a visible same-stream record's supersedes link is replaced by it (releases with
-- supersession, e.g. v2.8), except adjudication chains, where the parent stays (payer_medical_claims child with another
-- adjudication_sequence or claim_id; payer_pharmacy_claims 'reversed'/'recouped' child; medical_claims 'reversed' child).
-- Social screens: brief_clinical_needs_interview food_difficulty_reported / transport_difficulty_reported (legacy transport
-- 'unreliable') and AHC-HRSN screens via positive_domains (food_worry / food_ran_out = food insecurity; transportation_barrier).
-- Food referral = referrals referral_type 'food_resource_contact', community_service_referrals service_requested 'food_access'
-- (event_date on/after 2029-01-01), or an AHC-HRSN food-positive screen with referral_offered true on/after 2029-01-01.
-- Returns 2029 food-positive patients with no food referral.
WITH r0 AS MATERIALIZED (SELECT id, supersedes, patient, stream, payload FROM records WHERE stream IN ('social_screens','referrals','community_service_referrals')),
sc AS MATERIALIZED (SELECT id, supersedes, stream, payload FROM r0 WHERE supersedes IS NOT NULL),
sup AS MATERIALIZED (SELECT c.supersedes id FROM sc c CROSS JOIN r0 p ON p.id=c.supersedes AND p.stream=c.stream
  WHERE NOT ((c.stream='payer_medical_claims' AND (json_extract(c.payload,'$.adjudication_sequence') IS NOT json_extract(p.payload,'$.adjudication_sequence')
               OR json_extract(c.payload,'$.payer_claim_id') IS NOT json_extract(p.payload,'$.payer_claim_id')))
          OR (c.stream='payer_pharmacy_claims' AND json_extract(c.payload,'$.claim_status') IN ('reversed','recouped'))
          OR (c.stream='medical_claims' AND json_extract(c.payload,'$.status')='reversed'))),
r AS MATERIALIZED (SELECT patient, stream, payload FROM r0 WHERE id NOT IN (SELECT id FROM sup)),
pos AS (SELECT DISTINCT patient FROM r WHERE stream='social_screens' AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31' AND ((json_extract(payload,'$.food_difficulty_reported')=1) OR EXISTS (SELECT 1 FROM json_each(payload,'$.positive_domains') pd WHERE pd.value IN ('food_worry','food_ran_out')))),
ref AS (SELECT DISTINCT patient FROM r WHERE json_extract(payload,'$.event_date')>='2029-01-01' AND ((stream='referrals' AND json_extract(payload,'$.referral_type')='food_resource_contact')
   OR (stream='community_service_referrals' AND json_extract(payload,'$.service_requested')='food_access')
   OR (stream='social_screens' AND json_extract(payload,'$.referral_offered')=1 AND EXISTS (SELECT 1 FROM json_each(payload,'$.positive_domains') pd WHERE pd.value IN ('food_worry','food_ran_out')))))
SELECT patient AS patient_id FROM pos WHERE patient NOT IN (SELECT patient FROM ref) ORDER BY 1;
