-- Diagnostic: HCC / RAF risk adjustment scores. No such field exists.
SELECT (SELECT COUNT(*) FROM records WHERE payload LIKE '%risk_score%' OR payload LIKE '%raf_score%' OR payload LIKE '%"raf":%' OR payload LIKE '%hcc%' OR payload LIKE '%risk_adjust%')
     + (SELECT COUNT(*) FROM records WHERE stream='medical_claims' AND (payload LIKE '%icd10%' OR payload LIKE '%diagnosis_code%')) AS prerequisite_available;
