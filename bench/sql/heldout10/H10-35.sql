WITH ppc_sup AS (SELECT supersedes AS id FROM records WHERE stream='payer_pharmacy_claims' AND supersedes IS NOT NULL),
fills AS (
  SELECT patient, lower(json_extract(payload,'$.medication_name')) AS drug, event AS d, CAST(coalesce(json_extract(payload,'$.days_supply_reported'),1) AS INTEGER) AS ds
    FROM records WHERE stream='pharmacy_dispenses' AND json_extract(payload,'$.status')='dispensed' AND event IS NOT NULL
  UNION ALL
  SELECT patient, lower(json_extract(payload,'$.drug_name')), event, CAST(coalesce(json_extract(payload,'$.days_supply'),1) AS INTEGER)
    FROM records WHERE stream='payer_pharmacy_claims' AND json_extract(payload,'$.claim_status')='paid' AND event IS NOT NULL
      AND id NOT IN (SELECT id FROM ppc_sup)),
f AS (SELECT * FROM fills WHERE d BETWEEN '2029-01-01' AND '2029-12-31'),
c AS (
  SELECT 'statin' AS drug_class, patient FROM f WHERE (drug LIKE '%atorvastatin%' OR drug LIKE '%rosuvastatin%' OR drug LIKE '%simvastatin%' OR drug LIKE '%pravastatin%' OR drug LIKE '%lovastatin%' OR drug LIKE '%pitavastatin%' OR drug LIKE '%fluvastatin%') UNION
  SELECT 'metformin' AS drug_class, patient FROM f WHERE (drug LIKE '%metformin%') UNION
  SELECT 'sglt2_inhibitor' AS drug_class, patient FROM f WHERE (drug LIKE '%empagliflozin%' OR drug LIKE '%dapagliflozin%' OR drug LIKE '%canagliflozin%' OR drug LIKE '%ertugliflozin%') UNION
  SELECT 'glp1_agonist' AS drug_class, patient FROM f WHERE (drug LIKE '%semaglutide%' OR drug LIKE '%liraglutide%' OR drug LIKE '%dulaglutide%' OR drug LIKE '%exenatide%' OR drug LIKE '%tirzepatide%') UNION
  SELECT 'ace_inhibitor_or_arb' AS drug_class, patient FROM f WHERE (drug LIKE '%lisinopril%' OR drug LIKE '%enalapril%' OR drug LIKE '%ramipril%' OR drug LIKE '%benazepril%' OR drug LIKE '%quinapril%' OR drug LIKE '%captopril%' OR drug LIKE '%fosinopril%' OR drug LIKE '%perindopril%' OR drug LIKE '%losartan%' OR drug LIKE '%valsartan%' OR drug LIKE '%irbesartan%' OR drug LIKE '%olmesartan%' OR drug LIKE '%candesartan%' OR drug LIKE '%telmisartan%'))
SELECT drug_class, COUNT(DISTINCT patient) AS patients FROM c GROUP BY drug_class ORDER BY drug_class
