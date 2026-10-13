-- Patients with latest dated eGFR < 45 AND latest dated UACR >= 30 mg/g; undated/cancelled/non-numeric ignored; same-date ties -> worst value.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='labs'),
l AS (SELECT patient, json_extract(payload,'$.name') n, json_extract(payload,'$.event_date') d, json_extract(payload,'$.value') v
      FROM r WHERE json_extract(payload,'$.event_date') IS NOT NULL AND coalesce(json_extract(payload,'$.result_status'),'') <> 'cancelled'
        AND typeof(json_extract(payload,'$.value')) IN ('integer','real')),
e AS (SELECT patient, d, v FROM l WHERE n IN ('eGFR','Glomerular filtration rate')),
u AS (SELECT patient, d, v FROM l WHERE n IN ('Urine albumin/creatinine ratio','Microalbumin/Creatinine')),
le AS (SELECT e.patient, min(e.v) v FROM e JOIN (SELECT patient, max(d) md FROM e GROUP BY 1) m ON m.patient=e.patient AND m.md=e.d GROUP BY 1),
lu AS (SELECT u.patient, max(u.v) v FROM u JOIN (SELECT patient, max(d) md FROM u GROUP BY 1) m ON m.patient=u.patient AND m.md=u.d GROUP BY 1)
SELECT le.patient AS patient_id FROM le JOIN lu USING(patient) WHERE le.v < 45 AND lu.v >= 30 ORDER BY 1;
