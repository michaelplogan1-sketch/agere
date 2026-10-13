-- Diagnostic: CPT/HCPCS procedure codes and RVUs. Claims use service_code_system 'synthetic_local' only (e.g. professional_evaluation); no CPT/HCPCS code, RVU or wRVU field exists. Expect 0.
WITH r AS MATERIALIZED (SELECT payload FROM records WHERE stream IN ('medical_claims','medical_claim_lines','medical_claim_submissions'))
SELECT (SELECT COUNT(*) FROM r WHERE coalesce(json_extract(payload,'$.service_code_system'),'synthetic_local')<>'synthetic_local')
     + (SELECT COUNT(*) FROM records WHERE payload LIKE '%"cpt":%' OR payload LIKE '%"cpt_code%' OR payload LIKE '%"hcpcs%' OR payload LIKE '%"rvu%' OR payload LIKE '%"wrvu%' OR payload LIKE '%work_rvu%' OR payload LIKE '%relative value%') AS prerequisite_available;
