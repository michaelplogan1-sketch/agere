-- Pharmacy claims rejected as refill-too-soon in 2029: pharmacy_claim_transactions rows with transaction_status 'rejected' and reject_code 'REFILL_TOO_SOON', event_date 2029-01-01..2029-12-31, distinct claim_id.
SELECT COUNT(DISTINCT json_extract(payload,'$.claim_id')) AS count FROM records
WHERE stream='pharmacy_claim_transactions' AND json_extract(payload,'$.transaction_status')='rejected' AND json_extract(payload,'$.reject_code')='REFILL_TOO_SOON' AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31';
