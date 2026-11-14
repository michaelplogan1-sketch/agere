# Automatic workflow and recursive execution improvement

G3 remains active. This is a source candidate prepared with offline engineering
checks while Claude is unavailable. The complete live automatic answer and
deliverable are still pending. The accepted assisted Heartline delivery and the
existing runtimes are preserved.

## General product changes

The existing investigator can request a complete population document review by
supplying either received patient IDs or read-only cohort SQL and a map of
narrative questions. It reads all scoped notes, HIE, scanned and other supported
clinical text. Exact copies on the same date are deduplicated with aliases;
large documents continue across overlapping segments. Patients without documents
remain explicit. No patient sample replaces the requested population.

Haiku extracts source-bound findings first. Quotes are checked against their own
documents. The existing Jev/Perplexity judgment path checks the complete supplied
documents for omissions, negation, dates, other-person statements and conflicting
findings, including when the extraction is empty. Sonnet handles extraction or
judgment failures. Unresolved findings stay visible for further investigation;
they are not certified clinical facts. Shared document enumeration now respects
the received availability date as well as the clinical date.

The same read-only investigator connection accepts existing governed SQL macros
and dose functions. Dose arithmetic uses administration quantities and frequency;
reference ranges retain their existing published source and limitations. A
published range does not establish appropriateness or maximum tolerated dose.
This change exposes existing clinical definitions rather than adding a disease
handler or treating missing evidence as a contraindication.

An explicit complete-result query retains every native row in its receipt and
temporary result view. The model receives a marked preview for large tables and
can query the complete view. The final table is copied from executed results,
without model transcription. Downstream deliverable intent survives the
investigator/proposer boundary and reaches the existing saved-answer/frontier
artifact workflow. Free-form presentation/service briefs are accepted, while
additional analytical restrictions and calculations still require binding.

## Recursive self improvement

`AGERE_RECURSIVE_IMPROVEMENT=on` enables a candidate controller at the existing
model/tool boundary. Default remains off pending live qualification. It learns
literal execution configurations with fixed model weights:

1. A checked investigation proposes its exact read-only actions and native outputs.
2. A fresh native executor validates each output under source, scope, date,
   definition, code, model, request/context and physician-feedback guards.
3. Selection waits for the complete analytical answer and every requested
   downstream step. A dropped, blocked or changed deliverable cannot qualify it.
4. On reuse the tools execute again. Matching receipts permit substitution of the
   recorded actor callbacks; a retained final draft requires a fresh evidence review.
5. A changed receipt resumes the actor with current results. Failed revisions and
   outcomes and failed-request feedback remain in append-only history. Subsequent revisions preserve their
   parentage, and incomplete outcomes or observed actor-cost regressions retire reuse.

Explicit physician feedback can be recorded with
`harness_improvement.record_feedback(path, candidate_id, accepted=..., reason=...)`.
It invalidates prior reuse guards; a rejection also quarantines that revision.
Storage failure yields to ordinary actor execution and does not fail a completed
answer. This controller uses new operational tables, never protected answer keys
or the earlier learning/answer stores. It does not change clinical definitions,
train model weights, infer physician approval or establish broad clinical accuracy.

This implements the attached HarnessMM paper's fixed-weight, guarded callback
substitution approach. The engineering fixture demonstrates a complete baseline
followed by native replay with zero actor sends instead of three. Fresh evidence
reviews still run. Live overall cost, including candidate validation, reviewers,
rejected paths and artifact generation, has not been compared. No live savings
claim follows from the scripted example.

Reader failures with unreported usage preserve the known cost subtotal while
leaving the total unknown. Provider ledgers retain failures and validation
overhead; an unknown total cannot become a zero-cost answer.

## Offline evidence and resumption

The connected authored case uses diabetes and treatment barriers to exercise the
same investigator, whole-note tool, SQL dose calculation, final review, proposer,
saved context and frontier artifact binding. It preserves a resolved historical
barrier and a patient with no notes. Separate checks retain all 2,505 native rows,
reject scope/provenance changes, stop on a simulated subscription limit, retire
bad RSI revisions and reuse an exact validated provider response without sending.
Scripted model judgments establish wiring, not clinical acceptability.

Final frozen-source verification ran 282 checks across 19 modules: 281 passed
and one optional live System One check was skipped. Network and subprocess
guards recorded zero send attempts. Optional local worker tiers were disabled,
matching the prepared runtime; explicitly scripted send boundaries remained in
place. The source fingerprint was unchanged throughout verification:
`00988d69b72a4f82bdebb9588b6b111a8eb8c87de8e90cf49ba43771632ea14a`.

The earlier interrupted live attempt is retained under local
`artifacts/agere-auto-workflow-2026-10-26/attempt1`. It submitted 57 patients and
4,610 documents; 54 unique patients had completed reader events before the weekly
Claude limit. That is not a completed review or a correctness grade. The ledger
has 181 validated and five failed requests. The CLI reported reset October 28 at
06:00 America/Chicago. No live retry was made during this offline work.

`run_automatic_workflow.py` preflights the ordinary compose request without
credentials, a live Stack or provider calls. It prints a source fingerprint and
retained-ledger counts. Execution requires that fingerprint, a new per-attempt
output directory and an explicit `--execute`; the runner forces subscription
transport and stops on failure. There is no scheduler or reset polling.

After subscription availability, use the prepared isolated received-source
runtime and the interrupted attempt's ledger. Exact validated requests can replay;
changed prompts or source requirements must be recomputed. Do not reset failed
parse protections or declare partial work complete. Run the full ordinary request,
inspect every requested result and saved deliverable, then qualify RSI baseline
and reuse with complete-request costs and physician judgments. Keep default RSI
and the default runtime unchanged until that evidence exists.

Detailed offline test logs, the source manifest, preflight and interruption
receipts stay local under `artifacts/agere-auto-workflow-2026-10-26/`. Only source,
tests and this explanation belong in the approved private draft PR.
