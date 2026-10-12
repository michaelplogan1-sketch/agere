# Model roles and economics — October 9, 2026

The user's requirement is maximum useful, correct healthcare interpretation at
low total cost, with frontier models reserved for tasks needing them. It does not
require one foundation model for every stage. The current executable selection
uses deterministic operators, local compact retrieval/context models, optional
typed hosted reviewers and frontier escalation. Qwen3-4B is not the current default.

## Evidence inspected

| Candidate or component | Evidence and decision |
|---|---|
| Local Qwen3-4B | `LOCAL_MODEL_STATUS.md` records a synthetic 20-patient/47-status experiment: rules 44/47 versus Qwen+rules 26/47, with 7 schema errors. `artifacts/agere-clinical-reader-v1/local-qwen/score.json` separately records 9 agreements among 12 authored cases, 2 invalid contracts and 1 decisive disagreement. Neither supports promotion as the default reader. These are narrow local experiments, not a verdict on the Qwen family. |
| Perplexity `pplx-decider-v1.1-27b` | The [official model card](https://huggingface.co/perplexity-ai/pplx-decider-v1.1-27b) describes a Qwen-derived backbone with a specialized decision architecture/readout. This is a different checkpoint and inference task from the local Qwen3-4B chat reader. Its [typed API](https://docs.perplexity.ai/docs/decisions/quickstart) supports categorical decisions; used here for bounded plan selection as well as optional evidence review. It is hosted in the current stack, not a small locally trained model. |
| Jev `jev-1.13.0` | Existing executable patient-scoped typed review adapter. Keep a replaceable role; prior and future task comparisons determine routing. No evidence justifies running both Jev and Perplexity on every chart. |
| MiniLM + BGE | Installed local embedding retrieval and cross-encoder reranking in the current warehouse. MiniLM now also proposes plan candidates. Similarity scores are not clinical truth or semantic-equivalence probabilities. |
| BioClinical ModernBERT | The [official model card](https://huggingface.co/thomas-sounack/BioClinical-ModernBERT-base) describes a medical-domain encoder, not a drop-in generative planner. Earlier frozen-encoder task heads exist locally. A candidate for expanded supervised extraction/classification; broad question-planning benefit has not been demonstrated here. |
| MedGemma | Google's [model card](https://developers.google.com/health-ai-developer-foundations/medgemma/model-card) describes task-specific capabilities and development/adaptation requirements. A candidate for medical document and interpretation tasks, not automatically the best compiler or cheapest inference engine. Not newly downloaded or promoted in this increment. |
| Claude | Current frontier adapter compiles unfamiliar supported compositions. The app now attempts eligible local/cheap routes first. It remains available for complex questions and future bounded synthesis; provider branding does not replace required clinical semantics. |

The [Qwen3-4B model card](https://huggingface.co/Qwen/Qwen3-4B) was also reviewed;
local task results determine its current status. Public leaderboards and
manufacturer evaluations help shortlist models but do not establish Agere's
end-to-end correctness, action utility or total cost.

## Economic implementation and next model work

See `ECONOMICAL_ROUTING_BUILD.md` and its source receipts for the executed change.
Local exact plans and repeat requests need no hosted planning call. For supported
paraphrases MiniLM proposes complete plans and Perplexity selects or abstains.
Unknown constraints and uncertain/new questions escalate to Claude. This is an
initial conservative router, not broad local SLM question understanding.

Use reviewed question/plan/correction pairs to train a compact selector and then
a constrained local compiler where sufficient supervision exists. Preserve
cohort/time/negation qualifiers and explicit unknowns. Partition by question family
and source world, not just paraphrase, before training. Measure supported-answer
coverage, consequential errors, abstentions, provider usage, local latency/compute,
cache/retry behavior and review cost. Unreviewed teacher plans are not gold labels.

Broader cheap coverage is a build objective. It should come from better reusable
skills and verified operators, not a lower confidence cutoff or misleading cache
hits. Model downloads, additional inference and a larger ensemble are justified
only when they improve a defined task under those measurements.
