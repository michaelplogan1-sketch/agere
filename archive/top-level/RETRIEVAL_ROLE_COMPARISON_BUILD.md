# Model-role retrieval inspection — October 11, 2026

Agere now compares the existing BM25/MiniLM reciprocal-rank-fusion baseline with the existing BGE reranker on the exact same 24 passages. The actual question and scope stay attached. The source diversity rule is checked against ordinary search output; normal model routing and work authority are unchanged. Immutable reports retain candidate text, exact offsets, hashes, source dates, both rankings, selected/omitted evidence and bounded local compute accounting. Replays reuse the report. Source, model or code changes invalidate currentness.

The result is available directly beside normal question search, reopens by a saved link, and connects each passage to the existing source investigation. A received-category inventory shows what was outside the candidate/selected set and lets the operator inspect those records in the patient-filtered evidence atlas. Inventory counts cover the whole received patient snapshot, potentially wider than the query date window; they are not recall or clinical relevance scores. Category exploration does not automatically add sources to an answer or transmit them to a model.

| Development question | Same candidates | Selected before/after | Entered/left after reranking |
| --- | ---: | ---: | ---: |
| medication | 24 | 8/8 | 3/3 |
| respiratory | 24 | 8/8 | 4/4 |
| cardiometabolic | 24 | 8/8 | 5/5 |
| functional | 24 | 8/8 | 4/4 |
| appointment | 24 | 8/8 | 4/4 |
| payer | 24 | 8/8 | 5/5 |

All six use original-training patients with reserved/frozen families excluded. The exact questions were recorded before execution. These are engineering comparisons without relevance labels; ranking differences establish neither improvement nor clinical correctness. Reader, classifier, planner and service matching are marked not run. No model was trained, tuned or promoted. The initial comparisons are retained in `first-pass-before-metadata-guard`; final receipts include additional category/date/kind integrity checks and use the existing local pair-score cache.

A concrete candidate-generation gap is now visible: the payer question for P-1006 retrieved neither its one coverage record nor its 15 coverage-verification records. The medication question for P-1001 omitted both medication orders. Their existence alone does not establish relevance, but a reranker cannot recover a source absent from its input. The new category route opens these sources for explicit inspection. Automatic structured-source routing is not yet improved.

Verification: 27 focused tests; six live paired comparisons matching ordinary search; immutable/exact cache replay and invalid-input rejection; browser question → comparison → reopen → omitted category → exact source; independent audit of 144 passages from 129 records and all six category inventories. Prior 157 protected files remain intact; the new manifest protects 159. All 837 ledger rows, 19 Documents research files and program/capability histories are unchanged after navigation delivery (382 work items, 385 work events, 13 capability tasks, 37 capability events). Zero paid API calls/cost or external actions. Spending stays uncapped.

Performance remains a gap: first traces took about 10–25 seconds, with only 1.4–2 seconds measured in uncached BGE inference; fully score-cached traces still took about 9–22 seconds. This is evidence to profile the retrieval stages, not grounds to blame or replace a model.

Next: use these development diagnostics to qualify an explicit structured-source candidate route, starting with payer and medication questions and their existing exact-source adapters. Compare it to the frozen current baseline; preserve source categories, date scope and unresolved questions. Then compare reader/classifier outputs on identical bounded source context with explicit development-versus-adjudicated labels and independent held-out protections. Clinical validation, broad model selection, reliable production adapters and the larger product acceptance queue remain open.

Evidence: `artifacts/agere-retrieval-comparison-v1/implementation-receipt.json`, `predeclaration.json`, six workflow reports and category inventories, `http-verification.json`, `preservation.json`, `browser-verification.json`, `browser-comparison.png`.
