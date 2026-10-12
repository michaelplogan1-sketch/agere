WITH food AS MATERIALIZED (
 SELECT DISTINCT patient FROM records WHERE stream='social_screens' AND json_type(payload,'$.food_difficulty_reported')='true'
), ed AS MATERIALIZED (
 SELECT DISTINCT patient FROM records WHERE stream='encounters' AND json_extract(payload,'$.type') IN ('emergency','ED')
) SELECT COUNT(*) AS count FROM food WHERE patient IN (SELECT patient FROM ed);
