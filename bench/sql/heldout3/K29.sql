-- Encounters (distinct encounter_id) whose records carry no event_date (visit date missing).
SELECT COUNT(*) AS count FROM (SELECT json_extract(payload,'$.encounter_id') id FROM records WHERE stream='encounters'
  GROUP BY 1 HAVING MAX(json_extract(payload,'$.event_date')) IS NULL);
