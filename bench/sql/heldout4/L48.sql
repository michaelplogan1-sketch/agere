-- Diagnostic: RVU / relative value units / CPT codes. Claims use service_code_system 'synthetic_local' only.
SELECT (SELECT COUNT(*) FROM records WHERE payload LIKE '%rvu%' OR payload LIKE '%relative_value%' OR payload LIKE '%"cpt":%' OR payload LIKE '%cpt_code%' OR payload LIKE '%hcpcs%')
     + (SELECT COUNT(*) FROM records WHERE stream IN ('medical_claims','medical_claim_lines') AND json_extract(payload,'$.service_code_system') <> 'synthetic_local') AS prerequisite_available;
