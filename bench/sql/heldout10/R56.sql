WITH d AS (
  SELECT patient FROM (SELECT * FROM records WHERE stream='conditions' AND id NOT IN (SELECT supersedes FROM records WHERE stream='conditions' AND supersedes IS NOT NULL))
  WHERE upper(replace(json_extract(payload,'$.icd10'), '.', '')) LIKE 'F32%' OR upper(replace(json_extract(payload,'$.icd10'), '.', '')) LIKE 'F33%'
     OR (json_extract(payload,'$.icd10') IS NULL AND lower(json_extract(payload,'$.description')) LIKE '%depress%'
         AND lower(json_extract(payload,'$.description')) NOT LIKE '%postpartum%' AND lower(json_extract(payload,'$.description')) NOT LIKE '%bipolar%')
  UNION
  SELECT r.patient FROM records r, json_each(r.payload, '$.diagnoses') x
  WHERE r.stream = 'encounters' AND (upper(replace(json_extract(x.value, '$.icd10'), '.', '')) LIKE 'F32%'
                                  OR upper(replace(json_extract(x.value, '$.icd10'), '.', '')) LIKE 'F33%')
  UNION
  SELECT patient FROM records WHERE stream = 'adt_messages' AND (
       upper(replace(json_extract(payload,'$.admit_diagnosis.code'), '.', '')) LIKE 'F32%' OR upper(replace(json_extract(payload,'$.admit_diagnosis.code'), '.', '')) LIKE 'F33%'
    OR upper(replace(json_extract(payload,'$.discharge_diagnosis.code'), '.', '')) LIKE 'F32%' OR upper(replace(json_extract(payload,'$.discharge_diagnosis.code'), '.', '')) LIKE 'F33%')
  UNION
  SELECT c.patient FROM (SELECT * FROM records WHERE stream='payer_medical_claims' AND id NOT IN (SELECT supersedes FROM records WHERE stream='payer_medical_claims' AND supersedes IS NOT NULL)) c, json_each(c.payload, '$.diagnosis_codes') x
  WHERE upper(replace(x.value, '.', '')) LIKE 'F32%' OR upper(replace(x.value, '.', '')) LIKE 'F33%'
)
SELECT COUNT(DISTINCT patient) AS count FROM d
