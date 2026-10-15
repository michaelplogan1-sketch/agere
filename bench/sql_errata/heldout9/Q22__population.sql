-- Population = every patient in the patients view (original deep-chart roster + population members).
-- Current records only: a record named in a visible same-stream record's supersedes link is replaced by it (releases with
-- supersession, e.g. v2.8), except adjudication chains, where the parent stays (payer_medical_claims child with another
-- adjudication_sequence or claim_id; payer_pharmacy_claims 'reversed'/'recouped' child; medical_claims 'reversed' child).
-- Influenza vaccination for the 2029-2030 season: immunizations vaccine 'influenza' / completed / season '2029-2030'; or, where no season
-- is recorded, lower(vaccine) starting 'influenza' (incl. 'influenza high-dose'), completed, event_date in the season window
-- 2029-07-01..2030-06-30; or a payer claim line with an influenza vaccine CPT or G0008 in that window (voided claims excluded).
WITH r0 AS MATERIALIZED (SELECT id, supersedes, patient, stream, payload FROM records WHERE stream IN ('immunizations','payer_medical_claims','payer_medical_claim_lines')),
sc AS MATERIALIZED (SELECT id, supersedes, stream, payload FROM r0 WHERE supersedes IS NOT NULL),
sup AS MATERIALIZED (SELECT c.supersedes id FROM sc c CROSS JOIN r0 p ON p.id=c.supersedes AND p.stream=c.stream
  WHERE NOT ((c.stream='payer_medical_claims' AND (json_extract(c.payload,'$.adjudication_sequence') IS NOT json_extract(p.payload,'$.adjudication_sequence')
               OR json_extract(c.payload,'$.payer_claim_id') IS NOT json_extract(p.payload,'$.payer_claim_id')))
          OR (c.stream='payer_pharmacy_claims' AND json_extract(c.payload,'$.claim_status') IN ('reversed','recouped'))
          OR (c.stream='medical_claims' AND json_extract(c.payload,'$.status')='reversed'))),
r AS MATERIALIZED (SELECT patient, stream, payload FROM r0 WHERE id NOT IN (SELECT id FROM sup))
SELECT COUNT(DISTINCT patient) AS count FROM (
  SELECT patient FROM r WHERE stream='immunizations' AND lower(json_extract(payload,'$.vaccine')) LIKE 'influenza%' AND json_extract(payload,'$.administration_status')='completed' AND json_extract(payload,'$.season')='2029-2030'
  UNION SELECT patient FROM r WHERE stream='immunizations' AND json_extract(payload,'$.season') IS NULL AND lower(json_extract(payload,'$.vaccine')) LIKE 'influenza%' AND json_extract(payload,'$.administration_status')='completed'
    AND json_extract(payload,'$.event_date') BETWEEN '2029-07-01' AND '2030-06-30'
  UNION SELECT patient FROM r WHERE stream='payer_medical_claim_lines' AND json_extract(payload,'$.procedure_code') IN ('90630','90653','90654','90655','90656','90657','90658','90660','90661','90662','90664','90666','90667','90668','90672','90673','90674','90682','90685','90686','90687','90688','90689','90694','90756','G0008') AND json_extract(payload,'$.service_date') BETWEEN '2029-07-01' AND '2030-06-30' AND json_extract(payload,'$.payer_claim_id') NOT IN (SELECT json_extract(payload,'$.payer_claim_id') FROM r WHERE stream='payer_medical_claims' AND json_extract(payload,'$.claim_status')='voided'));
