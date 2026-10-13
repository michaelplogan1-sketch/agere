-- medical_claims rows with status 'denied', service_date in 2028, by payer organization_id.
SELECT json_extract(payload,'$.organization_id') AS payer_id, COUNT(*) AS denied_claims
FROM records WHERE stream='medical_claims' AND json_extract(payload,'$.status')='denied'
  AND json_extract(payload,'$.service_date') BETWEEN '2028-01-01' AND '2028-12-31'
GROUP BY 1 ORDER BY 1;
