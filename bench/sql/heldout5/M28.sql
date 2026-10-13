-- Median days from service_date to the FIRST payer cash allocation (payer_cash_allocations event_date, matched on claim_id) for medical claims with service_date in 2028 that have at least one allocation. Median of per-claim day counts (average of the two middle values if even).
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('medical_claims','payer_cash_allocations')),
sd AS (SELECT json_extract(payload,'$.claim_id') cid, min(json_extract(payload,'$.service_date')) sd FROM r WHERE stream='medical_claims' AND json_extract(payload,'$.claim_id') IS NOT NULL GROUP BY 1),
al AS (SELECT json_extract(payload,'$.claim_id') cid, min(json_extract(payload,'$.event_date')) d FROM r WHERE stream='payer_cash_allocations' GROUP BY 1),
x AS (SELECT julianday(al.d)-julianday(sd.sd) days FROM sd JOIN al USING(cid) WHERE sd.sd BETWEEN '2028-01-01' AND '2028-12-31'),
o AS (SELECT days, ROW_NUMBER() OVER (ORDER BY days) rn, COUNT(*) OVER () n FROM x)
SELECT AVG(days) AS value FROM o WHERE rn IN ((n+1)/2, (n+2)/2);
