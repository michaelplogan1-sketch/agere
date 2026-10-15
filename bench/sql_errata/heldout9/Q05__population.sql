-- Population = every patient in the patients view (original deep-chart roster + population members).
-- Current records only: a record named in a visible same-stream record's supersedes link is replaced by it (releases with
-- supersession, e.g. v2.8), except adjudication chains, where the parent stays (payer_medical_claims child with another
-- adjudication_sequence or claim_id; payer_pharmacy_claims 'reversed'/'recouped' child; medical_claims 'reversed' child).
-- Hospital observation stays in 2029, deduplicated across sources: EHR encounters type 'observation' (versions collapse by
-- encounter_id); ADT adt_messages patient_class 'O' per encounter_id; payer_medical_claims with an observation line (revenue code 0762
-- or HCPCS G0378), date = service_from, voided claims excluded, one per patient-day. A claim day within 1 day of an EHR/ADT stay of the
-- same patient is that stay.
WITH r0 AS MATERIALIZED (SELECT id, supersedes, patient, stream, payload FROM records WHERE stream IN ('encounters','adt_messages','payer_medical_claims','payer_medical_claim_lines')),
sc AS MATERIALIZED (SELECT id, supersedes, stream, payload FROM r0 WHERE supersedes IS NOT NULL),
sup AS MATERIALIZED (SELECT c.supersedes id FROM sc c CROSS JOIN r0 p ON p.id=c.supersedes AND p.stream=c.stream
  WHERE NOT ((c.stream='payer_medical_claims' AND (json_extract(c.payload,'$.adjudication_sequence') IS NOT json_extract(p.payload,'$.adjudication_sequence')
               OR json_extract(c.payload,'$.payer_claim_id') IS NOT json_extract(p.payload,'$.payer_claim_id')))
          OR (c.stream='payer_pharmacy_claims' AND json_extract(c.payload,'$.claim_status') IN ('reversed','recouped'))
          OR (c.stream='medical_claims' AND json_extract(c.payload,'$.status')='reversed'))),
r AS MATERIALIZED (SELECT patient, stream, payload FROM r0 WHERE id NOT IN (SELECT id FROM sup)),
ev AS MATERIALIZED (
  SELECT patient, min(json_extract(payload,'$.event_date')) d FROM r WHERE stream='encounters' AND json_extract(payload,'$.type')='observation' GROUP BY patient, json_extract(payload,'$.encounter_id')
  UNION ALL
  SELECT patient, min(json_extract(payload,'$.event_date')) d FROM r WHERE stream='adt_messages' AND json_extract(payload,'$.patient_class')='O' GROUP BY patient, json_extract(payload,'$.encounter_id')),
obl AS MATERIALIZED (SELECT DISTINCT json_extract(payload,'$.payer_claim_id') cid FROM r WHERE stream='payer_medical_claim_lines' AND (json_extract(payload,'$.revenue_code')='0762' OR json_extract(payload,'$.procedure_code')='G0378')),
pc AS MATERIALIZED (SELECT patient, json_extract(payload,'$.payer_claim_id') cid, json_extract(payload,'$.claim_status') st, json_extract(payload,'$.service_from') d FROM r WHERE stream='payer_medical_claims'),
clm AS MATERIALIZED (SELECT DISTINCT patient, d FROM pc WHERE cid IN (SELECT cid FROM obl) AND cid NOT IN (SELECT cid FROM pc WHERE st='voided')),
stays AS (SELECT patient, d FROM ev UNION ALL
  SELECT patient, d FROM clm c WHERE NOT EXISTS (SELECT 1 FROM ev e WHERE e.patient=c.patient AND abs(julianday(e.d)-julianday(c.d))<=1))
SELECT COUNT(*) AS count FROM stays WHERE d BETWEEN '2029-01-01' AND '2029-12-31';
