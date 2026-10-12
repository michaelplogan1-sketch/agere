WITH hf AS MATERIALIZED (
 SELECT DISTINCT patient FROM records WHERE stream='conditions' AND (
 substr(json_extract(payload,'$.icd10'),1,3)='I50' OR json_extract(payload,'$.snomed')='88805009')
), transport AS MATERIALIZED (
 SELECT DISTINCT patient FROM records WHERE stream='social_screens' AND json_type(payload,'$.transport_difficulty_reported')='true'
) SELECT COUNT(*) AS count FROM hf WHERE patient IN (SELECT patient FROM transport);
