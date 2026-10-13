-- Net contractual adjustments for medical claims with service_date in 2028: sum of financial_deltas_cents.contractual across all adjudication records (reversals net out), in dollars.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='medical_claims')
SELECT round(sum(json_extract(payload,'$.financial_deltas_cents.contractual'))/100.0,2) AS value FROM r WHERE json_extract(payload,'$.claim_id') IS NOT NULL AND json_extract(payload,'$.service_date') BETWEEN '2028-01-01' AND '2028-12-31';
