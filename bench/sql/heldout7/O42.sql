-- Among patients with a 2028 'brief_clinical_needs_interview' social screen, % whose latest such interview reports food_difficulty_reported = true. The 2026 social screens (transport/education/difficulty paying) did not ask about food and are excluded from the denominator.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='social_screens'),
s AS (SELECT patient, json_extract(payload,'$.food_difficulty_reported') f, row_number() OVER (PARTITION BY patient ORDER BY json_extract(payload,'$.event_date') DESC, json_extract(payload,'$.available_time') DESC) rn
      FROM r WHERE json_extract(payload,'$.instrument')='brief_clinical_needs_interview')
SELECT round(100.0*sum(f=1)/count(*),1) AS value FROM s WHERE rn=1;
