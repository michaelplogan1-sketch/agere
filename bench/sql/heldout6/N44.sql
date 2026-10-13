-- Patients positive in >=2 of food / transportation / cost-of-care domains.
WITH s AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream IN ('social_screens','financial_screens')),
food AS (SELECT DISTINCT patient FROM s WHERE json_extract(payload,'$.food_difficulty_reported')=1),
trans AS (SELECT DISTINCT patient FROM s WHERE json_extract(payload,'$.transport_difficulty_reported')=1 OR json_extract(payload,'$.transport')='unreliable'),
cost AS (SELECT DISTINCT patient FROM s WHERE json_extract(payload,'$.difficulty_paying_for_care_reported')=1 OR json_extract(payload,'$.difficulty_paying_medical_bills')=1),
u AS (SELECT patient FROM food UNION ALL SELECT patient FROM trans UNION ALL SELECT patient FROM cost)
SELECT count(*) AS count FROM (SELECT patient FROM u GROUP BY 1 HAVING count(*) >= 2);
