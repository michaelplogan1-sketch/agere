SELECT COUNT(*) AS prerequisite_available FROM records WHERE stream LIKE '%wearable%' OR stream LIKE '%activity%'
 OR stream LIKE '%accelerometer%' OR stream LIKE '%step_count%' OR stream LIKE '%device_reading%';
