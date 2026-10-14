-- Original-roster reading grouped by MS-DRG code: medical_claims rows carrying ms_drg, non-'reversed', discharge_date in 2029,
-- distinct claim_id. Columns ms_drg, claims.
SELECT json_extract(payload,'$.ms_drg') AS ms_drg, COUNT(DISTINCT json_extract(payload,'$.claim_id')) AS claims FROM records
WHERE stream='medical_claims' AND json_extract(payload,'$.ms_drg') IS NOT NULL AND json_extract(payload,'$.status')<>'reversed' AND json_extract(payload,'$.discharge_date') BETWEEN '2029-01-01' AND '2029-12-31'
GROUP BY 1 ORDER BY 1;
