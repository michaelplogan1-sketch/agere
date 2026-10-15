SELECT json_extract(payload,'$.reject_code') AS reject_code, COUNT(*) AS claims FROM records WHERE stream='payer_pharmacy_claims'
AND json_extract(payload,'$.claim_status')='rejected' AND event BETWEEN '2029-01-01' AND '2029-12-31' GROUP BY 1 ORDER BY 1
