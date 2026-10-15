-- Dispensed fills in 2029 by drug class: pharmacy_dispenses status 'dispensed', event_date 2029-01-01..2029-12-31, one fill per fill_id (record_id when fill_id absent). Class by lower(medication_name): statin (simva/atorva/rosuva/prava/lova/pitava/fluvastatin), ace_inhibitor (lisinopril/enalapril/benazepril/quinapril/ramipril), arb (losartan/valsartan/irbesartan; sacubitril/valsartan counted as arb), loop_diuretic (furosemide/bumetanide/torsemide), beta_blocker (carvedilol/metoprolol/atenolol/bisoprolol/propranolol/labetalol), p2y12_inhibitor (clopidogrel/prasugrel/ticagrelor), metformin; first match in that order; other drugs excluded. Columns drug_class, fills.
WITH d AS MATERIALIZED (SELECT DISTINCT coalesce(json_extract(payload,'$.fill_id'),json_extract(payload,'$.record_id')) fid, lower(json_extract(payload,'$.medication_name')) m FROM records
  WHERE stream='pharmacy_dispenses' AND json_extract(payload,'$.status')='dispensed' AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31'),
c AS (SELECT fid, CASE
  WHEN m LIKE '%simvastatin%' OR m LIKE '%atorvastatin%' OR m LIKE '%rosuvastatin%' OR m LIKE '%pravastatin%' OR m LIKE '%lovastatin%' OR m LIKE '%pitavastatin%' OR m LIKE '%fluvastatin%' THEN 'statin'
  WHEN m LIKE '%lisinopril%' OR m LIKE '%enalapril%' OR m LIKE '%benazepril%' OR m LIKE '%quinapril%' OR m LIKE '%ramipril%' THEN 'ace_inhibitor'
  WHEN m LIKE '%losartan%' OR m LIKE '%valsartan%' OR m LIKE '%irbesartan%' THEN 'arb'
  WHEN m LIKE '%furosemide%' OR m LIKE '%bumetanide%' OR m LIKE '%torsemide%' THEN 'loop_diuretic'
  WHEN m LIKE '%carvedilol%' OR m LIKE '%metoprolol%' OR m LIKE '%atenolol%' OR m LIKE '%bisoprolol%' OR m LIKE '%propranolol%' OR m LIKE '%labetalol%' THEN 'beta_blocker'
  WHEN m LIKE '%clopidogrel%' OR m LIKE '%prasugrel%' OR m LIKE '%ticagrelor%' THEN 'p2y12_inhibitor'
  WHEN m LIKE '%metformin%' THEN 'metformin' END cls FROM d)
SELECT cls AS drug_class, COUNT(DISTINCT fid) AS fills FROM c WHERE cls IS NOT NULL GROUP BY cls ORDER BY cls;
