-- Population = every patient in the patients view (original deep-chart roster + population members).
-- Current records only: a record named in a visible same-stream record's supersedes link is replaced by it (releases with
-- supersession, e.g. v2.8), except adjudication chains, where the parent stays (payer_medical_claims child with another
-- adjudication_sequence or claim_id; payer_pharmacy_claims 'reversed'/'recouped' child; medical_claims 'reversed' child).
-- ED visit, deduplicated across sources:
--  (1) EHR encounters type 'emergency'/'ED'/'ED stroke alert' (version rows collapse by encounter_id; date = earliest event_date);
--  (2) hospital ADT adt_messages patient_class 'E' (messages collapse by encounter_id; date = earliest event_date);
--  (3) payer_medical_claims ED claims: professional place_of_service '23', or a claim with an ED revenue line (045x) in
--      payer_medical_claim_lines; date = service_from; a voided claim_id is not evidence (its replacement is).
--  Dedupe: one visit per patient per date across EHR+ADT; an ED claim day is the same visit as an EHR/ADT ED visit of the same
--  patient on the same date, or 1 day apart when that EHR/ADT visit has no same-day ED claim (ADT clock vs claim service date);
--  otherwise the claim day is its own visit (claim days collapse per patient-day). Identities = warehouse patient ids (no fuzzy merge).
-- Patients with >= 2 visits dated 2029. Returns patient_id.
WITH r0 AS MATERIALIZED (SELECT id, supersedes, patient, stream, payload FROM records WHERE stream IN ('encounters','adt_messages','payer_medical_claims','payer_medical_claim_lines')),
sc AS MATERIALIZED (SELECT id, supersedes, stream, payload FROM r0 WHERE supersedes IS NOT NULL),
sup AS MATERIALIZED (SELECT c.supersedes id FROM sc c CROSS JOIN r0 p ON p.id=c.supersedes AND p.stream=c.stream
  WHERE NOT ((c.stream='payer_medical_claims' AND (json_extract(c.payload,'$.adjudication_sequence') IS NOT json_extract(p.payload,'$.adjudication_sequence')
               OR json_extract(c.payload,'$.payer_claim_id') IS NOT json_extract(p.payload,'$.payer_claim_id')))
          OR (c.stream='payer_pharmacy_claims' AND json_extract(c.payload,'$.claim_status') IN ('reversed','recouped'))
          OR (c.stream='medical_claims' AND json_extract(c.payload,'$.status')='reversed'))),
r AS MATERIALIZED (SELECT patient, stream, payload FROM r0 WHERE id NOT IN (SELECT id FROM sup)),
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
SELECT patient AS patient_id FROM visits WHERE d BETWEEN '2029-01-01' AND '2029-12-31' GROUP BY patient HAVING COUNT(*)>=2 ORDER BY 1;
