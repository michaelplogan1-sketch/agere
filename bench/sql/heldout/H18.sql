WITH per AS (
 SELECT patient,COUNT(DISTINCT json_extract(payload,'$.encounter_id')) AS n FROM records WHERE stream='encounters'
  AND json_extract(payload,'$.type')='primary_care' AND event BETWEEN '2028-01-01' AND '2028-12-31'
  AND json_extract(payload,'$.encounter_id') IS NOT NULL GROUP BY patient
) SELECT AVG(n) AS value FROM per;
