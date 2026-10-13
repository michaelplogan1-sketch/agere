-- Clearinghouse front-end rejection rate: of ORIGINAL medical claim submissions (submission_kind='original') with event_date in 2028, the percent whose claim_acknowledgements record (matched on submission_record_id) has status 'rejected_for_member_identifier'. Corrected resubmissions excluded from the denominator. Rounded to 2 decimals.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('medical_claim_submissions','claim_acknowledgements')),
s AS (SELECT json_extract(payload,'$.record_id') rid FROM r WHERE stream='medical_claim_submissions' AND json_extract(payload,'$.submission_kind')='original' AND json_extract(payload,'$.event_date') BETWEEN '2028-01-01' AND '2028-12-31'),
rej AS (SELECT DISTINCT json_extract(payload,'$.submission_record_id') sid FROM r WHERE stream='claim_acknowledgements' AND json_extract(payload,'$.status')='rejected_for_member_identifier')
SELECT ROUND(100.0*SUM(rid IN (SELECT sid FROM rej))/COUNT(*),2) AS value FROM s;
