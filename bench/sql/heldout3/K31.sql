-- Condition records whose onset is not a complete YYYY-MM-DD date: missing onset, year-only or year-month, or free text ('today', 'childhood', 'HD2', ...).
SELECT COUNT(*) AS count FROM records WHERE stream='conditions'
  AND (json_extract(payload,'$.onset') IS NULL OR json_extract(payload,'$.onset') NOT GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]');
