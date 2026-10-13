-- Diagnostic: payer prior authorization. No prior-auth/precert records; billing_authorizations are all self-pay. Expect 0.
SELECT (SELECT COUNT(*) FROM records WHERE payload LIKE '%prior_auth%' OR payload LIKE '%prior auth%' OR payload LIKE '%preauth%' OR payload LIKE '%precert%')
     + (SELECT COUNT(*) FROM records WHERE stream='billing_authorizations' AND json_extract(payload,'$.payment_basis')<>'self_pay') AS prerequisite_available;
