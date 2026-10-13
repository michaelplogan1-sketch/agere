-- Distinct claim_ids with a member-identifier rejection acknowledgement dated 2028.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('claim_acknowledgements'))
SELECT COUNT(DISTINCT json_extract(payload,'$.claim_id')) AS count FROM r
WHERE json_extract(payload,'$.status')='rejected_for_member_identifier' AND json_extract(payload,'$.event_date') BETWEEN '2028-01-01' AND '2028-12-31';
