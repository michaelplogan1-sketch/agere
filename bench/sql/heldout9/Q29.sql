-- Patients by coverage type: one coverage row per patient (coverage stream, registration snapshot 2026-08-28/29); count distinct patients by coverage_type. Columns coverage_type, patients.
SELECT json_extract(payload,'$.coverage_type') AS coverage_type, COUNT(DISTINCT patient) AS patients FROM records WHERE stream='coverage' GROUP BY 1 ORDER BY 1;
