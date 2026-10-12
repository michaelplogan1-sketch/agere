SELECT COUNT(*) AS count FROM records WHERE stream='claim_acknowledgements'
 AND json_extract(payload,'$.status')='rejected_for_member_identifier' AND event BETWEEN '2027-01-01' AND '2027-12-31';
