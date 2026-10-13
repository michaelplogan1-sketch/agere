-- Claim adjudication feed timeliness by adjudication year (medical_claims event_date year): records, records whose available_date is more than 30 days after event_date, and mean lag days (available_date - event_date). Columns year, records, late_over_30d, avg_lag_days.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='medical_claims')
SELECT CAST(substr(json_extract(payload,'$.event_date'),1,4) AS INTEGER) AS year, count(*) AS records,
       sum(julianday(json_extract(payload,'$.available_date'))-julianday(json_extract(payload,'$.event_date'))>30) AS late_over_30d,
       round(avg(julianday(json_extract(payload,'$.available_date'))-julianday(json_extract(payload,'$.event_date'))),1) AS avg_lag_days
FROM r GROUP BY 1 ORDER BY 1;
