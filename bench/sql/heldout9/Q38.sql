-- Patients whose most recent PHQ-9 in the 12 months to the as-of date (mood_questionnaires instrument 'PHQ-9', event_date 2029-05-28..2030-05-27; latest by event_date, event_time, record_id) has total >= 10 (moderate or worse depressive symptoms). Repeat questionnaires for one patient count once. Distinct patients.
WITH q AS (SELECT patient, json_extract(payload,'$.total') t, row_number() OVER (PARTITION BY patient ORDER BY json_extract(payload,'$.event_date') DESC, json_extract(payload,'$.event_time') DESC, json_extract(payload,'$.record_id') DESC) rn
  FROM records WHERE stream='mood_questionnaires' AND json_extract(payload,'$.instrument')='PHQ-9' AND json_extract(payload,'$.event_date') BETWEEN '2029-05-28' AND '2030-05-27')
SELECT COUNT(*) AS count FROM q WHERE rn=1 AND t>=10;
