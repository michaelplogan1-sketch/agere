-- ED visit (population-wide, all 4,499 patients in the patients view), deduplicated across sources:
--  (1) EHR encounters type 'emergency'/'ED'/'ED stroke alert' (version rows collapse by encounter_id; date = earliest event_date);
--  (2) hospital ADT feed adt_messages patient_class 'E' (A04/A08/A03 collapse by encounter_id; date = earliest event_date);
--  (3) payer_medical_claims ED claims: professional place_of_service '23', or a claim with an ED revenue line (045x) in
--      payer_medical_claim_lines; date = service_from; a claim_id that was voided is not evidence (its replacement is).
--  Dedupe rule: one visit per patient per date across EHR+ADT; an ED claim day is the same visit as an EHR/ADT ED visit of the same
--  patient on the same date, or 1 day apart when that EHR/ADT visit has no same-day ED claim (ADT clock vs claim service date);
--  otherwise the claim day is its own visit (claim days collapse per patient-day). Identities = warehouse patient ids (no fuzzy merge).
-- Count of distinct visits dated 2029-01-01..2029-12-31.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('encounters','adt_messages','payer_medical_claims','payer_medical_claim_lines')),
ev AS MATERIALIZED (
  SELECT patient, min(json_extract(payload,'$.event_date')) d, min(json_extract(payload,'$.organization_id')) org FROM r
   WHERE stream='encounters' AND json_extract(payload,'$.type') IN ('emergency','ED','ED stroke alert') GROUP BY patient, json_extract(payload,'$.encounter_id')
  UNION ALL
  SELECT patient, min(json_extract(payload,'$.event_date')) d, min(json_extract(payload,'$.organization_id')) org FROM r
   WHERE stream='adt_messages' AND json_extract(payload,'$.patient_class')='E' GROUP BY patient, json_extract(payload,'$.encounter_id')),
evd AS MATERIALIZED (SELECT patient, d, min(org) org FROM ev GROUP BY patient, d),
edl AS MATERIALIZED (SELECT DISTINCT json_extract(payload,'$.payer_claim_id') cid FROM r WHERE stream='payer_medical_claim_lines' AND json_extract(payload,'$.revenue_code') LIKE '045%'),
pc AS MATERIALIZED (SELECT patient, json_extract(payload,'$.payer_claim_id') cid, json_extract(payload,'$.claim_status') st, json_extract(payload,'$.claim_type') ct, json_extract(payload,'$.place_of_service') pos,
   json_extract(payload,'$.billing_provider_npi') npi, json_extract(payload,'$.service_from') d FROM r WHERE stream='payer_medical_claims'),
clm AS MATERIALIZED (SELECT patient, d, min(CASE WHEN ct='institutional' THEN npi END) npi FROM pc
   WHERE (pos='23' OR cid IN (SELECT cid FROM edl)) AND cid NOT IN (SELECT cid FROM pc WHERE st='voided') GROUP BY patient, d),
hosp AS MATERIALIZED (SELECT json_extract(payload,'$.npi') npi, min(id) org FROM source_entities WHERE json_extract(payload,'$.organization_type')='hospital' GROUP BY 1),
visits AS MATERIALIZED (SELECT patient, d, org FROM evd
  UNION ALL
  SELECT c.patient, c.d, h.org FROM clm c LEFT JOIN hosp h ON h.npi=c.npi
   WHERE NOT EXISTS (SELECT 1 FROM evd e WHERE e.patient=c.patient AND (e.d=c.d
     OR (abs(julianday(e.d)-julianday(c.d))=1 AND NOT EXISTS (SELECT 1 FROM clm c2 WHERE c2.patient=e.patient AND c2.d=e.d)))))
SELECT COUNT(*) AS count FROM visits WHERE d BETWEEN '2029-01-01' AND '2029-12-31';
