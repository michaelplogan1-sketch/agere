-- Current records only: a record named in a visible same-stream record's supersedes link is replaced by it (releases with
-- supersession, e.g. v2.8), except adjudication chains, where the parent stays (payer_medical_claims child with another
-- adjudication_sequence or claim_id; payer_pharmacy_claims 'reversed'/'recouped' child; medical_claims 'reversed' child).
-- Reading B on problem-list hypertensives only: latest 2029 reading >= 130 systolic OR >= 80 diastolic.
WITH r0 AS MATERIALIZED (SELECT id, supersedes, patient, stream, payload FROM records WHERE stream IN ('conditions','vitals')),
sc AS MATERIALIZED (SELECT id, supersedes, stream, payload FROM r0 WHERE supersedes IS NOT NULL),
sup AS MATERIALIZED (SELECT c.supersedes id FROM sc c CROSS JOIN r0 p ON p.id=c.supersedes AND p.stream=c.stream
  WHERE NOT ((c.stream='payer_medical_claims' AND (json_extract(c.payload,'$.adjudication_sequence') IS NOT json_extract(p.payload,'$.adjudication_sequence')
               OR json_extract(c.payload,'$.payer_claim_id') IS NOT json_extract(p.payload,'$.payer_claim_id')))
          OR (c.stream='payer_pharmacy_claims' AND json_extract(c.payload,'$.claim_status') IN ('reversed','recouped'))
          OR (c.stream='medical_claims' AND json_extract(c.payload,'$.status')='reversed'))),
r AS MATERIALIZED (SELECT patient, stream, payload FROM r0 WHERE id NOT IN (SELECT id FROM sup)),
htn AS MATERIALIZED (SELECT patient FROM r WHERE stream='conditions' AND (substr(json_extract(payload,'$.icd10'),1,3) IN ('I10','I11','I12','I13','I15','I16')
   OR ((lower(json_extract(payload,'$.description')) LIKE '%hypertension%' OR json_extract(payload,'$.description') LIKE '%HTN%') AND coalesce(json_extract(payload,'$.icd10'),'') NOT LIKE 'I27%'))),
b AS (SELECT patient, json_extract(payload,'$.blood_pressure_systolic') s, json_extract(payload,'$.blood_pressure_diastolic') d,
  row_number() OVER (PARTITION BY patient ORDER BY json_extract(payload,'$.event_date') DESC, json_extract(payload,'$.event_time') DESC, json_extract(payload,'$.record_id') DESC) rn
  FROM r WHERE stream='vitals' AND json_extract(payload,'$.blood_pressure_systolic') IS NOT NULL AND json_extract(payload,'$.blood_pressure_diastolic') IS NOT NULL AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31')
SELECT COUNT(*) AS count FROM b WHERE rn=1 AND (s>=130 OR d>=80) AND patient IN (SELECT patient FROM htn);
