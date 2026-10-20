# Warehouse interface delivery — 2026-10-20

The local app on :8885 now opens into a whole-warehouse explorer with a consistent visual system across Explore, Saved analyses and Action workspace. This is a delivered G3 workflow increment. G3 remains active; G5 remains blocked; complete stack qualification is not claimed.

## User-visible changes

- One prominent question box with explicit whole-warehouse or saved-population scope. Editable examples span operations, patients, costs and outcomes; they do not limit the planner.
- Overview, searchable Data and Method & sources tabs, with existing charts, CSV exports, warnings and interpretation details preserved.
- A searchable saved-analysis library reopens full results. The data library shows the real received-source inventory and supports source search.
- The matching action workspace offers general review work, cohort exploration and synthetic deliverables. PharmD/RPM remain options; custom follow-up is the general default.
- Responsive navigation, readable secondary text, keyboard controls, visible pending/error states and reduced-motion support. No new service or frontend dependency.

## Functional correction

Previously a reopened or earlier saved answer could have no reliable follow-up context, or inherit the latest conversation turn. The compose contract now accepts one validated server-owned answer identity, or an explicit warehouse scope. Saved follow-ups resolve current immutable membership; invalid or conflicting identities fail closed. Resizing no longer resets navigation or query scope.

## Evidence

88 focused regression tests passed, including new exact-selected-context and invalid-scope regressions. JavaScript syntax and git whitespace checks passed. Browser checks covered draft-only examples, a new operations answer, result searching, method details, saved search/reopen, combined financial/outcome rendering, source discovery and synthetic review setup. At 390px, home and action workspace have no horizontal overflow. No console errors were observed.

The live service returned 10 for a selected saved cohort and 4,115 for explicit whole warehouse; the appointment query returned nine practice groups. The inventory reported 237 source categories and 2,396,490 received records. These are finite workflow checks, not a benchmark or a general accuracy claim. No protected evaluation was run. This increment used deterministic questions and reopened existing answers; no new frontier calls were required.

Source commit: 27e1d63940e226366f584ae53eb9a45812a482f7. Runtime: /Users/mikel/agere-mvp-run, port 8885, hosted planning enabled. Recovery commit: 48c39e3102631a230ceb6ed61d43ea9cfef96186. Active warehouse overlays, source data, saved work and concurrent files were preserved.

Evidence: artifacts/agere-interface-v1/implementation-receipt.json, live-check.json, browser-check.json, tests.log and screenshots.

## Design references

Reviewed [Hex Threads](https://hex.tech/product/threads/), [Linear's design refresh](https://linear.app/now/behind-the-latest-design-refresh) and [Evidence on GitHub](https://github.com/evidence-dev/evidence). Used their question-to-analysis, navigation and data-presentation patterns as references; the implementation is original and reuses Agere's existing analytical and work APIs.

## Remaining work

The product scope remains all supported questions across the warehouse. This interface delivery does not establish that every question can already be answered correctly. Continue closing shared clinical/time/evidence interpretation gaps across claims, admissions, access, providers and contracts, with constraint mutations and independent checks. Do not add a separate handler for each question or treat HF as the product boundary. Synthetic actions and deliverables remain demonstrations.
