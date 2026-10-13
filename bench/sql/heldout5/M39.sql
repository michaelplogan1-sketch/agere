-- Feed reconciliation gap: distinct claim_ids that have at least one provider_remittance_advice (835-style) record but NO payer adjudication record (medical_claims row with that claim_id) received by the as-of date.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('provider_remittance_advice','medical_claims')),
adj AS MATERIALIZED (SELECT DISTINCT json_extract(payload,'$.claim_id') cid FROM r WHERE stream='medical_claims' AND json_extract(payload,'$.claim_id') IS NOT NULL),
ra AS MATERIALIZED (SELECT DISTINCT json_extract(payload,'$.claim_id') cid FROM r WHERE stream='provider_remittance_advice')
SELECT COUNT(*) AS count FROM ra WHERE cid NOT IN (SELECT cid FROM adj);
