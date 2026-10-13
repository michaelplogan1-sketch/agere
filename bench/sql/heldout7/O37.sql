-- % of lab result records (labs stream, every received row incl. cancelled) that lack an event_date (collection date unknown).
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='labs')
SELECT round(100.0*sum(json_extract(payload,'$.event_date') IS NULL)/count(*),1) AS value FROM r;
