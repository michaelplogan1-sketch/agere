WITH ppc_sup AS (SELECT supersedes AS id FROM records WHERE stream='payer_pharmacy_claims' AND supersedes IS NOT NULL),
fills AS (
  SELECT patient, lower(json_extract(payload,'$.medication_name')) AS drug, event AS d, CAST(coalesce(json_extract(payload,'$.days_supply_reported'),1) AS INTEGER) AS ds
    FROM records WHERE stream='pharmacy_dispenses' AND json_extract(payload,'$.status')='dispensed' AND event IS NOT NULL
  UNION ALL
  SELECT patient, lower(json_extract(payload,'$.drug_name')), event, CAST(coalesce(json_extract(payload,'$.days_supply'),1) AS INTEGER)
    FROM records WHERE stream='payer_pharmacy_claims' AND json_extract(payload,'$.claim_status')='paid' AND event IS NOT NULL
      AND id NOT IN (SELECT id FROM ppc_sup))
SELECT COUNT(DISTINCT patient) AS count FROM fills WHERE d BETWEEN '2029-01-01' AND '2029-12-31' AND (drug LIKE '%extended release%' OR drug LIKE '%transdermal%') AND (drug LIKE '%hydrocodone%' OR drug LIKE '%oxycodone%' OR drug LIKE '%tramadol%' OR drug LIKE '%fentanyl%' OR drug LIKE '%morphine%' OR drug LIKE '%hydromorphone%' OR drug LIKE '%tapentadol%' OR drug LIKE '%oxymorphone%' OR drug LIKE '%methadone%')
