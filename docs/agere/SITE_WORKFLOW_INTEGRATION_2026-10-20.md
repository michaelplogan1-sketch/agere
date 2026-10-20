# Connected Agere website — October 20, 2026

G3 remains active; G5 remains blocked and full ten-area acceptance remains false.

The designed website now runs at http://127.0.0.1:8885/site/ on the same origin as the existing Agere API. An owned copy of design commit `9cf1febd08551d886d578f68ba8b6aaa3560f33f` is in `web/agere-site`; its packaged build is in `clinical_core/agere/site_dist`. The active design checkout is unchanged and its hosted preview remains separate. No public audience, hosting identity, credentials, service or deployment was added.

The live form submits the original question through `/api/stack/compose`, displays actual warehouse counts and source date, preserves questions on failure, distinguishes partial answers, and opens the same saved evidence, brief and manual follow-up workflow. A saved URL reloads by GET rather than resubmitting. Learning counts and review links use the existing learning API. Historical hero figures and care/reward journeys are explicitly illustrative.

## Measured checks

- Build succeeded using the existing pinned design dependencies; no runtime Node dependency was added. The inherited large Three.js chunk warning remains.
- All 79 relevant tests passed on the working copy and independently frozen `da83c23` source: site traversal/symlink checks, question UI, workflow creation/retry, synthetic actions, frozen lookup and document-title regressions. These are development checks, not a qualification score.
- Actual browser submission in an isolated clone answered the patient count with 4,115 and persisted it as `ANS-4303df8f4c321fa4c8b46427`. Refresh opened that answer without submitting again.
- Actual browser submission of the full financial scenario produced the $246,450 hypothetical ACO liability, saved `ANS-9f32a64096b4946a1e169c5b`, an HTML brief and a manual monitor. Desktop and 727-pixel CSS viewport checks showed the connected form and result without horizontal overflow.
- Browser inspection exposed an unresolved data path that the previous static verifier accepted. The new verifier rejects that pattern before delivery. Repair then exposed a numeric browser-title false positive; numeric title values now require evidence while numeric body facts still require binding. An exact already-paid provider response was recovered offline into revision 3, and its rendered figures, assumptions, cited-rule rows and limits were inspected. The only console error retained is the original pre-repair event at 23:22:47 UTC; no new error appeared on the repaired view.
- Fourteen live HTTP checks passed, including assets, saved context, health, traversal rejection and origin/host guards. Runtime source hashes match the frozen release; warehouse database size/mtime and configuration hash were preserved.
- Live browser replay through the new website reopened existing `ANS-b03813fb18167833358aafeb` with the same brief/monitor, zero provider calls and no duplicate work. One submission recorded one outcome (1,556 to 1,557); the subsequent refresh added none.

## Release and accounting

Activated source: `da83c23e0435cf74bb8b8991a464674107db19d6`. Local checkout: `/Users/mikel/agere-mvp-run`, port 8885, PID 92451, exec session 15389. Rollback: `8e84ac26f57575cf1967b209a6712199cb07837a`. Log: `/private/tmp/agere-live-site.log`. Temporary candidate port 8917 was stopped. Browser viewport override was reset.

This increment made 10 provider attempts: nine have token usage, one failed for insufficient credits without usage. Known API estimate: **$2.174426**; complete cost is unknown. Offline recovery and live replay made zero new calls. These amounts exclude preceding releases, local compute and review time; they are usage estimates rather than invoice reconciliation. All failed/partial attempts remain in evidence.

## Remaining gaps and next action

- The hosted design preview remains a separate static preview. This release connects its owned design snapshot to the existing local app only; no external backend deployment or publication occurred.
- G3 temporal/current-state composition, current dose/barrier interpretation, broad multi-entity questions and conditional work remain incomplete. G5 and full ten-area acceptance remain incomplete.
- The new frozen-view verifier checks direct literal AGERE.get paths and evidence-backed numeric document titles. It is not a general JavaScript evaluator or complete generated-view runtime/meaning verifier. Original failures and repair attempts are preserved.
- Generated briefs contain generation-time workflow state; this brief still describes planned work inside the frozen snapshot while the surrounding live workflow shows ready/manual monitoring.
- Financial output is hypothetical completion-adjusted received medical/pharmacy cost arithmetic on supplied terms, not official CMS reconciliation, actuarial qualification, attribution certification or payment entitlement. Prior expenditure reconciliation limits remain.
- The savings_pct warning incorrectly assumes percentages must be between zero and one hundred. Aggregate financial answers still show zero patients in focus in the action workspace. Fresh compound workflows remain slow.
- Anthropic returned HTTP 400 credit-balance-too-low during automatic brief repair. The already-paid validated response was replayed offline after fixing the title false positive; no new API call was used. Further uncached provider validation remains blocked pending credit restoration.
- Learning means recorded outcomes/corrections and checked scoped reuse. No automatic model-weight training or independent clinical/model qualification is claimed.

G3: integrate the isolated healthcare source/correction cache candidate f5ad976 + 5edac69 and temporal candidate 0835f13 + cf6fd98 in controlled increments; verify changed-wording correction/suspension/source-change behavior through the connected website, then the original 21-patient final-status access question and clinical compound answer/workflow with provider calls when credits are available. Keep candidates unactivated until their required owner workflow checks pass; preserve generated-view meaning and latency work and independent G5 qualification.

The separately human-authorized candidates remain isolated: healthcare-domain learning `f5ad976` + `5edac69` (167 checks reported by its lane), and temporal/current-state semantics `0835f13` + `cf6fd98` (194 checks plus 14 local warehouse questions reported by its lane). They are not part of this activated release. Owner qualification must retain their failed provider attempts and evaluation-hygiene disclosures.

Receipt: `artifacts/agere-site-workflow-v1/implementation-receipt.json`. No protected evaluation, generator, research corpus or original source data was modified.
