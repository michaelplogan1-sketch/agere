# Exact references in review questions — October 10, 2026

Saved review questions now expose the exact source units behind pass-qualified names such as `p1:u6`, with an explicit route to select those references and prepare linked follow-up evidence. This extends the existing answer → evidence-needs → bounded interpretation → program/buyer review path. Historical answers, evidence-needs receipts, attachment engines and clinical assessments are unchanged.

## Delivered behavior

- Exact pass-qualified names resolve through the original saved unit manifest. The full context manifest and original pass mappings must agree. Each located unit retains its source record, field, kind, unit content, hashes and exact question mentions.
- Bare `uN` references bind only to one unambiguous original source-pass namespace. Reconciliation aliases, conflicting named passes, unknown units, abbreviated references and missing source records remain unresolved. Complete literal received-record IDs can locate evidence outside the answer's original selection.
- A reviewer explicitly selects reference names. Agere preserves the original question, records unselected references and unresolved mentions, and expands the chosen records through existing typed links. Scope and meaning remain unassessed. No question is automatically closed and no premise is endorsed.
- A new additive attachment path accepts only a complete saved answer for the exact selected context and patient. It links back to the original question, supports idempotent attachment, and reports stale dependencies. Existing attachment records continue through their original engine. New reference selections and attachments use content-addressed receipts.
- The actual saved-answer interface includes unit inspection, explicit checkboxes, bounded source preparation and the existing interpretation/follow-up controls. The downstream saved-answer interface already supports program review and buyer handoffs.

## Actual warehouse verification

The preserved medication answer `3c26f4b1afff1ff8048117a7c55bed945dcd40306ec1324c1240af2dd3cc8c36` asks whether two source texts match in full. Its question resolves four reference names: two exact units and the two full record IDs appended to the original question. Selecting only `p1:u6` and `p2:u3` prepares **five linked records / eleven source units**, with no omissions. The exact original question is retained. Context `512f812df34b2d7d549ba77cfa85342c832ed449970b8c19f3c74bee45700135` and selection `d52ccc3eaf9fd4a1332c42d926d2786782df75213c10934dfbe0b268af5b7d5c` are saved.

A reconciliation question with ambiguous bare `u1` produces no guessed reference. The respiratory question containing two shortened record IDs likewise leaves both unresolved. Repeated lookup/selection returns identical receipts. Empty, duplicate and unknown selections are rejected. Attaching the parent answer to the new question's different context is rejected by the actual API.

71 unique focused tests pass, including 17 new tests. Tests cover source/pass boundaries, malformed suffixes, literal outside-scope IDs, manifest changes, explicit subsets, source mutation, context changes, wrong-patient/context attachments, immutable original questions, idempotence and stale attachment listing. JavaScript/Python syntax and actual HTTP/browser checks pass. The live browser reached the existing inference plan with the selected five-record context; it did not submit a new inference request. New-answer attachment success is fixture-tested; no new live answer, clinical review or buyer task was fabricated.

## Preservation and limits

76 earlier protected implementation files, eight saved paid answers, six buyer tasks and 19 separate Documents research files verify unchanged. Functional work and events retain the prior digests. Provider usage/cost accounting is unchanged, including after browser preparation: zero new model/token-count requests and zero external actions. Original-cohort warehouse configuration, source-family boundaries, frozen Kev/d1 experiments, reserved cohorts and the generator are preserved.

The new identity-bound `need_references.py` is added to the 77-file continuation preservation list. Future extensions should use a separate versioned module instead of changing existing receipt identities.

This capability locates evidence; it does not establish authorship, identity adjudication, clinical truth, benefit eligibility, completed care, or question resolution. It does not infer missing portions of abbreviated IDs, resolve arbitrary natural-language references or supply a clinical treatment recommendation. Explicit selection may include only part of a question's references; unresolved and unselected names remain in its receipt. A requested unit and a full-record name can refer to the same record, which is included only once in the context.

Next: answer appropriate literal source-comparison questions locally, retaining distinctions between equal text, source lineage, authorship and clinical meaning. Broader source semantics, reviewed clinical recommendations, buyer/plan applicability, qualified prediction/discovery, causal/latent/shared-value research, audited answer retirement and portable full-stack acceptance remain incomplete. Continue substantive work under the existing sole-owner heartbeat.
