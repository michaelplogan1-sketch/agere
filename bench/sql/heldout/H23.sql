SELECT json_extract(payload,'$.status') AS status,COUNT(*) AS claim_record_count FROM records
 WHERE stream='medical_claims' AND json_extract(payload,'$.service_date') BETWEEN '2029-01-01' AND '2029-12-31'
 AND json_extract(payload,'$.status') IS NOT NULL GROUP BY 1 ORDER BY 1;
