WITH a AS (SELECT json_extract(payload,'$.visit_number') v, min(event) ad FROM records WHERE stream='adt_messages'
              AND json_extract(payload,'$.trigger_event')='A01' AND json_extract(payload,'$.patient_class')='I' GROUP BY 1),
d AS (SELECT DISTINCT json_extract(payload,'$.visit_number') v FROM records WHERE stream='adt_messages' AND json_extract(payload,'$.trigger_event')='A03' AND event <= '2029-12-31')
SELECT COUNT(*) AS count FROM a WHERE ad BETWEEN '2029-01-01' AND '2029-12-31' AND v NOT IN (SELECT v FROM d)
