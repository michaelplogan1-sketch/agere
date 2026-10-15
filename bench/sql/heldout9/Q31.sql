-- Inpatient facility claims by MS-DRG title for discharges in 2029: distinct claim_id among medical_claims rows carrying ms_drg with discharge_date 2029-01-01..2029-12-31 and a non-reversed status (a claim reversed and re-adjudicated counts once). Columns drg_title, claims.
SELECT json_extract(payload,'$.title') AS drg_title, COUNT(DISTINCT json_extract(payload,'$.claim_id')) AS claims FROM records
WHERE stream='medical_claims' AND json_extract(payload,'$.ms_drg') IS NOT NULL AND json_extract(payload,'$.status')<>'reversed' AND json_extract(payload,'$.discharge_date') BETWEEN '2029-01-01' AND '2029-12-31'
GROUP BY 1 ORDER BY 1;
