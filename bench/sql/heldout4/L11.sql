-- Female patients with a dated Hemoglobin/Hgb < 10 g/dL in 2028-08-16..2029-08-15.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('labs'))
SELECT COUNT(DISTINCT r.patient) AS count FROM r JOIN patients p ON p.id=r.patient
WHERE json_extract(p.payload,'$.sex_reported')='F' AND json_extract(r.payload,'$.name') IN ('Hemoglobin','Hgb')
  AND json_extract(r.payload,'$.unit')='g/dL' AND json_type(r.payload,'$.value') IN ('integer','real') AND json_extract(r.payload,'$.value') < 10.0
  AND json_extract(r.payload,'$.event_date') BETWEEN '2028-08-16' AND '2029-08-15';
