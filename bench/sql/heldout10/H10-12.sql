WITH d AS (SELECT json_extract(payload,'$.visit_number') v, event, coalesce(json_extract(payload,'$.discharge_disposition'),'not_recorded') disp,
              row_number() OVER (PARTITION BY json_extract(payload,'$.visit_number') ORDER BY event DESC, id DESC) rn
            FROM records WHERE stream='adt_messages' AND json_extract(payload,'$.trigger_event')='A03' AND json_extract(payload,'$.patient_class')='I')
SELECT disp AS disposition, COUNT(*) AS discharges FROM d WHERE rn=1 AND event BETWEEN '2029-01-01' AND '2029-12-31' GROUP BY disp ORDER BY disp
