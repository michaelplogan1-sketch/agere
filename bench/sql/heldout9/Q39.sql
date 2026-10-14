-- PHQ-9 worsening: among patients with >= 2 PHQ-9 questionnaires (mood_questionnaires instrument 'PHQ-9', any date through as-of), those whose latest total (by event_date, event_time, record_id) is at least 5 points higher than their first total. Returns patient_id.
WITH q AS (SELECT patient, json_extract(payload,'$.total') t,
  row_number() OVER (PARTITION BY patient ORDER BY json_extract(payload,'$.event_date'), json_extract(payload,'$.event_time'), json_extract(payload,'$.record_id')) f,
  row_number() OVER (PARTITION BY patient ORDER BY json_extract(payload,'$.event_date') DESC, json_extract(payload,'$.event_time') DESC, json_extract(payload,'$.record_id') DESC) l,
  COUNT(*) OVER (PARTITION BY patient) n
  FROM records WHERE stream='mood_questionnaires' AND json_extract(payload,'$.instrument')='PHQ-9')
SELECT a.patient AS patient_id FROM q a JOIN q b ON a.patient=b.patient AND a.f=1 AND b.l=1 WHERE a.n>=2 AND b.t-a.t>=5 ORDER BY 1;
