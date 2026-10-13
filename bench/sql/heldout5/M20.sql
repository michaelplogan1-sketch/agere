-- Roster patients with NO primary-care-type encounter (type primary_care, office, pediatric_office, wellness or 'Annual physical') dated in the trailing 12 months 2028-08-16..2029-08-15. Telephone, specialty, ED and urgent care do not count.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='encounters'),
seen AS (SELECT DISTINCT patient FROM r WHERE json_extract(payload,'$.type') IN ('primary_care','office','pediatric_office','wellness','Annual physical') AND json_extract(payload,'$.event_date') BETWEEN '2028-08-16' AND '2029-08-15')
SELECT COUNT(*) AS count FROM patients WHERE id NOT IN (SELECT patient FROM seen);
