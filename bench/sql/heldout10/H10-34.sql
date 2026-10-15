WITH RECURSIVE ppc_sup AS (SELECT supersedes AS id FROM records WHERE stream='payer_pharmacy_claims' AND supersedes IS NOT NULL),
fills AS (
  SELECT patient, lower(json_extract(payload,'$.medication_name')) AS drug, event AS d, CAST(coalesce(json_extract(payload,'$.days_supply_reported'),1) AS INTEGER) AS ds
    FROM records WHERE stream='pharmacy_dispenses' AND json_extract(payload,'$.status')='dispensed' AND event IS NOT NULL
  UNION ALL
  SELECT patient, lower(json_extract(payload,'$.drug_name')), event, CAST(coalesce(json_extract(payload,'$.days_supply'),1) AS INTEGER)
    FROM records WHERE stream='payer_pharmacy_claims' AND json_extract(payload,'$.claim_status')='paid' AND event IS NOT NULL
      AND id NOT IN (SELECT id FROM ppc_sup)),
s AS (SELECT patient, d, ds FROM fills WHERE d BETWEEN '2029-01-01' AND '2029-12-31' AND (drug LIKE '%atorvastatin%' OR drug LIKE '%rosuvastatin%' OR drug LIKE '%simvastatin%' OR drug LIKE '%pravastatin%' OR drug LIKE '%lovastatin%' OR drug LIKE '%pitavastatin%' OR drug LIKE '%fluvastatin%')),
el AS (SELECT patient, min(d) f FROM s GROUP BY patient HAVING count(DISTINCT d) >= 2),
days(patient, day, last) AS (
  SELECT s.patient, s.d, date(s.d, '+' || (max(s.ds,1) - 1) || ' days') FROM s JOIN el ON el.patient=s.patient
  UNION ALL SELECT patient, date(day, '+1 day'), last FROM days WHERE day < last AND day < '2029-12-31'),
cov AS (SELECT d.patient, COUNT(DISTINCT d.day) c FROM days d JOIN el ON el.patient=d.patient WHERE d.day BETWEEN el.f AND '2029-12-31' GROUP BY d.patient)
SELECT COUNT(*) AS count FROM el JOIN cov ON cov.patient=el.patient WHERE 1.0 * cov.c / (julianday('2029-12-31') - julianday(el.f) + 1) >= 0.8
