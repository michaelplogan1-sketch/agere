-- Latest dated Hemoglobin/Hgb below sex-specific cutoff (F<12, M<13); excludes A1c and fecal hemoglobin.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='labs'),
h AS (SELECT patient, json_extract(payload,'$.event_date') d, json_extract(payload,'$.value') v FROM r
      WHERE json_extract(payload,'$.name') IN ('Hemoglobin','Hgb') AND json_extract(payload,'$.event_date') IS NOT NULL
        AND coalesce(json_extract(payload,'$.result_status'),'')<>'cancelled' AND typeof(json_extract(payload,'$.value')) IN ('integer','real')),
l AS (SELECT h.patient, min(h.v) v FROM h JOIN (SELECT patient, max(d) md FROM h GROUP BY 1) m ON m.patient=h.patient AND m.md=h.d GROUP BY 1)
SELECT COUNT(*) AS count FROM l JOIN patients p ON p.id=l.patient
WHERE (json_extract(p.payload,'$.sex_reported')='F' AND l.v<12) OR (json_extract(p.payload,'$.sex_reported')='M' AND l.v<13);
