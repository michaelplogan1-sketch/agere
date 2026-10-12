SELECT COUNT(*) AS prerequisite_available FROM records WHERE stream LIKE '%causal%' OR stream LIKE '%randomiz%'
 OR stream LIKE '%trial_arm%' OR stream LIKE '%treatment_assignment%' OR stream LIKE '%program_evaluation%';
