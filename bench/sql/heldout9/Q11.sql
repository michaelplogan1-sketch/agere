-- Medical claims reversed and re-adjudicated (payer repricing) in 2029: distinct claim_id having a medical_claims row with status 'reversed' whose event_date is 2029-01-01..2029-12-31.
SELECT COUNT(DISTINCT json_extract(payload,'$.claim_id')) AS count FROM records
WHERE stream='medical_claims' AND json_extract(payload,'$.status')='reversed' AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31';
