-- Patients by coverage type as of the as-of date (2030-05-27), all 4,499 patients: product of the enrollment_spans row active on the
-- as-of date (coverage_start <= as-of and coverage_end null or >= as-of; latest coverage_start, then record_id, when several);
-- patients with no active span (original-roster uninsured, disenrolled or deceased members, duplicate charts) = 'no_active_coverage'.
-- Columns coverage_type, patients.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('enrollment_spans')),
act AS (SELECT patient, json_extract(payload,'$.product') prod, row_number() OVER (PARTITION BY patient ORDER BY json_extract(payload,'$.coverage_start') DESC, json_extract(payload,'$.record_id') DESC) rn FROM r
   WHERE json_extract(payload,'$.coverage_start')<='2030-05-27' AND (json_extract(payload,'$.coverage_end') IS NULL OR json_extract(payload,'$.coverage_end')>='2030-05-27'))
SELECT coalesce(a.prod,'no_active_coverage') AS coverage_type, COUNT(*) AS patients FROM patients p LEFT JOIN act a ON a.patient=p.id AND a.rn=1
GROUP BY 1 ORDER BY 1;
