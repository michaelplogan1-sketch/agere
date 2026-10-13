-- Late-arriving primary care practice encounters: per practice (encounter organization_id WH-PRACTICE-*), the number of distinct encounters
-- whose FIRST received record arrived more than 30 days after the encounter event_date. Undated encounters excluded.
WITH e AS (SELECT json_extract(payload,'$.encounter_id') id, MIN(json_extract(payload,'$.event_date')) d,
                  MIN(json_extract(payload,'$.organization_id')) org, MIN(arrival) a
           FROM records WHERE stream='encounters' GROUP BY 1)
SELECT org AS practice, COUNT(*) AS late_encounters FROM e
WHERE org LIKE 'WH-PRACTICE-%' AND d IS NOT NULL AND julianday(a) - julianday(d) > 30
GROUP BY 1 ORDER BY 1;
