-- % of 2028-service claim adjudication records arriving > 30 days after service_date.
WITH m AS (SELECT arrival, json_extract(payload,'$.service_date') sd FROM records WHERE stream='medical_claims' AND json_extract(payload,'$.claim_id') IS NOT NULL)
SELECT round(100.0*SUM(julianday(arrival)-julianday(sd) > 30)/COUNT(*),1) AS value FROM m WHERE sd BETWEEN '2028-01-01' AND '2028-12-31';
