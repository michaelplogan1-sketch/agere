-- Net pharmacy payer payments for 2028: sum of payer_paid_delta_cents over pharmacy_claim_transactions with event_date in 2028 (paid positive, reversed negative, rejected zero), in dollars. The separate pharmacy_claims (legacy 2026) stream is outside the window.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='pharmacy_claim_transactions')
SELECT round(sum(json_extract(payload,'$.payer_paid_delta_cents'))/100.0,2) AS value FROM r WHERE json_extract(payload,'$.event_date') BETWEEN '2028-01-01' AND '2028-12-31';
