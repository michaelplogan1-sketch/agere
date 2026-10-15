WITH ppc_sup AS (SELECT supersedes AS id FROM records WHERE stream='payer_pharmacy_claims' AND supersedes IS NOT NULL),
fills AS (
  SELECT patient, lower(json_extract(payload,'$.medication_name')) AS drug, event AS d, CAST(coalesce(json_extract(payload,'$.days_supply_reported'),1) AS INTEGER) AS ds
    FROM records WHERE stream='pharmacy_dispenses' AND json_extract(payload,'$.status')='dispensed' AND event IS NOT NULL
  UNION ALL
  SELECT patient, lower(json_extract(payload,'$.drug_name')), event, CAST(coalesce(json_extract(payload,'$.days_supply'),1) AS INTEGER)
    FROM records WHERE stream='payer_pharmacy_claims' AND json_extract(payload,'$.claim_status')='paid' AND event IS NOT NULL
      AND id NOT IN (SELECT id FROM ppc_sup)),
f AS (SELECT patient, d, date(d, '+' || (max(ds,1) - 1) || ' days') e, drug FROM fills WHERE d BETWEEN '2029-01-01' AND '2029-12-31'),
o AS (SELECT * FROM f WHERE (drug LIKE '%warfarin%' OR drug LIKE '%apixaban%' OR drug LIKE '%rivaroxaban%' OR drug LIKE '%dabigatran%' OR drug LIKE '%edoxaban%')), b AS (SELECT * FROM f WHERE (drug LIKE '%clopidogrel%' OR drug LIKE '%prasugrel%' OR drug LIKE '%ticagrelor%'))
SELECT DISTINCT o.patient AS patient_id FROM o JOIN b ON b.patient=o.patient AND o.d <= b.e AND b.d <= o.e ORDER BY 1
