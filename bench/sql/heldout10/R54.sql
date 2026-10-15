WITH q AS (
  SELECT patient, CAST(json_extract(payload,'$.total') AS INTEGER) AS total,
         ROW_NUMBER() OVER (PARTITION BY patient ORDER BY json_extract(payload,'$.event_time'), id) AS rn_first,
         ROW_NUMBER() OVER (PARTITION BY patient ORDER BY json_extract(payload,'$.event_time') DESC, id DESC) AS rn_last,
         COUNT(*) OVER (PARTITION BY patient) AS n
  FROM records WHERE stream = 'mood_questionnaires' AND upper(json_extract(payload,'$.instrument')) = 'PHQ-9'
)
SELECT COUNT(*) AS count FROM (
  SELECT patient, MAX(CASE WHEN rn_first = 1 THEN total END) AS f, MAX(CASE WHEN rn_last = 1 THEN total END) AS l
  FROM q WHERE n >= 2 GROUP BY patient
) WHERE l - f >= 5
