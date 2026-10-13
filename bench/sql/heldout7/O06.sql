-- Patients with ischemic heart disease / CAD / prior MI (condition description containing 'ischemic heart disease', 'myocardial infarction', 'coronary', 'STEMI', 'Non-ST elevation MI', 'preinfarction', or ICD-10 I20-I25) who have NO 'dispensed' antiplatelet (aspirin/ASA, clopidogrel, prasugrel, ticagrelor) dated 2028-08-16..2029-08-15.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('conditions','pharmacy_dispenses')),
cad AS (SELECT DISTINCT patient FROM (SELECT patient, lower(coalesce(json_extract(payload,'$.description'),'')) d, coalesce(json_extract(payload,'$.icd10'),'') i FROM r WHERE stream='conditions')
        WHERE d LIKE '%ischemic heart disease%' OR d LIKE '%myocardial infarction%' OR d LIKE '%coronary%' OR d LIKE 'stemi%' OR d LIKE 'non-st elevation mi%' OR d LIKE '%preinfarction%'
           OR i LIKE 'I20%' OR i LIKE 'I21%' OR i LIKE 'I22%' OR i LIKE 'I23%' OR i LIKE 'I24%' OR i LIKE 'I25%'),
ap AS (SELECT DISTINCT patient FROM (SELECT patient, lower(json_extract(payload,'$.medication_name')) m FROM r WHERE stream='pharmacy_dispenses' AND json_extract(payload,'$.status')='dispensed' AND json_extract(payload,'$.event_date') BETWEEN '2028-08-16' AND '2029-08-15')
       WHERE m LIKE '%aspirin%' OR m='asa' OR m LIKE '%clopidogrel%' OR m LIKE '%prasugrel%' OR m LIKE '%ticagrelor%')
SELECT COUNT(*) AS count FROM cad WHERE patient NOT IN (SELECT patient FROM ap);
