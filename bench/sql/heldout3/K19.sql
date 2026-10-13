-- First-pass denial rate for medical claims in benefit year 2028: distinct claim_ids (benefit_year 2028) with at least one adjudication
-- record of status 'denied' (whether or not later paid), divided by all distinct claim_ids in benefit year 2028, as a percentage (0-100), 1 decimal.
-- Legacy claim rows without a claim_id/benefit_year are excluded.
WITH c AS (SELECT json_extract(payload,'$.claim_id') id, MAX(json_extract(payload,'$.status')='denied') denied
           FROM records WHERE stream='medical_claims' AND json_extract(payload,'$.benefit_year')=2028 AND json_extract(payload,'$.claim_id') IS NOT NULL
           GROUP BY 1)
SELECT ROUND(100.0*SUM(denied)/COUNT(*),1) AS value FROM c;
