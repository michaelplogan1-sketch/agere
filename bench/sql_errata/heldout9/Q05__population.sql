-- Hospital observation stays in 2029 (population-wide), deduplicated across sources:
--  (1) EHR encounters type 'observation' (versions collapse by encounter_id; date = earliest event_date);
--  (2) hospital ADT adt_messages patient_class 'O' per encounter_id (date = earliest event_date);
--  (3) payer_medical_claims carrying an observation line (revenue code 0762 or HCPCS G0378 in payer_medical_claim_lines), date =
--      service_from, voided claim_ids excluded, one per patient-day.
--  Dedupe rule: a claim observation day within 1 day of an EHR/ADT observation stay of the same patient is that stay.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('encounters','adt_messages','payer_medical_claims','payer_medical_claim_lines')),
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
