# Ordinary application integration

The existing warehouse launcher selected a fixed historical provider ledger and relabeled pending requests abandoned even during `--check`. It also did not initialize the explicitly requested subscription transport before enabling live app routes.

The launcher now accepts an explicit ledger, binds it before credentials and inference, initializes Claude subscription transport and refuses external Anthropic transport. Configuration inspection creates no ledger or build lock and loads no credentials or server. Normal startup preserves active request reservations in both selected and unrelated ledgers. Restored inspection-only builds retain their live-inference prohibition.

Validation: 24 focused authored engineering checks passed, including actual ledger byte-preservation checks, selection before key loading, transport failure/refusal, recovery protection and surrounding runner, subscription and total-cost accounting contracts. No provider or child-process attempt occurred. Receipt and log are local at `artifacts/agere-app-integration-2026-10-28/`.

Next qualify the existing ordinary app compose endpoint against a separate received-source installation, with fresh question state and provider ledger, exact source/version checks and end-to-end accounting. Existing warehouse ingestion is distinct from per-question cost. The previous independently accepted results and delivery remain preserved; no repeat clinician-review gate is added. Broader G3 and commercial release prerequisites remain open.
