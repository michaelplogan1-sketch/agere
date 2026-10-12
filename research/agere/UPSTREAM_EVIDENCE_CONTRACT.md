# Evidence contract for the synthetic-world integration

October 8, 2026. Interface guidance for separate world and stack work; this document does not modify the generator. Everything described here uses synthetic records.

## Preserve the observation boundary

The stack must receive what a system could have observed at the requested cutoff. Hidden patient state, planted patterns and simulator outcomes belong in separate supervision/evaluation artifacts. An oracle flag must never silently become a clinical observation.

Preserve the following fields in the richer source representation:

- Stable corpus/world, patient and source-family identities. Family identity is required for training splits; repeated versions of one source person are not independent patients.
- Source record ID, source system, record version and exact text span or structured field reference. Keep copied/derived evidence linked to its origin.
- Event time and first available time separately. Unknown arrival stays unknown; event time is not a substitute.
- Applicable validity interval and explicit corrections or supersession. A hold or treatment statement does not become current forever.
- Raw lab value, raw unit, reference range and any normalized value with the conversion rule. Missing units must not be guessed.
- Medication orders, dispense events, reversals, reported use, discontinuations and reviewed exceptions as separate observations.
- Source connection, coverage window, expected delay, linkage status and outages separately from whether a clinical fact is positive or negative.
- Relevant synthetic social/economic facts with their source and dates. A documented barrier and a proposed barrier need different states.
- Policy/measure ID, version, effective dates and authoritative clause references for any compiled decision.

These are requested source features, not a claim that every current feed or kernel consumes them. The current Boolean decision kernel receives validated fact contracts after extraction and reconciliation; longitudinal measurements and source coverage need their own upstream operators.

## Current executable fact boundary

`clinical_core.reasoning.analyze_case(cache, scope=..., program=..., observations=..., as_of=..., acquisition=...)` accepts a synthetic decision program and the observation map below. Choose a scope containing corpus/world, patient and question identifiers. Do not reuse a scope for unrelated cases.

```json
{
  "eligible": {
    "value": null,
    "status": "unknown",
    "available_date": null,
    "valid_from": null,
    "valid_to": null,
    "support_ids": []
  },
  "care_current": {
    "value": true,
    "status": "proposal",
    "available_date": "2026-09-20",
    "valid_from": null,
    "valid_to": null,
    "support_ids": ["fictional-source:note:span"]
  }
}
```

This example deliberately remains unknown to the decision kernel: a proposed extracted statement is not a confirmed current-care fact. Omitted facts are also unknown. `value` must be a Boolean or null; zero, an empty string and absent data are not accepted substitutes for false.

Allowed status values are `reviewed`, `source_record`, `proposal`, `conflicting` and `unknown`. Only reviewed/source-record facts with a Boolean value, supporting identifiers, arrival by the cutoff and a known validity interval covering the cutoff are accepted. A `source_record` must actually establish the specific predicate: a pharmacy claim can establish a dispense event but cannot by itself establish ingestion or optimal therapy. The calling adapter is responsible for that semantic distinction and for authenticating the evidence/review.

Use `workflow_program()` only for the explicitly fictional engineering workflow. A real program requires its own reviewed specification; changing a rule is a versioned data update, not model retraining. This bounded kernel does not yet compile payer PDFs or validate clinical thresholds, lab units or payment arithmetic.

## Supervision for future models

Keep three artifacts separate: observed evidence, hidden simulator state, and reviewed labels. Train extraction on source-supported facts; train observation uncertainty on cases with controlled missingness; use hidden truth only in the permitted supervision/evaluation path. If identical observed records support different hidden realities, a forced hidden-state answer is the wrong target.

Useful paired cases change an answer-critical fact, a source's visibility or its arrival time while retaining the surrounding chart. Cases should cover contradictory medication states, outside care, stale copy-forward text, unknown units and documented exceptions. Synthetic facts alone do not establish that a clinical eligibility rule is medically correct.

The existing `clinical_core.learning` review workflow remains the path for attributed corrections and development-only exports. The reasoning lab's assumption controls are not reviewer submissions. Reserved evaluation families must remain outside training and calibration.

## Integration checks

For each future feed adapter, demonstrate that future-arrival evidence cannot alter an earlier answer, changing a relevant source/policy triggers affected work, copied evidence retains shared lineage, unconnected sources do not masquerade as negative care findings, and unsupported clinical premises remain unresolved. Measure correctness, useful coverage and full compute/review cost together; cache hits alone do not prove better care or lower total cost.
