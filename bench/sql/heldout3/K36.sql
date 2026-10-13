-- Diagnostic: patient-experience survey data (CAHPS/HCAHPS, Press Ganey, satisfaction scores, NPS). No such stream or field exists.
SELECT (SELECT COUNT(*) FROM records WHERE stream LIKE '%survey%' OR stream LIKE '%experience%' OR stream LIKE '%satisfaction%')
     + (SELECT COUNT(*) FROM records WHERE payload LIKE '%cahps%' OR payload LIKE '%press ganey%' OR payload LIKE '%satisfaction_score%'
          OR payload LIKE '%patient_experience%' OR payload LIKE '%net_promoter%' OR payload LIKE '%likelihood_to_recommend%') AS prerequisite_available;
