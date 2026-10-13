-- Men (sex_reported M) aged 50-75 inclusive by LATEST reported age (latest demographic_updates age, else roster age) with ANY LDL result >= 190 mg/dL (inclusive) ever received, dated or undated (names 'LDL cholesterol' or 'LDL'; numeric, not cancelled).
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('labs','demographic_updates')),
ldl AS (SELECT DISTINCT patient FROM r WHERE stream='labs' AND json_extract(payload,'$.name') IN ('LDL cholesterol','LDL')
        AND typeof(json_extract(payload,'$.value')) IN ('integer','real') AND json_extract(payload,'$.value')>=190
        AND coalesce(json_extract(payload,'$.result_status'),'final')<>'cancelled'),
du AS (SELECT patient, json_extract(payload,'$.age_reported') a, json_extract(payload,'$.event_date') ed FROM r WHERE stream='demographic_updates'),
age AS (SELECT p.id patient, json_extract(p.payload,'$.sex_reported') sex,
          coalesce((SELECT a FROM du WHERE du.patient=p.id ORDER BY ed DESC LIMIT 1), json_extract(p.payload,'$.age_reported')) a FROM patients p)
SELECT COUNT(*) AS count FROM age WHERE sex='M' AND a BETWEEN 50 AND 75 AND patient IN (SELECT patient FROM ldl);
