-- Roster patients aged 18+ (age_reported) with NO primary_care, office or pediatric_office encounter (any status, distinct by encounter) at a WH-PRACTICE-* org dated 2028-08-16..2029-08-15. Telephone encounters do not count.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='encounters'),
seen AS (SELECT DISTINCT patient FROM r WHERE json_extract(payload,'$.organization_id') LIKE 'WH-PRACTICE-%' AND json_extract(payload,'$.type') IN ('primary_care','office','pediatric_office')
           AND json_extract(payload,'$.event_date') BETWEEN '2028-08-16' AND '2029-08-15')
SELECT COUNT(*) AS count FROM patients p WHERE json_extract(p.payload,'$.age_reported')>=18 AND p.id NOT IN (SELECT patient FROM seen);
