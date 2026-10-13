-- Distinct patients positive per social-need domain across social_screens and financial_screens.
WITH s AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream IN ('social_screens','financial_screens')),
food AS (SELECT DISTINCT patient FROM s WHERE json_extract(payload,'$.food_difficulty_reported')=1),
trans AS (SELECT DISTINCT patient FROM s WHERE json_extract(payload,'$.transport_difficulty_reported')=1 OR json_extract(payload,'$.transport')='unreliable'),
cost AS (SELECT DISTINCT patient FROM s WHERE json_extract(payload,'$.difficulty_paying_for_care_reported')=1 OR json_extract(payload,'$.difficulty_paying_medical_bills')=1)
SELECT 'food' AS need, (SELECT count(*) FROM food) AS patients UNION ALL SELECT 'transportation', (SELECT count(*) FROM trans)
UNION ALL SELECT 'cost_of_care', (SELECT count(*) FROM cost);
