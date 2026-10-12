SELECT COUNT(*) AS prerequisite_available FROM records WHERE stream LIKE '%risk_score%' OR stream LIKE '%prediction%'
 OR stream LIKE '%model_validation%' OR stream LIKE '%calibration%';
