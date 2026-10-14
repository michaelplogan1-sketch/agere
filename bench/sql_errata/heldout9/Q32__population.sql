-- Social screens, population-wide: brief_clinical_needs_interview food_difficulty_reported / transport_difficulty_reported (original
-- roster; legacy transport 'unreliable') and AHC-HRSN screens (population) via positive_domains (food_worry / food_ran_out = food
-- insecurity; transportation_barrier = transport barrier).
-- Distinct patients with a food-insecurity positive screen dated 2029.
SELECT COUNT(DISTINCT patient) AS count FROM records WHERE stream='social_screens' AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31' AND ((json_extract(payload,'$.food_difficulty_reported')=1) OR EXISTS (SELECT 1 FROM json_each(payload,'$.positive_domains') pd WHERE pd.value IN ('food_worry','food_ran_out')));
