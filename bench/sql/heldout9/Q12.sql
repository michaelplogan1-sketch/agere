-- Claims rejected by the clearinghouse/payer front end in 2029: distinct claim_id with a claim_acknowledgements row status 'rejected_for_member_identifier' and event_date 2029-01-01..2029-12-31 (resubmissions of the same claim count once).
SELECT COUNT(DISTINCT json_extract(payload,'$.claim_id')) AS count FROM records
WHERE stream='claim_acknowledgements' AND json_extract(payload,'$.status')='rejected_for_member_identifier' AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31';
