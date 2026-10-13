-- AF patients with any oral anticoagulant AND any antiplatelet (clopidogrel/prasugrel/ticagrelor/aspirin incl. 'ASA') across medication streams.
WITH c AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='conditions'),
af AS (SELECT DISTINCT patient FROM c WHERE lower(coalesce(json_extract(payload,'$.description'),'')) LIKE '%atrial fibrillation%'
       OR lower(coalesce(json_extract(payload,'$.description'),'')) LIKE 'afib%' OR coalesce(json_extract(payload,'$.icd10'),'') LIKE 'I48%'),
m AS (SELECT patient, lower(coalesce(json_extract(payload,'$.medication_name'), json_extract(payload,'$.name'),'')) n FROM records
      WHERE stream IN ('medication_lists','medication_orders','pharmacy_dispenses','pharmacy_claims','pharmacy_history') AND patient IN (SELECT patient FROM af))
SELECT patient AS patient_id FROM af WHERE patient IN (SELECT patient FROM m WHERE (n LIKE '%warfarin%' OR n LIKE '%apixaban%' OR n LIKE '%rivaroxaban%' OR n LIKE '%dabigatran%' OR n LIKE '%edoxaban%' OR n LIKE '%eliquis%' OR n LIKE '%xarelto%' OR n LIKE '%coumadin%')) AND patient IN (SELECT patient FROM m WHERE (n LIKE '%clopidogrel%' OR n LIKE '%prasugrel%' OR n LIKE '%ticagrelor%' OR n LIKE '%aspirin%' OR n = 'asa' OR n LIKE 'asa %')) ORDER BY 1;
