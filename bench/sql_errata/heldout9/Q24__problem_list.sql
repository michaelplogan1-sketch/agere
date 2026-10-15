-- Current records only: a record named in a visible same-stream record's supersedes link is replaced by it (releases with
-- supersession, e.g. v2.8), except adjudication chains, where the parent stays (payer_medical_claims child with another
-- adjudication_sequence or claim_id; payer_pharmacy_claims 'reversed'/'recouped' child; medical_claims 'reversed' child).
-- uACR lab = lower(name) in 'urine albumin/creatinine ratio', 'microalbumin/creatinine', 'microalb/creat ratio, random urine',
-- 'microalbumin/creatinine [mass ratio] in urine' or LOINC 9318-7; dated numeric current 2029 results (urine albumin alone does not count;
-- claims carry 82043 urine albumin but no 82570 creatinine, so claims cannot evidence a ratio).
-- Problem-list diabetics only.
WITH r0 AS MATERIALIZED (SELECT id, supersedes, patient, stream, payload FROM records WHERE stream IN ('conditions','labs')),
sc AS MATERIALIZED (SELECT id, supersedes, stream, payload FROM r0 WHERE supersedes IS NOT NULL),
sup AS MATERIALIZED (SELECT c.supersedes id FROM sc c CROSS JOIN r0 p ON p.id=c.supersedes AND p.stream=c.stream
  WHERE NOT ((c.stream='payer_medical_claims' AND (json_extract(c.payload,'$.adjudication_sequence') IS NOT json_extract(p.payload,'$.adjudication_sequence')
               OR json_extract(c.payload,'$.payer_claim_id') IS NOT json_extract(p.payload,'$.payer_claim_id')))
          OR (c.stream='payer_pharmacy_claims' AND json_extract(c.payload,'$.claim_status') IN ('reversed','recouped'))
          OR (c.stream='medical_claims' AND json_extract(c.payload,'$.status')='reversed'))),
r AS MATERIALIZED (SELECT patient, stream, payload FROM r0 WHERE id NOT IN (SELECT id FROM sup)),
dm AS MATERIALIZED (SELECT DISTINCT patient FROM r WHERE stream='conditions' AND substr(json_extract(payload,'$.icd10'),1,3) IN ('E10','E11','E13'))
SELECT COUNT(DISTINCT patient) AS count FROM r WHERE stream='labs' AND (lower(json_extract(payload,'$.name')) IN ('urine albumin/creatinine ratio','microalbumin/creatinine','microalb/creat ratio, random urine','microalbumin/creatinine [mass ratio] in urine') OR json_extract(payload,'$.loinc')='9318-7') AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31' AND json_type(payload,'$.value') IN ('integer','real') AND patient IN (SELECT patient FROM dm);
