WITH s AS (
  SELECT patient, CAST(json_extract(payload,'$.total') AS REAL) AS score, event FROM records
  WHERE stream = 'mood_questionnaires' AND upper(json_extract(payload,'$.instrument')) = 'PHQ-9' AND json_extract(payload,'$.total') IS NOT NULL
  UNION ALL
  SELECT patient, CAST(json_extract(payload,'$.value') AS REAL), event FROM (SELECT * FROM records WHERE stream='labs' AND id NOT IN (SELECT supersedes FROM records WHERE stream='labs' AND supersedes IS NOT NULL))
  WHERE upper(trim(json_extract(payload,'$.name'))) = 'PHQ-9' AND json_extract(payload,'$.value') IS NOT NULL
)
SELECT COUNT(DISTINCT patient) AS count FROM s WHERE score >= 10
