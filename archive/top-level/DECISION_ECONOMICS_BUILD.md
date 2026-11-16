# Saved-question route and request accounting — October 11, 2026

G5 remains active. Saved individual and population questions now show their actual
planning route, an equivalent-input baseline, exact linked request status and
recorded usage alongside the original internal work. This removes a connected
accounting gap; it does **not** complete the economical-routing gate. Full local
release engineering remains **4/9**; G3 remains blocked at **5/6**.

The new read-only audit reuses the question-decision follow-up store and existing
provider ledger. It recovers original usage behind exact cache keys, deduplicates
shared calls, and retains failed/pending/missing usage as unknown. When a retained
failed response has usage, that estimate can be displayed without retrying or
revalidating the request. Accounting reservations are not spend. Spend remains
uncapped; this delivery introduces no limit or dispatch policy.

The UI exposes request bounds where reconstructible, price provenance, fallback
and unresolved findings, and separate retrieval/ranking/reader/planning/service
qualification. Snapshots bind question, source dependencies, work, request rows,
failed response files and pricing. Reopen detects changed dependencies. The audit
does not make model calls, change services, or create care tasks.

## Actual workflow evidence

The verified individual comparison uses the same saved cardiometabolic answer and
exact excerpts, not different patient inputs. Population verification uses the
existing complete 1,614-member respiratory question and its original owned work.

| Saved route | Recorded inference calls linked | Known historical API estimate | Mapped findings |
|---|---:|---:|---:|
| Local source-category planner | 26 | $2.046312 | 0 of 47 |
| Explicit buyer-aware candidate | 36 | $2.548540 | 35 of 47 |
| Local population contracts | 0 in this question trace | $0 linked | Not comparable to individual findings |

The two individual totals overlap: 26 source calls are shared; the candidate adds
10 planner calls and $0.502228 in recorded API estimates. Do not sum these rows as
separate incurred workloads. Coverage is not semantic correctness or clinical
accuracy. The candidate stays explicit and unqualified; no default is promoted.

Nineteen current reconstructed request keys have no ledger reservation. They are
shown as not dispatched in this reconstruction, not evidence that earlier variants
were free. Earlier changed-scope attempts, pre-index ingestion/learning, local
compute and human review cannot be fully attributed here. Total cost stays unknown;
the zero linked population API estimate is not a claim that its upstream work was
free. Estimates use the existing pinned pricing and are not invoice verification.

Verification in `artifacts/agere-decision-economics-v1/`:

- 58 focused checks pass, including failed/pending/missing usage, duplicate keys,
  retained failure usage, work/source/ledger changes and interrupted assembly.
- `verified-v2/verification.json`: three actual HTTP workflows, exact repeat and
  reopen, identical-answer comparison, independent raw-ledger arithmetic, unchanged
  provider ledger and unchanged existing work.
- `browser-verification.json`: population audit and current-check, individual
  candidate display/baseline/current-check, and intact service/work/follow-up UI.
- `restart-verification.json`: all three snapshots reopen unchanged through a
  separate normal-startup server with hosted inference disabled.
- Python/JavaScript syntax checks pass. All 170 protected files and 5,132 prior
  JSON artifacts checked retain their bytes; the complete provider ledger is unchanged.

The initial live audit incorrectly included token-count receipt hashes as inference
requests. Corrected usage-only collection and regression checks exclude those keys;
the initial artifacts remain preserved and are superseded by `verified-v2/`.
The browser also exposed an assumption that every follow-up had source-change
fields. Accounting snapshots now render in their own section without breaking the
existing individual decision. An initial fresh-process probe omitted normal startup
hooks; the subsequent full-server restart check passes. Failed probe logs remain.

Initial API audit timings were 26.73 seconds (individual baseline), 44.27 seconds
(candidate), and 0.39 seconds (population); normal fresh-server dependency reopens
took 13.93, 22.85, and 0.92 seconds. These are development measurements under local
verification load, not latency guarantees. Individual dependency reconstruction is
still expensive. No new paid calls, model fits, task changes, human reviews or
external actions occurred; total development compute cost is unknown.

## Remaining G5 work

Complete prospective question-to-attempt lineage, including failed requests and
escalation, in the existing dispatch path; verify route selection and fallback on
equivalent visible inputs through the shared question/work flow. Historical missing
lineage must remain explicit. This audit alone cannot establish complete request,
retry and review accounting or qualify retrieval, rerankers, readers and planners.
Use the canonical control checkpoint for the next connected acceptance increment.
Independent clinical/model targets, changed-source interpretation, the pending
specific medication transfer, adapter conformance and portable operations remain
required. The full ten-area backlog is preserved.
