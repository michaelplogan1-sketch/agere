-- Patients with depression (ICD-10 F32.*/F33.*/F53.0, SNOMED 370143000, description containing 'depress' or 'MDD'), anxiety (F41.*, 'anxiety', 'GAD', 'panic'), PTSD (F43.1*, 'PTSD'), bipolar (F31.*, 'bipolar') or schizophrenia (F20.*, 'schizophrenia'). OCD, ADHD and substance use are not included.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='conditions'),
c AS (SELECT patient, coalesce(json_extract(payload,'$.description'),'') d, coalesce(json_extract(payload,'$.icd10'),'') i, coalesce(json_extract(payload,'$.snomed'),'') s FROM r)
SELECT COUNT(DISTINCT patient) AS count FROM c WHERE substr(i,1,3) IN ('F32','F33','F41','F31','F20') OR i='F53.0' OR i LIKE 'F43.1%' OR s='370143000'
  OR lower(d) LIKE '%depress%' OR d LIKE '%MDD%' OR lower(d) LIKE '%anxiety%' OR d LIKE '%GAD%' OR lower(d) LIKE '%panic%' OR d LIKE '%PTSD%' OR lower(d) LIKE '%bipolar%' OR lower(d) LIKE '%schizophrenia%';
