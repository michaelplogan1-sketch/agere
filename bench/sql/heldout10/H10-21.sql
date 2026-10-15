SELECT COUNT(*) AS count FROM records s JOIN records o ON o.id=s.supersedes
WHERE s.stream='payer_medical_claims' AND o.stream='payer_medical_claims'
  AND json_extract(s.payload,'$.claim_status')='paid' AND json_extract(o.payload,'$.claim_status')='denied'
  AND json_extract(s.payload,'$.paid_date') BETWEEN '2029-01-01' AND '2029-12-31'
