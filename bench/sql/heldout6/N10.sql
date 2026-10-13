-- Roster age_reported < 18 with an asthma diagnosis (description contains 'asthma' or ICD-10 J45.*).
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='conditions')
SELECT COUNT(DISTINCT r.patient) AS count FROM r JOIN patients p ON p.id=r.patient
WHERE json_extract(p.payload,'$.age_reported') < 18
  AND (lower(coalesce(json_extract(r.payload,'$.description'),'')) LIKE '%asthma%' OR coalesce(json_extract(r.payload,'$.icd10'),'') LIKE 'J45%');
