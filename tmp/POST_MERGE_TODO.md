# After merging heldout9 fix branches A/B/C/D (2026-10-14)
Branches: A worktree-agent-af09b64f5586603d7 (157906c, semantic-layer/16) · B worktree-agent-ab5bd027f3cf88a6e (4daff44)
          C worktree-agent-a138a17b7e58adb00 (6d957e9, d72cf65) · D worktree-agent-a786bf21b064215de (pending)
Merge order: A → C → B → D. Keep A's semantic_layer.SCHEMA_DOC; C's edits are nl2sql TABLES/VERIFY_RULES/EXAMPLES only.
1. B's local_compiler: import MEASURE_ASKED / PLAIN_COUNT / plain_count_question from metric_router (drop B's copy).
2. Rebuild semantic layer (layer/16) on v2.8 build (+ v1.23/v2.0 stacks used by the compiler-rule accept gate), re-run v2.8 verification chain (code identity changed).
3. compiler_rules via the accept gate (not by hand):
   - population_lookups.json:61  evidence IN ('dispensed', 'claim') -> picked_up = 1 ; text "dispensed or claimed" -> "picked up"
   - operations_lookups.json:123 m.evidence IN ('dispensed', 'claim') -> m.picked_up = 1
   - .venv/bin/python tools/compiler_growth/accept.py config/agere/compiler_rules/<file> --write
4. config/agere/metric_vocabulary.json medication_evidence.dispensed -> m.picked_up = 1, after re-checking family reference results on a layer/16 stack.
5. Full test suite; learned-router retrain if routing labels changed; acceptance + smoke; heldout9 (with D's corrected refs) + fresh blind set; sealed v2.8 truth scorer (tools/eval_predictions.py); ablations on v2.8.
6. Sealed scorer on the v2.8 SAMPLE (patient-scoped), after activation:
   .venv/bin/python tools/eval_predictions.py --build artifacts/agere-healthcare-v28-sample-0.30 --out artifacts/eval-v28-sample/predictions
   cd /Users/mikel/synthetic-patient-ecosystem && python3 -m ecosystem.eval_scorer --release releases/healthcare-warehouse-v2.8 \
     --predictions /Users/mikel/clinical-insights-local/artifacts/eval-v28-sample/predictions \
     --sample-release /Users/mikel/clinical-insights-local/artifacts/healthcare-warehouse-v2.8-sample-0.30 --out /Users/mikel/clinical-insights-local/artifacts/eval-v28-sample/scores.json
   Baseline on a 30% sample ≈ full: conditions P .90/R .98, HCC P .91/R .83, inpatient P .93/R .87.
7. Merge Wave 1 branch worktree-agent-aca6f84ceb094cb24 (d195b42: gate certification + evidence-type head) AFTER the v2.8 build;
   then `.venv/bin/python tools/certify_gates.py --write` (route certs are bound to source digests); take its calibrated_thresholds.json on conflict;
   tests expecting only verified/low need 'unverified'. Evidence types cover the cohort path only (not compiler_rules SQL yet).
8. Merge Wave 1 branch worktree-agent-aab4667c38b5c4ed5 (release-diff guard + verified carry-forward) when it reports.
9. Merge UltraBERT second encoder branch worktree-agent-ae0054a9d7a476085 (0edd958) after heldout10 finishes; add server warm-up call local_encoders.second_encoder().
