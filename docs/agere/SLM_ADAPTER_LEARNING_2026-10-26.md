# Local SLM learning: offline implementation and evidence

The general stack now has a connected, opt-in SLM improvement loop alongside the
existing router/classifier learning, mathematical engines and fixed-weight execution
reuse. It accepts explicit reviewed tasks, trains local adapters, compares them with
the prior model using native source calculations, and supports release, correction,
quarantine and rollback. G3 remains open. The automatic clinical workflow still needs
the subscription run and physician review; the assisted Heartline acceptance does
not establish automatic product acceptance.

## What changed

The four existing local roles—typed request interpretation, question normalization,
bottled SQL and narrow cohort slots—share versioned adapter registration and runtime
pointers. Local base files, tokenizer, weights, serving prompts, trainer and native
compiler identities are pinned. Explicit registry mode does not silently discover
or fall back to old promoted weights or benchmark datasets. No disease-specific
handler, new architecture or background service was added.

The existing answer-correction API now accepts a source/context-bound direct user
review. The Ask view exposes “Mark answer correct” in the isolated learning mode;
negative feedback quarantines affected releases and execution reuse. A human-accepted
complete native SQL answer can produce compiler-checked code targets, never a copied
headline as clinical truth. Compound narratives and deliverables remain review
material rather than automatically becoming scalar labels. Direct task reviews can
also provide explicit targets and acceptability rules. Model-generated requests
cannot approve themselves.

An explicit plan exports eligible observations into immutable manifests with
separate fit, calibration and verification families and wording. Heavy adapter jobs
are queued by answers and run through the existing training CLI; they do not block
answers. Completed jobs are retained and deduplicated. The seven-job shipped plan
contains three classifier jobs and four adapter jobs, all with `install: false`.
Training waits for suitable independent supervision rather than treating volume as
quality. There is no new patient or spend cap.

Training uses the installed MLX LoRA backend and the existing pinned local Qwen3-4B
base. Complete sequence lengths determine the training context; inputs are not
silently truncated. Verification examples never enter the gradient/loss loader.
Only declared local files are used; network, downloads, telemetry and external
subprocesses are blocked during guarded experiments. Failed and rejected attempts
remain in the evidence history.

Qualification compiles outputs through the existing typed request/program/SQL or
cohort compiler and executes full source results. It preserves exact population,
empty membership, dates, grain, keys and explicit unknowns. Declared percent, dollars,
count and yes/no rules replace headline decimal-string grading. A single unambiguous
numeric metric alias can differ while keys and values must still match; ambiguous
column matching is refused. Candidate selection requires useful improvement without
regression or accepted wrong results on the primary and altered-source fixtures.
Raw local decodes have an integrity-checked exact cache, but every qualification
re-executes native source grading. Cached text is never supervision by itself.

## Measured offline results

Local evidence is under `artifacts/agere-slm-learning-2026-10-26/` and its preserved
copy in the original checkout's `artifacts/` directory. Source fingerprint:
`251e8ce3acd92b4f284649b3bc1a86d9104ea4e978ff8f4727cf9ffeba25ab94`.

| Check | Result and limit |
| --- | --- |
| Guarded regression checks | 488 primary checks passed; four optional/live or unavailable-data checks skipped. Fifteen isolated routing/Metal checks and one import-only check passed; four grammar checks overlap the primary suite. Zero provider sends, blocked attempts or protected-corpus reads in final verification. |
| Authored curriculum | 10,000 combinations reduced to 1,307 unique tasks in 15 families; 8,693 duplicates discarded. This is not 10,000 independent clinical questions. |
| Native target verification | All 1,307 compiler targets reproduce declared calculations on the frozen primary and altered-source fixtures. This verifies targets and source execution, not model interpretation. |
| Real local training | Typed-role LoRA: 520 fit tasks, 6 calibration, 6 verification; 260 iterations, batch 2, rank 8, four layers, attention and MLP projections. No provider inference. |
| Model comparison | Baseline verification 0/6 acceptable; candidate 3/6 acceptable, 3 declined, zero accepted wrong results on either source. Calibration improved 1/6 to 3/6. No regressions. |
| Independence limit | Verification comprises only two families with three wordings each. All three accepted verification cases are the claim-denial grouping family; terminology-name cases declined. This is engineering candidate selection, not clinical accuracy or broad generalization. |
| Fresh qualification | Reused 24 exact raw decodes with zero new generations; native compilation, full-result and altered-source grading ran again. |
| Actual runtime loading | A separate isolated release loaded the real trained weights through the normal typed worker and answered an authored grouping question acceptably on both sources. It was retired immediately after the check; default runtime weights were not promoted. |
| Other local roles | Normalizer, bottled SQL and narrow-slot manifest/compiler/runtime/rollback paths are covered by fixtures. This run did not train and clinically qualify four different adapters. |

