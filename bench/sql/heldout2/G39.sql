-- Diagnostic: any patient-experience survey source (CAHPS/HCAHPS, satisfaction, NPS).
SELECT (SELECT COUNT(*) FROM records WHERE stream LIKE '%survey%' OR stream LIKE '%satisfaction%' OR stream LIKE '%experience%')
     + (SELECT COUNT(*) FROM records WHERE lower(payload) LIKE '%cahps%' OR lower(payload) LIKE '%satisfaction%' OR lower(payload) LIKE '%net promoter%') AS prerequisite_available;
