-- Patients who reported BOTH food difficulty (social_screens food_difficulty_reported = true) and transportation difficulty
-- (transport_difficulty_reported = true, or social_screens transport = 'unreliable'), on any social screen (not necessarily the same one).
WITH s AS (SELECT patient, json_extract(payload,'$.food_difficulty_reported') f, json_extract(payload,'$.transport_difficulty_reported') t,
                  json_extract(payload,'$.transport') tr FROM records WHERE stream='social_screens')
SELECT COUNT(*) AS count FROM (SELECT patient FROM s GROUP BY patient
  HAVING MAX(f=1)=1 AND MAX(t=1 OR tr='unreliable')=1);
