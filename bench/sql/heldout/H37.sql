SELECT COUNT(*) AS prerequisite_available FROM records WHERE stream LIKE '%capitation%' OR stream LIKE '%contract%'
 OR stream LIKE '%premium%' OR stream LIKE '%enrollment_month%' OR stream LIKE '%member_month%';