The relational grammar alone interpreted 0/1,307 of these natural phrasings. That
receipt concerns one grammar, not the full stack, and is retained as a reason to
qualify the model-assisted path rather than as a stack-wide accuracy score.
Final test counts and exclusions are recorded in `verification.json` and `tests.json`.
Scripted clinical/model judgments are labeled engineering fixtures. Browser JavaScript
syntax was checked; the new user-review control still needs an actual browser check.

During an earlier legacy unit run, existing tests opened repository question
fixtures. No dev3/held-out answer-key scoring or model training used them. Those
file-based tests were removed from the final selection; the bottled guard now uses
a newly authored throwaway fixture. Final verification explicitly blocks repository
question-corpus reads. Earlier logs, failed setup checks and rejected candidates
remain available rather than being counted as passing evidence.

## Public references and learning scale

The public-source snapshot uses eight generic-drug searches against the
[NLM RxTerms API](https://clinicaltables.nlm.nih.gov/apidoc/rxterms/v3/doc.html), with
raw response hashes, retrieval time and [usage reference](https://clinicaltables.nlm.nih.gov/faq.html).
It contains 154 strength/form entries. These support names, routes and available
forms; they do not establish target/maximal tolerated doses or treatment suitability.
No patient information was sent to that API.

Doximity Ask and OpenEvidence were not queried for synthetic training answers. Their
published [Doximity terms](https://www.doximity.com/terms-of-service) and
[OpenEvidence Foundation acceptable-use policy](https://www.openevidence.foundation/policies/acceptable-use)
restrict the contemplated automated extraction/competitive training uses. No access
restriction was bypassed. Licensed or separately permitted reference material can
later enter with its own provenance and purpose.

A larger question program can help after there are independently reviewed source
calculations and sufficiently varied families: clinical current state and notes,
medication reconciliation/dose reasoning, utilization, access, providers, financial
and VBC workflows. Paraphrase volume alone does not demonstrate learning. Newly
reviewed successful and corrected ordinary work should feed the existing loop;
verification families stay separate, and an unsupported answer remains unsupported.

## Cost and exact continuation

Provider inference cost for this offline increment was zero; eight public-reference
HTTP requests are separately recorded. Local GPU training, evaluation, failures and
review still consume resources. There is no reliable cost per clinically correct
automatic answer yet. Count accepted physician-reviewed answers and include all
training, failed/rejected candidates, validation and final review costs before
claiming savings.

`resume-after-reset.txt` in the new evidence directory supersedes earlier commands.
It binds this frozen source fingerprint, the original request, the received-source
runtime, and the interrupted attempt1 ledger with 181 validated and five failed
entries. Preflight was prepared without credentials, providers or live Stack creation.
The first continuation keeps local SLMs off to finish the controlled clinical
baseline; candidate weights remain uninstalled. Later test any proposed released
role on appropriate reviewed clinical/analytics tasks before default activation.

After Claude is available, run the ordinary complete-note/dose/final-answer/artifact
workflow via `AGERE_CLAUDE_TRANSPORT=subscription`, inspect the saved result and
exports in the browser, and obtain physician acceptability. Then compare guarded
reuse with baseline including total costs. Stop immediately on another limit; no
external Anthropic API fallback, automatic retry or wakeup is configured. The
observed reset is October 28 at 06:00 America/Chicago. Full ten-area acceptance,
broader clinical/model qualification and total-cost improvement remain open.
