-- Urgent care visits per month, January-June 2029. Types 'urgent_care' and 'urgentcare'; distinct encounter_id; month of event_date.
-- Months with zero visits would be shown as absent.
WITH e AS (SELECT patient, json_extract(payload,'$.encounter_id') id, MIN(json_extract(payload,'$.type')) t, MIN(json_extract(payload,'$.event_date')) d
  FROM records WHERE stream='encounters' GROUP BY patient, json_extract(payload,'$.encounter_id'))
SELECT substr(d,1,7) AS month, COUNT(*) AS visits FROM e
WHERE t IN ('urgent_care','urgentcare') AND d BETWEEN '2029-01-01' AND '2029-06-30'
GROUP BY 1 ORDER BY 1;
