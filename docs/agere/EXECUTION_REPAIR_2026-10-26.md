# Shared execution repair and component evidence

G3 remains active. Mike accepted every answer in the delivered assisted Heartline
workflow and authorized next steps. That acceptance belongs to that delivery.
The ordinary compound product request is still incomplete.

## What changed

- Scoped document retrieval probes FTS by row ID after selecting the patient/date
  scope. BM25, tie ordering and candidate counts are preserved; global searches
  keep their original query. A received-source diagnostic returned the same 73
  ordered passages in 0.209s versus 2.241s with the previous access order. This
  measures that query, not total product speed.
- Before expensive atomic population reading, the existing System One checks
  whether the declared operations can cover the original request. A confidently
  incomplete proposal yields to the next existing proposer. Unavailable or
  uncertain judgments leave execution available.
- The investigator inherits the clinical owner's existing phenotype definitions
  as read-only SQL views. The compiler and investigator now use the same
  definition, including its narrative evidence, instead of substituting coded
  condition membership. The received HFrEF source has 40 coded-only living
  patients and 57 under the owner phenotype. No clinical definition changed.
- The shared sender imposes neither its former 180-second timeout nor its own
  request-size ceiling. Explicit caller settings remain effective.
- Component receipts distinguish invocation, completion, failure, cached results
  and fresh inference. Optional expanded-SQL diagnostics do not replace query
  cancellation. Loading a model is not recorded as use.

## Which components actually ran

The accepted assisted delivery used governed SQL/Python, Haiku, Sonnet, Jev,
Perplexity and Opus for the HTML. It did not use UltraBERT, local Qwen/SLM tiers
or passage rerankers.

Later normal-path diagnostics exercised additional components:

| Component | Observed behavior | Limit of the evidence |
|---|---|---|
| MiniLM | Retrieval embedding calls; the recorded vectors were cache hits | Not fresh inference or proof of improved accuracy |
| BGE reranker | 154 calls in diagnostic candidate1; 165 fresh pairs scored, alongside cached pairs | That diagnostic had unavailable network providers and was stopped; it was not an accepted answer |
| UltraBERT | Not observed in these workflow traces | Separate offline encoder tests do not count as product use |
| Local Qwen/SLMs | Disabled in this isolated candidate configuration | No workflow contribution established |

The later network-enabled candidates did not invoke BGE after the incomplete
atomic proposal yielded to the investigator. Component use is conditional, not a
requirement to run every model on every question. No component ablation or broad
clinical qualification was performed.

## Guarded continuation

Subscription tool emulation now supports batches of literal, already-resolved
read-only actions. The existing investigator validates the source, scope, date,
definition and executor identities before executing each action. A changed
identity or failed predecessor returns control to the same reasoning conversation
with earlier results retained. Invalid batches are not partly executed.

Scripted transports establish identical actions/results with two callbacks
instead of three for an eligible batch. The live candidate did not use a batch,
so no live token or cost saving is claimed. There is no learned action cache,
model-weight training or operating recursive candidate-selection loop here.

## Verification and remaining gap

Across 11 focused modules, 219 distinct tests were exercised: 218 passed and one
optional live test was skipped. These cover retrieval ordering, SQL scope,
definition inheritance, read-only restrictions, cancellation, actual-use
observations, subscription translation and guarded continuation. They do not
measure clinical accuracy.

The retained network-enabled candidate2 returned partial work, then its repair
hit the former sender timeout. Candidate3 completed in 358.9s without that timeout
or a subscription limit and used the 57-patient owner phenotype. It remained
explicitly partial/unverified: 12 tool calls and nine answer reviews ended with
`no_progress`. Extracted note rows did not complete whole-note review, verified
dose assessment remained incomplete, and the service deliverable was an outline.
Its findings do not replace the accepted assisted result.

Next: connect unresolved requirements to evidence acquisition and the existing
deliverable workflow. Rewording a partial answer is not completion. Retain the
whole request, existing architecture and all ten acceptance areas. The known-good
Heartline workspace and original default checkout remain preserved.

Detailed traces, component audit, provider accounting, failed attempts and
engineering receipts stay in local `artifacts/agere-execution-repair-2026-10-26/`.
No synthetic data or runtime artifacts belong in the source PR.
