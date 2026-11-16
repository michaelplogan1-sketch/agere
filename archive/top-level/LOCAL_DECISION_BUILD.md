# Local bounded decision adapters

October 9, 2026. Implemented and executed on synthetic data only.

Kev 4B now has a pinned MLX backbone/LoRA/pointer-head wrapper. Liquid d1-3B has
a pinned native BF16/MPS wrapper under the same bounded evidence contract.
Both are installed research capabilities. Neither was promoted into the default
warehouse reader; the matched experiment did not establish a quality advantage
or a usable calibrated automatic-acceptance region.

Read the [paired run report](artifacts/agere-local-decisions-v1/REPORT.md) and
[research reconciliation](research/agere/LOCAL_DECISION_RECONCILIATION_2026-10-09.md).
Every model revision, input, threshold and raw prediction is retained.

## Code and artifacts

| Path | Responsibility |
|---|---|
| `clinical_core/agere/clinical/local_decisions.py` | Offline pinned model loading, SHA-256 checks, native readouts, exact option distributions, strict context bounds, parity and resource measurements |
| `clinical_core/agere/clinical/bounded_evidence.py` | Shared retrieval packets, source spans, category/support questions, consistency and abstention rules, matched lexicon |
| `run_local_decision_comparison.py` | Freeze sources and disjoint calibration families, run both candidates, retain all thresholds/predictions, score retrieval versus interpretation and downstream statuses |
| `test_local_decisions.py` | Integrity, source binding, option validation, ambiguity, calibration and missing-reference checks |
| `models/kev-4b/agere-model.json` | Kev adapter/head, base and audited source inventories |
| `models/d1-3b/agere-model.json` | d1 weights, tokenizer, configuration and custom-source inventory |
| `artifacts/agere-local-decisions-v1/` | Frozen paired experiment and reports |

## Use the wrapper

Load one model instance per process and reuse it. Execute the models sequentially
on this laptop; do not assume two resident backbones fit comfortably with the
rest of the stack. A source packet uses exact spans returned by the shared
retriever. Model results are evidence proposals, not clinical decisions.

```python
from clinical_core.bridge import InsightBridge
from clinical_core.agere.clinical.local_decisions import KevMLX, LiquidD1
from clinical_core.agere.clinical import bounded_evidence as evidence
from pathlib import Path

bridge = InsightBridge(Path('/Users/mikel/insight-engine'))
packet = evidence.retrieve(bridge.reader, synthetic_notes, 'statin')
state, questions = evidence.request('statin', packet, '2026-09-30')
model = KevMLX()  # or LiquidD1() in a separate process
answer = model(state, questions)
# The recorded experiment disabled automatic acceptance for both models.
proposal = evidence.interpret(answer, packet, 'statin', threshold=1.000001)
```

## Reproduce or inspect the frozen comparison

```sh
cd /Users/mikel/clinical-insights-local
.venv/bin/python -m unittest test_local_decisions test_core test_mlx_reader -q
.venv/bin/python run_local_decision_comparison.py run --model kev
.venv/bin/python run_local_decision_comparison.py run --model d1
```

The run commands verify frozen code/input/model identities and reuse recorded
requests. They do load the model and repeat Kev's native parity check, but do not
resample cached cohort predictions. A different model, prompt, threshold rule or
source set requires a new versioned experiment; do not overwrite this cohort.
`prepare` intentionally refuses to replace an existing experiment. Scoring used
`evaluate`; rerunning it updates observational lexicon timings, so preserve the
original receipts when conducting a new analysis.

The runtime is fully local after acquisition. Verified custom d1 code is loaded
with offline Hugging Face access; no hosted fallback exists. Release licenses are
retained with the checkpoints. Local operation is not zero total economic cost.
