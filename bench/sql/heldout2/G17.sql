-- Urgent care visits (types urgent_care/urgentcare, distinct encounter IDs) by month of event date, 2029-01-01..2029-08-15.
SELECT substr(event,1,7) AS month, COUNT(DISTINCT json_extract(payload,'$.encounter_id')) AS urgent_care_visits
FROM records WHERE stream='encounters' AND lower(json_extract(payload,'$.type')) IN ('urgent_care','urgentcare')
  AND event BETWEEN '2029-01-01' AND '2029-08-15'
GROUP BY 1 ORDER BY 1;
