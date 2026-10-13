-- Depressive disorder: ICD-10 F32.*, F33.*, F34.1, F53.0; SNOMED 370143000/35489007; free text depress*/MDD. Bipolar excluded.
SELECT COUNT(DISTINCT patient) AS count FROM records
WHERE stream='conditions' AND (
  substr(json_extract(payload,'$.icd10'),1,3) IN ('F32','F33')
  OR json_extract(payload,'$.icd10') IN ('F34.1','F53.0')
  OR json_extract(payload,'$.snomed') IN ('370143000','35489007')
  OR lower(json_extract(payload,'$.description')) LIKE '%depress%'
  OR json_extract(payload,'$.description') LIKE 'MDD%')
  AND lower(COALESCE(json_extract(payload,'$.description'),'')) NOT LIKE '%bipolar%';
