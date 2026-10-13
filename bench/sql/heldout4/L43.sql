-- Transport barrier (either instrument) AND any no-show appointment.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('social_screens','appointments')),
tb AS (SELECT DISTINCT patient FROM r WHERE stream='social_screens' AND (json_extract(payload,'$.transport_difficulty_reported')=1 OR json_extract(payload,'$.transport')='unreliable')),
ns AS (SELECT DISTINCT patient FROM r WHERE stream='appointments' AND json_extract(payload,'$.status')='no_show')
SELECT COUNT(*) AS count FROM tb WHERE patient IN (SELECT patient FROM ns);
