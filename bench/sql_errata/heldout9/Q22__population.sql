-- Patients vaccinated against influenza for the 2029-2030 season, population-wide:
--  immunizations rows with vaccine 'influenza' / administration_status 'completed' / season '2029-2030' (original roster), or, where no
--  season field is recorded (population), lower(vaccine) starting 'influenza' (incl. 'influenza high-dose'), completed, event_date in
--  the season window 2029-07-01..2030-06-30; or a payer claim line with an influenza vaccine CPT or G0008 (administration) and
--  service_date in that window (voided claims excluded). Distinct patients.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('immunizations','payer_medical_claims','payer_medical_claim_lines'))
SELECT COUNT(DISTINCT patient) AS count FROM (
  SELECT patient FROM r WHERE stream='immunizations' AND json_extract(payload,'$.vaccine')='influenza' AND json_extract(payload,'$.administration_status')='completed' AND json_extract(payload,'$.season')='2029-2030'
  UNION SELECT patient FROM r WHERE stream='immunizations' AND json_extract(payload,'$.season') IS NULL AND lower(json_extract(payload,'$.vaccine')) LIKE 'influenza%' AND json_extract(payload,'$.administration_status')='completed'
    AND json_extract(payload,'$.event_date') BETWEEN '2029-07-01' AND '2030-06-30'
  UNION SELECT patient FROM r WHERE stream='payer_medical_claim_lines' AND json_extract(payload,'$.procedure_code') IN ('90630','90653','90654','90655','90656','90657','90658','90660','90661','90662','90664','90666','90667','90668','90672','90673','90674','90682','90685','90686','90687','90688','90689','90694','90756','G0008') AND json_extract(payload,'$.service_date') BETWEEN '2029-07-01' AND '2030-06-30' AND json_extract(payload,'$.payer_claim_id') NOT IN (SELECT json_extract(payload,'$.payer_claim_id') FROM r WHERE stream='payer_medical_claims' AND json_extract(payload,'$.claim_status')='voided'));
