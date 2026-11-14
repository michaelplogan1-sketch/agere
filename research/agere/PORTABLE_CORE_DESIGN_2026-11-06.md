# A portable core with checked warehouse mappings

**Design only; no schema migration, dataset ingestion or runtime activation.**
Mike's November 6 direction makes Wollstonecraft industry agnostic. Healthcare
remains the synthetic proving ground, with all ten acceptance areas and remaining
G3 program-criteria/source-interpretation → automatic-handoff work retained.
This extends the existing adapter, assertion/state, operator and decision context
contracts; it does not replace the application or turn clinical rules into universal rules.

## Core contract

| Object | Minimum meaning and constraints |
| --- | --- |
| Entity | Namespaced, typed identity: person, organization, asset, product, account, etc. Identity links require source evidence; labels are not merge keys. |
| Event | Typed occurrence with participants and their roles, event-time bounds, known/received time, source record/version and status. A reported event stays distinct from observed completion. |
| Relationship | Typed directed endpoints, role, effective period and assertion provenance. Cardinality and same-entity constraints remain explicit. |
| Measure | Versioned definition, grain, unit, eligible population, numerator/denominator, aggregation and missingness rules; separately typed observations/estimates with calculation receipts. |
| Contract / obligation | Versioned parties, conditions, required/permitted/prohibited conduct, measures, effective period and authority. Applicability, fulfilment and entitlement are separate findings. |
| Period | Typed interval with open/closed endpoints, precision, calendar/timezone and as-of policy; event, availability and contract-effective clocks cannot substitute for each other. |

Every object links to immutable source identity, original value/unit, mapping and
definition versions, uncertainty and observed/assumed/simulated status. Many-valued
participants and assertions remain relations, preserving row multiplicity and
conflicts. An absent value is an explicit unknown or missing reference, never an
invented entity, zero or negative fact. The existing shared decision context binds
the original question, exact entity/population scope, period, evidence, work and
follow-up to these contracts.

## LLM proposal, mechanical checking

1. Profile an authorized read-only source: keys, duplicates, nulls, types, units,
   joins, clocks, dictionary definitions and coverage. Pin source/schema hashes.
   An LLM proposes named concepts, typed paths, read-only view expressions and
   evidence for their meanings. Every uncertain mapping remains a proposal.
2. Compile transformations into a typed **source-view schema S**. Let **C** be
   the core schema and propose **F: C → S**, mapping core objects to source views
   and core arrows to typed source paths. Given source-view instance **I**, the
   core instance is the pullback **Δ_F(I) = I ∘ F**. This makes the mapping direction
   explicit. Σ/Π are possible adjoint migrations under their conditions, not a
   promise to push arbitrary warehouse data losslessly into any schema.
3. A separate checker verifies domains/codomains, type/unit consistency,
   identity/composition and declared path equations. Restrict the initial proof
   language to a decidable fragment; an unproved obligation is **unknown**, not
   pass. Key uniqueness, foreign keys, nullability, join cardinality, row/grain
   preservation and temporal constraints need additional finite-instance checks
   and declared constraints; a functor alone does not guarantee them.
4. Check operator preconditions, conservation/reconciliation and direct-query
   equivalence against the pinned data. Emit pass/fail/unknown with counterexamples,
   mapping hash and proof receipts. Admit only the covered operators. Preserve a
   known-good mapping and invalidate only its dependent results after change.

Formal checks establish the declared structure and constraints. They do **not**
prove that “balance” means revenue, an assignment is current responsibility, or an
invoice establishes payment. Those meanings require source definitions and
semantic qualification; ambiguous meanings abstain. Partial/nullable relationships
need explicit option types or partial-map semantics rather than fictional total
foreign keys.

The foundation is [Algebraic Data Integration, §§4–6](https://arxiv.org/html/1503.03571).
The [robot transfer paper](https://arxiv.org/html/2406.15961) also warns about
missing attributes and unsafe defaults. LLM mapping papers supply candidate
generation methods, not proofs of business meaning.

## Attach A, C and guarded reuse

- **A — disagreement:** Attach a source-overlap graph to the mapped assertions.
  For comparable numeric values only, restriction maps normalize units, entity,
  grain and period; edge residuals contribute to `||δx||²`. Retain the exact
  residuals and source pairs. No overlap means unassessed consistency, not zero
  disagreement. Categorical claims need discrete compatibility constraints.
  Source disagreement can suggest a VoI candidate or retrieval request; it cannot
  select truth or become calibrated uncertainty. Compare against simple paired
  residuals/conflict rules before adding spectral machinery.
- **C — learned operators:** Canonicalize journal subplans against core types,
  extract parameterized repeated compositions, and propose a documented operator
  with preconditions and provenance. Keep LLM subroutines distinct from compiled
  native programs. Gate each proposal on source-family-separated paired complete
  workflows, including unfamiliar combinations and adverse cases, at the same
  backbone and scope. Require useful improvement without lost requirements,
  account for induction/verification cost, and retain rollback. No automatic
  training or deployment follows from repeated traces.
- **#3 — guarded reuse:** Reuse an already resolved decision only when original
  intent/scope, period, source versions, mapping, rule/measure/contract versions,
  operator/model qualification and authority guards hold. Keep literal arguments
  and dependency edges immutable. A changed mapping or unresolved semantic choice
  invalidates affected decisions; new semantic inference returns to the ordinary
  planner. A passing guard proves its declared condition, not overall correctness.

The distinctive hypothesis is that mapping failures and localized source conflicts
can guide economical clarification/acquisition, after which qualified operator
compositions and guarded decisions transfer. Test this combination against plain
checked mappings plus the existing operators/cache; uniqueness and compound gains
remain unverified.

## First portability proof

Use [UCI Online Retail](https://archive.ics.uci.edu/dataset/352/online%2Bretail):
541,909 transaction lines from a UK retailer, December 2010–December 2011, with
invoice/product/customer identifiers, timestamp, quantity, sterling unit price
and country. UCI marks cancellation invoice IDs with an initial “c”. The source
and its current license were identified; this lane has not downloaded or profiled it.

Map customers/products to entities, invoice lines/cancellations to events,
line-to-invoice/product/customer links to relationships, and explicitly defined
quantities/invoiced-value measures to periods. Use received file hash plus row
ordinal for line identity; InvoiceNo is not a unique line key and identical rows
must not collapse. Profile missing IDs and cancellations before selecting a
denominator or net-value definition. The dataset has no verified contract terms,
payment ledger or inventory: contract/obligation, cash receipts, fulfilment and
stock-out questions should return precise unsupported inputs.

After G3, prove the **same existing** filter/join/window/aggregate/rank operators
on country/time-scoped entity counts, product quantities and invoiced-value
rankings, matching independent SQL and exact row/denominator accounting. Mutate
units, join keys, duplicate rows and time bounds to require rejection or a new
mapping version; verify guarded invalidation. Re-run the affected healthcare
regressions unchanged. Keep actual UCI observations, any authored perturbations
and the synthetic healthcare data distinctly labelled. This would prove finite
operator portability, not readiness for every industry, causal inference,
forecast qualification or transferable action authority.
