WITH lab_sup AS (SELECT supersedes AS id FROM records WHERE stream='labs' AND supersedes IS NOT NULL
              UNION SELECT json_extract(payload,'$.replaces_result_record_id') FROM records WHERE stream='labs' AND json_extract(payload,'$.replaces_result_record_id') IS NOT NULL),
g AS (
  SELECT r.patient, r.event AS d, CAST(json_extract(r.payload,'$.value') AS REAL) AS v
  FROM records r
  WHERE r.stream='labs' AND r.event IS NOT NULL AND (lower(json_extract(r.payload,'$.name')) IN ('egfr','egfr ckd-epi 2021','glomerular filtration rate') OR json_extract(r.payload,'$.loinc')='98979-8')
    AND typeof(json_extract(r.payload,'$.value')) IN ('integer','real')
    AND COALESCE(json_extract(r.payload,'$.result_status'),'') <> 'cancelled'
    AND r.id NOT IN (SELECT id FROM lab_sup)),
u AS (
  SELECT r.patient, r.event AS d, CAST(json_extract(r.payload,'$.value') AS REAL) AS v
  FROM records r
  WHERE r.stream='labs' AND r.event IS NOT NULL AND (lower(json_extract(r.payload,'$.name')) IN ('urine albumin/creatinine ratio','microalbumin/creatinine [mass ratio] in urine','microalb/creat ratio, random urine') OR json_extract(r.payload,'$.loinc')='9318-7')
    AND typeof(json_extract(r.payload,'$.value')) IN ('integer','real')
    AND COALESCE(json_extract(r.payload,'$.result_status'),'') <> 'cancelled'
    AND r.id NOT IN (SELECT id FROM lab_sup)),
dm AS (
  SELECT DISTINCT patient FROM (
    SELECT patient, upper(replace(json_extract(payload,'$.icd10'),'.','')) AS c FROM records WHERE stream='conditions'
    UNION ALL SELECT r.patient, upper(replace(json_extract(d.value,'$.icd10'),'.','')) FROM records r, json_each(r.payload,'$.diagnoses') d WHERE r.stream='encounters'
    UNION ALL SELECT r.patient, upper(replace(d.value,'.','')) FROM records r, json_each(r.payload,'$.diagnosis_codes') d WHERE r.stream IN ('payer_medical_claims','medical_claims')
  ) WHERE substr(c,1,3) IN ('E10','E11'))
SELECT COUNT(*) AS count FROM patients p WHERE p.id IN (SELECT patient FROM dm) AND (CAST(strftime('%Y','2029-12-31') AS INTEGER) - CAST(strftime('%Y',json_extract(p.payload,'$.birth_date')) AS INTEGER) - (strftime('%m-%d','2029-12-31') < strftime('%m-%d',json_extract(p.payload,'$.birth_date')))) BETWEEN 18 AND 85
  AND p.id IN (SELECT patient FROM g WHERE d BETWEEN '2029-01-01' AND '2029-12-31')
  AND p.id IN (SELECT patient FROM u WHERE d BETWEEN '2029-01-01' AND '2029-12-31')
