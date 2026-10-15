SELECT COUNT(DISTINCT json_extract(payload,'$.claim_id')) AS count FROM records WHERE stream='provider_remittance_advice'
AND json_extract(payload,'$.status')='denied_information_needed' AND event BETWEEN '2029-01-01' AND '2029-12-31'
