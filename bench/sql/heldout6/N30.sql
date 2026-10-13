-- Distinct claims with a 2027 clearinghouse acknowledgement 'rejected_for_member_identifier'.
WITH r AS MATERIALIZED (SELECT payload FROM records WHERE stream='claim_acknowledgements')
SELECT count(DISTINCT json_extract(payload,'$.claim_id')) AS count FROM r
WHERE json_extract(payload,'$.status')='rejected_for_member_identifier' AND json_extract(payload,'$.event_date') BETWEEN '2027-01-01' AND '2027-12-31';
