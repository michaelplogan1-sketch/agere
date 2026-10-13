-- Diagnostic: work RVU / productivity / compensation data for clinicians. Claims use synthetic local service codes with no RVU weights;
-- no RVU, CPT-weight or clinician compensation field exists.
SELECT (SELECT COUNT(*) FROM records WHERE payload LIKE '%rvu%' OR payload LIKE '%relative_value%' OR payload LIKE '%compensation%' OR payload LIKE '%salary%')
     + (SELECT COUNT(*) FROM records WHERE stream='medical_claims' AND json_extract(payload,'$.service_code_system') IS NOT NULL
          AND json_extract(payload,'$.service_code_system') <> 'synthetic_local') AS prerequisite_available;
