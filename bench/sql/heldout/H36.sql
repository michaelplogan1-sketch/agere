WITH transport AS MATERIALIZED (
 SELECT DISTINCT patient FROM records WHERE stream='social_screens' AND json_type(payload,'$.transport_difficulty_reported')='true'
), noshow AS MATERIALIZED (
 SELECT DISTINCT patient FROM records WHERE stream='appointments' AND json_extract(payload,'$.status')='no_show'
) SELECT 100.0*SUM(patient IN (SELECT patient FROM noshow))/COUNT(*) AS value FROM transport;
