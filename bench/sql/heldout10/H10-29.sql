SELECT round(100.0 * sum(json_extract(payload,'$.read_back_verified') = 1) / count(*), 2) AS value FROM records WHERE stream='critical_result_notifications'
