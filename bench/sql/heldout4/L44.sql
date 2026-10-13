-- Per coverage type: screened patients, strained (either instrument = 1), pct.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('social_screens','financial_screens','coverage')), cov AS (SELECT patient, json_extract(payload,'$.coverage_type') ctype, json_extract(payload,'$.organization_id') payer FROM r WHERE stream='coverage'),
s AS (SELECT patient, json_extract(payload,'$.difficulty_paying_for_care_reported') v FROM r WHERE stream='social_screens' AND json_extract(payload,'$.difficulty_paying_for_care_reported') IS NOT NULL
      UNION ALL SELECT patient, json_extract(payload,'$.difficulty_paying_medical_bills') FROM r WHERE stream='financial_screens' AND json_extract(payload,'$.difficulty_paying_medical_bills') IS NOT NULL),
p AS (SELECT patient, MAX(v=1) strained FROM s GROUP BY patient)
SELECT cov.ctype AS coverage_type, COUNT(*) AS screened, SUM(p.strained) AS strained, round(100.0*SUM(p.strained)/COUNT(*),1) AS strained_pct
FROM p JOIN cov ON cov.patient=p.patient GROUP BY cov.ctype ORDER BY coverage_type;
