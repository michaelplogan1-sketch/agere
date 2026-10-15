WITH a AS (SELECT json_extract(payload,'$.visit_number') v, patient, min(event) ad FROM records WHERE stream='adt_messages'
              AND json_extract(payload,'$.trigger_event')='A01' AND json_extract(payload,'$.patient_class')='I' GROUP BY 1,2),
d AS (SELECT json_extract(payload,'$.visit_number') v, max(event) dd FROM records WHERE stream='adt_messages' AND json_extract(payload,'$.trigger_event')='A03' GROUP BY 1)
SELECT round(avg(julianday(d.dd) - julianday(a.ad)), 2) AS value FROM a JOIN d ON d.v=a.v WHERE d.dd BETWEEN '2029-01-01' AND '2029-12-31'
