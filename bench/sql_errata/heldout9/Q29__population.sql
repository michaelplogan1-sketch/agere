-- Population = every patient in the patients view (original deep-chart roster + population members).
-- Current records only: a record named in a visible same-stream record's supersedes link is replaced by it (releases with
-- supersession, e.g. v2.8), except adjudication chains, where the parent stays (payer_medical_claims child with another
-- adjudication_sequence or claim_id; payer_pharmacy_claims 'reversed'/'recouped' child; medical_claims 'reversed' child).
-- Patients by coverage as of 2030-05-27: product of the current enrollment_spans row active on that date (coverage_start <= as-of and
-- coverage_end null or >= as-of; latest coverage_start, then record_id); no active span (uninsured originals, disenrolled or deceased
-- members, duplicate charts) = 'no_active_coverage'. Columns coverage_type, patients.
WITH r0 AS MATERIALIZED (SELECT id, supersedes, patient, stream, payload FROM records WHERE stream IN ('enrollment_spans')),
sc AS MATERIALIZED (SELECT id, supersedes, stream, payload FROM r0 WHERE supersedes IS NOT NULL),
sup AS MATERIALIZED (SELECT c.supersedes id FROM sc c CROSS JOIN r0 p ON p.id=c.supersedes AND p.stream=c.stream
  WHERE NOT ((c.stream='payer_medical_claims' AND (json_extract(c.payload,'$.adjudication_sequence') IS NOT json_extract(p.payload,'$.adjudication_sequence')
               OR json_extract(c.payload,'$.payer_claim_id') IS NOT json_extract(p.payload,'$.payer_claim_id')))
          OR (c.stream='payer_pharmacy_claims' AND json_extract(c.payload,'$.claim_status') IN ('reversed','recouped'))
          OR (c.stream='medical_claims' AND json_extract(c.payload,'$.status')='reversed'))),
r AS MATERIALIZED (SELECT patient, stream, payload FROM r0 WHERE id NOT IN (SELECT id FROM sup)),
act AS (SELECT patient, json_extract(payload,'$.product') prod, row_number() OVER (PARTITION BY patient ORDER BY json_extract(payload,'$.coverage_start') DESC, json_extract(payload,'$.record_id') DESC) rn FROM r
   WHERE json_extract(payload,'$.coverage_start')<='2030-05-27' AND (json_extract(payload,'$.coverage_end') IS NULL OR json_extract(payload,'$.coverage_end')>='2030-05-27'))
SELECT coalesce(a.prod,'no_active_coverage') AS coverage_type, COUNT(*) AS patients FROM patients p LEFT JOIN act a ON a.patient=p.id AND a.rn=1
GROUP BY 1 ORDER BY 1;
