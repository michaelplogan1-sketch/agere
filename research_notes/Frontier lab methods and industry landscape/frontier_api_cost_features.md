# Frontier-model API features and pricing mechanisms that affect accuracy and cost per question (Agere)

Research date: 2026-11-17. All pages below were opened on that date unless marked "snippet-only". Model names (Claude Fable/Mythos/Opus 5.5, GPT-6 / GPT-6.1 Sol/Terra, Gemini 4 Carbon/Argon/Hydrogen, and so on) are copied as the official pages list them on that date. Several prices change on announced dates (noted inline). Recommendations use trial / park / skip, each judged against the simple baseline named in the brief: no caching, a single standard call, and plain JSON prompting.

## Q1. Prompt caching: multipliers, TTLs and limits (Anthropic, OpenAI, Gemini)

### Takeaway
All three labs now discount cached input heavily, to 0.025x–0.1x of base input. Anthropic and OpenAI on GPT-5.6+ charge a write premium (1.25x short TTL, 2x–2.5x long TTL). Gemini 4 supports only automatic implicit caching. For Agere, a stable cached prefix (schema/semantic-layer catalog, governed metric definitions, few-shot recipes) on the frontier text-to-SQL rung is the clearest cost win. Recommendation: **trial** immediately, measured against the no-cache baseline using cache-hit rate and $/question.

### Cited Findings
**Anthropic (Claude API)**
- Multipliers: 5-minute cache write 1.25x base input; 1-hour cache write 2x base input; cache read 0.1x base input on most models. Caching pays off after 1 read (5m) or 2 reads (1h). — [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing)
- Newer models discount reads further: 0.05x for Claude Opus 5.5 ($0.20/MTok) and Sonnet 5.5 ($0.10/MTok); 0.025x for Fable/Mythos 5.1 and 5.2 ($0.25 and $0.20/MTok). Caching multipliers stack with the Batch discount and with data residency. — [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing)
- Example table rows: Opus 5.5 base $4, 5m write $5, 1h write $8, read $0.20, output $20 /MTok. Sonnet 5.5 base $2, writes $2.50/$4, read $0.10, output $10. Haiku 5.5 (≤100k prompt) base $0.10, read $0.01, output $0.50. — [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing)
- Minimum cacheable prefix is 512 tokens on current models (Fable/Mythos 5.x, Opus 5.5, Opus 5, Sonnet 5.5, Haiku 5.5), 1,024 on Sonnet 5 / Sonnet 4.6 / Opus 4.8, and 4,096 on Opus 4.5/4.6 and Haiku 4.5. Shorter prompts are silently not cached. — [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- TTL is 5 minutes by default, refreshed free on every hit; 1 hour is optional. A request allows up to 4 explicit breakpoints, and automatic caching (a top-level `cache_control`) uses one of them. Lookback is 20 blocks per breakpoint. — [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- Invalidation order is tools → system → messages. Changing tool definitions invalidates everything. Toggling citations or web search invalidates system and messages. Changing `tool_choice`, images, or thinking/effort parameters can invalidate. Matching is an exact prefix match. — [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- Usage reporting: `cache_creation_input_tokens`, `cache_read_input_tokens`, `input_tokens`, plus a split of writes by TTL. Organizations never share caches. Within an org, the Claude API isolates caches per workspace; Bedrock and Google-managed Vertex isolate at org level only. — [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- The docs give no lab-reported latency or percentage-saving benchmarks, only qualitative "improved time-to-first-token". — [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)

**OpenAI**
- Cached input is "discounted up to 95%". On GPT-5.6 and later, cache reads cost 0.1x input, or 0.05x on GPT-6.1 Sol and Terra. — [OpenAI prompt caching guide](https://developers.openai.com/api/docs/guides/prompt-caching)
- GPT-5.6+: `prompt_cache_options.ttl` sets a 30-minute minimum (default, writes at 1.25x) or 12 hours (writes at 2.5x, Responses API only). Older models use `in_memory` (about 5–10 minutes, up to 1h) or `24h` retention. The default depends on Zero Data Retention status: ZDR orgs default to `in_memory`. — [OpenAI prompt caching guide](https://developers.openai.com/api/docs/guides/prompt-caching)
- Minimum is 1,024 visible input tokens on GPT-5.6+. `prompt_cache_key` affects routing but does not guarantee a hit. Illustrative hit rates: about 70% for a single-turn LLM judge, above 90% for a multi-turn agent. Caches are not shared across orgs or across regional-processing boundaries. — [OpenAI prompt caching guide](https://developers.openai.com/api/docs/guides/prompt-caching)
- Cached input prices (Standard, per 1M tokens): gpt-6-astra $10 in / $1.00 cached / $50 out; gpt-6.1-sol $2 / $0.10 / $10; gpt-6.1-terra $1 / $0.05 / $5; gpt-6-luna $0.10 / $0.01 / $0.50. — [OpenAI API pricing](https://developers.openai.com/api/docs/pricing)

**Google Gemini**
- Implicit caching is on by default for Gemini 2.5 and newer, and savings are passed through automatically. Explicit (managed) caches are not supported for Gemini 4 models or the Interactions API; they remain available on Gemini 3.x/2.5 via generateContent. — [Gemini caching](https://aistudio.google.com/docs/caching)
- Minimum is 4,096 tokens for Gemini 4 Carbon/Argon/Hydrogen, 3.8/3.6 Flash and 3.1 Pro Preview, versus 2,048 for 2.5. In Gemini 4 multi-turn loops, the full previous request is cached only if the new request adds at least 2,050 tokens. — [Gemini caching](https://aistudio.google.com/docs/caching)
- Cached-input prices: Gemini 4 Carbon/Argon $0.10 per 1M until a step-up to $0.20 on 2026-12-01, against base input $2 → $4. Gemini 3.1 Pro Preview cached $0.20 (≤200k) / $0.40 (>200k), plus storage at $4.50 per 1M tokens per hour. 3.8 Flash storage is $0.50 → $1.00 per 1M tokens per hour. — [Gemini pricing](https://aistudio.google.com/docs/pricing)

### Inferences
- Agere's frontier SQL prompt (semantic layer + metric definitions + guardrails) is very likely above every minimum (512–4,096 tokens), so caching applies. With a 5m TTL and low question rates per tenant, hit rates may be poor. The 1h Anthropic TTL (2x write) or the 30-minute default on OpenAI GPT-5.6+ suits sporadic analyst traffic better; measure the hit rate before choosing.
- Per-workspace cache isolation on the Claude API maps cleanly onto per-customer (hospital/plan) workspaces, which limits cross-tenant leakage concerns. Bedrock and Vertex isolate caches only at org level.
- Keep tool definitions, `tool_choice`, citations toggles and effort constant inside a cached flow, because each can silently drop the cache. Log `cache_read_input_tokens` per request as a governance metric.
- Gemini 4's 4,096-token minimum and its implicit-only caching make cost less predictable than Anthropic's explicit breakpoints.

### Gaps
- None of the labs publish measured latency reductions for current models on their docs pages. Agere would need its own TTFT measurements.
- The Gemini page states no explicit implicit-cache discount percentage; it lists only cached prices.

## Q2. Batch APIs: discounts and turnaround

### Takeaway
Batch is a flat 50% discount at every vendor checked (Anthropic, OpenAI, Gemini, Bedrock), with a 24-hour completion window. It fits Agere's offline recipe promotion, nightly regression evaluation and bulk note-reading backfills. One caveat matters for PHI: Anthropic's Batch API is **not ZDR-eligible** (29-day retention). Recommendation: **trial** for nightly evaluation and recipe promotion on de-identified or synthetic data; check the retention/BAA terms before sending PHI.

### Cited Findings
- Anthropic Message Batches: 50% off input and output. Most batches finish in under 1 hour; a batch expires at 24h, and expired requests are not billed. Limits are 100,000 requests or 256 MB per batch, with results kept 29 days. Prompt-caching discounts stack with batch. Fast mode is not available in batch. `max_tokens: 0` cache pre-warming is not supported inside a batch. — [Anthropic batch processing](https://platform.claude.com/docs/en/build-with-claude/batch-processing); [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing)
- Anthropic batch ZDR status: "not-eligible — 29-day retention; async storage required" (page metadata). — [Anthropic batch processing](https://platform.claude.com/docs/en/build-with-claude/batch-processing)
- Anthropic batch prices: Opus 5.5 $2 / $10; Sonnet 5.5 $1 / $5; Haiku 5.5 $0.05 / $0.25 (≤100k prompt); Fable 5.2 $4 / $20 per MTok. — [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing)
- OpenAI Batch and Flex are both 50% of Standard. For example, gpt-6.1-sol batch is $1 in / $0.05 cached / $5 out, and gpt-6-astra is $5 / $0.50 / $25. — [OpenAI API pricing](https://developers.openai.com/api/docs/pricing)
- Gemini Batch/Flex is about 50% of Standard. For example, Gemini 4 Carbon/Argon batch is $1.00 → $2.00 input and $5 → $10 output (step-up on 2026-12-01), and 3.1 Pro Preview batch is $1 / $6 (≤200k). — [Gemini pricing](https://aistudio.google.com/docs/pricing)
- AWS Bedrock: Batch is 50% lower than on-demand for select models, including Anthropic. Flex is at a 50% discount, and Priority carries a 75% premium. — [Bedrock pricing](https://aws.amazon.com/bedrock/pricing/)

### Inferences
- Nightly re-runs of Agere's reserved evaluation set and candidate-recipe promotion (generate SQL for many paraphrases, then execute and compare) are a natural fit. Batch plus a cached shared prefix compounds the savings (for example, Opus 5.5 cache read under batch would be about $0.10/MTok if the multipliers stack as documented; this figure is my derivation).
- OpenAI/Gemini "Flex" gives a batch-like price on synchronous-ish calls. That could serve the non-urgent "frontier last rung" if the owner accepts slower answers, which fits the stated priority of accuracy and cost over speed.

### Gaps
- I did not open the OpenAI Batch guide itself, so its turnaround window and ZDR status come from the pricing page only. The standard 24h window is not verified here.
- Bedrock/Vertex retention terms for batch were not checked.

## Q3. Structured outputs, constrained decoding and strict tool use vs prompt-only JSON

### Takeaway
All labs now offer schema-constrained decoding. It removes parse failures and schema violations, but the vendors publish no current quantitative error rates. Independent work (JSONSchemaBench) shows near-100% compliance for OpenAI/Gemini on the schemas they support, though they support only a limited subset of JSON Schema. Constrained decoding matched or improved accuracy by up to about 4 points on reasoning tasks there. A separate study found strict format restrictions can hurt reasoning. Recommendation: **trial** strict tool use / JSON schema for the SQL-plan and extraction envelopes, measured against plain JSON prompting with retry. Keep reasoning in a free field or in thinking, and keep the schema small.

### Cited Findings
- Anthropic structured outputs (JSON outputs via `output_config.format`, plus `strict: true` tool use) use constrained decoding and claim "always valid", "no JSON parsing errors" and "no retries needed for schema violations". The page gives no quantitative error rates. — [Anthropic structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
- Unsupported on Anthropic: recursive schemas, numeric min/max, string length, most array constraints; regex lookaround and backreferences; `additionalProperties` must be false. An unsupported feature returns a 400. The first use of a schema adds grammar-compile latency, and grammars are cached for 24h. A hidden format system prompt adds input tokens. Changing `output_config.format` invalidates the prompt cache. — [Anthropic structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
- OpenAI: strict Structured Outputs adheres to the schema; JSON mode guarantees valid JSON but not schema adherence. The first request with a new schema has extra latency, and refusals come back in a separate `refusal` field. The guide gives no reliability numbers. — [OpenAI structured outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs)
- JSONSchemaBench (Geng et al., EPFL/Microsoft, arXiv 2501.10868 v3): OpenAI compliance 0.92–1.00 and Gemini 0.88–1.00 on supported schemas, but low empirical coverage (for example, OpenAI 0.09 on GitHub Hard and 0.06 on JSONSchemaStore) because they support only a subset of schemas. — [JSONSchemaBench](https://arxiv.org/html/2501.10868v3)
- JSONSchemaBench downstream accuracy with open engines (Llama-3.1-8B): GSM8K 80.1% unconstrained vs 81.6–83.8% constrained; Last Letters 50.7% vs 51.2–54.0%. Constrained decoding was at or above baseline, with up to about 4-point gains. Guidance roughly halved generation time on some datasets. — [JSONSchemaBench](https://arxiv.org/html/2501.10868v3)
- Counter-evidence: "Let Me Speak Freely?" (Tam et al., arXiv 2408.02442, 2024) reports "significant decline in LLMs reasoning abilities under format restrictions", with stricter formats causing larger drops. — [Let Me Speak Freely?](https://arxiv.org/abs/2408.02442)
- Anthropic tool-use system prompt overhead: about 286 tokens (auto/none) on Opus 5.5, and 286/406 tokens on Opus 5. — [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing)

### Inferences
- For Agere, the measurable failure that structured outputs remove is envelope breakage (malformed JSON, missing fields such as `sql`, `assumptions`, `population_scope`). That failure is likely already rare with plain prompting plus validation and retry, so the gain should be measured as retry rate × cost, not assumed.
- The reasoning-degradation risk argues for keeping SQL reasoning outside the strict schema (thinking, or a free-text `rationale` field placed first) and for not encoding numeric business rules into the schema, which Anthropic and OpenAI do not enforce anyway. Validate those in code.
- Anthropic's restriction that citations cannot be combined with structured outputs affects note reading (see Q4).

### Gaps
- OpenAI's 2024 launch post reportedly gave a schema-following figure (100% for strict mode vs much lower for prompt-only on an older model), but the page returned 403. Treat it as unverified. No current vendor figure for the 2026 models was found.
- No text-to-SQL–specific study comparing strict schema vs prompt-only JSON on SQL accuracy was found.

## Q4. Citations and grounding features for quoting note evidence

### Takeaway
Anthropic's Citations API returns exact quoted spans (character, page or custom block indices). Quoted text is free in output tokens, and the feature is HIPAA-ready and ZDR-eligible. Vendor-reported evidence: up to 15% better recall vs custom prompt citations, and one customer cut source hallucinations from 10% to 0%. It cannot be combined with structured outputs. Recommendation: **trial** on the note-reading rung, with custom-content documents (one block per note sentence or section), against the baseline of a prompted quote-and-offset JSON verified by string match. Gemini Search grounding and OpenAI web search are web-oriented and irrelevant for PHI notes (**skip**). OpenAI file search (a hosted vector store) is **park**, because it moves notes into a vendor store.

### Cited Findings
- Anthropic citations support plain text (auto-chunked into sentences), PDF (text-extracted; scanned image PDFs are not citable) and custom content (no chunking; citations as block indices). `cited_text` does not count toward output tokens, nor toward input when passed back. Enabling citations slightly increases input tokens. — [Anthropic citations](https://platform.claude.com/docs/en/build-with-claude/citations)
- "Citations cannot be used together with structured outputs": a request that enables both errors. The docs advise against prompts demanding "only JSON", which yield few or no citations. Source documents can be prompt-cached, but citation blocks cannot. Citations are ZDR-eligible and HIPAA-ready on all active models. — [Anthropic citations](https://platform.claude.com/docs/en/build-with-claude/citations)
- Anthropic launch (2025-06-23): internal evals found built-in citations increase recall accuracy "by up to 15%" vs most custom implementations. Customer Endex reported source hallucinations and formatting issues falling from 10% to 0%, with references per response up 20%. Both are vendor/customer-reported, not independent. — [Anthropic Citations launch](https://claude.com/blog/introducing-citations-api)
- Gemini grounding with Google Search: 5,000 free requests per month, then $14 per 1,000 requests (daily cap 1,500). Maps grounding is priced similarly. — [Gemini pricing](https://aistudio.google.com/docs/pricing)
- OpenAI file search: storage $0.10/GB/day (1 GB free); tool calls $2.50 per 1k (Responses API only). Web search $10 per 1k calls plus content tokens. — [OpenAI API pricing](https://developers.openai.com/api/docs/pricing)

### Inferences
- For Agere's note ladder, the useful property is verifiable span offsets that a deterministic checker can confirm exist in the source note. Custom-content blocks keyed to Agere's own sentence or section IDs make citation indices map directly to versioned evidence IDs.
- Because citations exclude structured outputs, the pattern would be one citations call for evidence spans plus deterministic or cheap-model structuring, or the documented "one line per field" pattern parsed in code. That cost and latency must be compared against a single strict-JSON call with self-reported quotes and offsets, then string verification.

### Gaps
- No independent (non-vendor) benchmark of the Citations API on clinical notes was found.
- No accuracy evidence was found for OpenAI file search or Gemini grounding on private-document QA. I did not open the Vertex grounding-on-your-data docs.

## Q5. Extended/adaptive thinking and reasoning-effort controls: cost vs accuracy

### Takeaway
On current Anthropic models, adaptive thinking is always on and `effort` (low / medium / high / xhigh / max) is the main cost dial. Anthropic says most gains arrive by `high`, and that `max` often adds cost for small gains and can "overthink" structured-output tasks. The text-to-SQL literature suggests cheap pipeline modules (execution-feedback refinement) beat buying more model or reasoning. Recommendation: **trial** an effort sweep (low/medium/high) on Agere's SQL evaluation set for the frontier rung, measured against default effort and against an execution-feedback retry loop at lower effort.

### Cited Findings
- Effort levels: `low`, `medium`, `high`, `xhigh`, `max`. The default is `high` on most models and `medium` on Opus 5.5 and Haiku 5.5. Effort affects all output tokens, including tool calls and thinking. It is "a behavioral signal, not a strict token budget." — [Anthropic effort](https://platform.claude.com/docs/en/build-with-claude/effort)
- Opus 5.5: adaptive thinking is always on and can't be disabled (`thinking: disabled` returns a 400), so effort is the primary cost control. Anthropic recommends running an effort sweep on your own evals. — [Anthropic effort](https://platform.claude.com/docs/en/build-with-claude/effort)
- Fable 5.2 guidance: "Most of the gain in capability is already present at `high`". `max` "runs slower and costs more than at `xhigh`, often for only a small gain." Opus 4.7 guidance: `max` "can lead to overthinking" on "structured-output or less intelligence-sensitive tasks". — [Anthropic effort](https://platform.claude.com/docs/en/build-with-claude/effort)
- Changing top-level effort between requests can invalidate the prompt cache; per-message effort (on supported models) preserves it. — [Anthropic effort](https://platform.claude.com/docs/en/build-with-claude/effort)
- Lin et al., "Are These Modules Worth Their Cost?" (arXiv 2608.28432, 2026-08-28): across 17 ICL text-to-SQL configurations and 4 backbones, execution-feedback refinement is "the only paradigm whose benefit holds universally at consistently low cost." At a fixed budget, a richer pipeline on a mid-tier backbone often beats a frontier model with a lean pipeline. Abstract only; no numbers. — [arXiv 2608.28432](https://arxiv.org/abs/2608.28432)
- Snippet-only (search result, not opened): a 2026 on-prem BIRD study reports self-consistency gives +0.13 pp for about 5x tokens (not significant); Reasoning-SQL reports 72.78% BIRD EX at "93% lower" inference cost. — [arXiv 2606.29733](https://arxiv.org/abs/2606.29733) (snippet-only)

### Inferences
- For Agere's frontier rung, the default-effort difference (Opus 5.5 defaults to medium, other models to high) is itself a cost variable. Pin effort explicitly and record it with each answer's provenance.
- Execution-feedback refinement (run the SQL against the governed warehouse, feed back errors or empty results) is the strongest cheap baseline to compare against higher effort, and it fits Agere's existing execution-validation design.

### Gaps
- I did not open OpenAI's reasoning-effort or verbosity docs or Gemini's thinking-budget docs, so their parameter ranges and pricing behaviour are not verified here. (Gemini output prices are stated "incl. thinking" on the pricing page.)
- No vendor-published SQL-benchmark curves by effort level were found.

## Q6. Model routing and cascades: published savings at equal accuracy

### Takeaway
The academic results are large but old and benchmark-specific. FrugalGPT reports matching GPT-4 at up to 98% lower cost; RouteLLM reports more than 2x cost reduction without quality loss. These support Agere's existing ladder design (recipes → built-in SQL → classifier → small model → frontier), measured against an always-frontier baseline. Recommendation: **trial** as an evaluation of Agere's own ladder (cost and accuracy vs always-frontier on the reserved set). **Skip** vendor "auto" routers, which hide the model choice and break per-answer provenance.

### Cited Findings
- FrugalGPT (Chen, Zaharia, Zou; arXiv 2305.05176, 2023-05-09): an LLM cascade "can match the performance of GPT-4 ... with up to 98% cost reduction", or improve accuracy over GPT-4 by 4% at the same cost. — [FrugalGPT](https://arxiv.org/abs/2305.05176)
- RouteLLM (Ong et al., LMSYS/Berkeley; arXiv 2406.18665, v4 2025-02-23): routers trained on preference data reduce costs "by over 2 times in certain cases" without compromising response quality. — [RouteLLM](https://arxiv.org/abs/2406.18665)
- Anthropic's token counting docs explicitly list "make smart model routing decisions" as a use of the free count endpoint. — [Anthropic token counting](https://platform.claude.com/docs/en/build-with-claude/token-counting)
- Lin et al. 2026: under a fixed budget, a mid-tier backbone with a richer pipeline often beats a frontier backbone with a lean one. This supports routing most SQL to cheaper rungs plus validation. — [arXiv 2608.28432](https://arxiv.org/abs/2608.28432)

### Inferences
- Cascade savings depend on a reliable "accept" signal. Agere's equivalents (recipe match, SQL execution validity, semantic-layer conformance, classifier confidence) must be calibrated on the reserved evaluation, or the cascade trades cost for silent errors.
- The price spread between tiers is now very wide (Haiku 5.5 $0.10/$0.50 vs Opus 5.5 $4/$20 vs Fable 5.2 $8/$40 per MTok; gpt-6-luna $0.10/$0.50 vs gpt-6-astra $10/$50), so cascade economics are stronger than in 2023, provided the accept signal holds.

### Gaps
- Lab "auto" router products (for example OpenAI's GPT-5 routing) were not opened; no current published cost/accuracy data on them was gathered.
- No routing study on text-to-SQL or clinical note extraction specifically was found.

## Q7. Token counting, long-context tiers, data residency, fast/priority tiers, rate limits

### Takeaway
Cost-governance levers differ by vendor:
- Anthropic: free token counting, flat 1M-context pricing except on Haiku 5.5.
- OpenAI: 2x price above 272K tokens.
- Gemini: tiers above 200K on 3.1 Pro.

US data residency adds 10% at Anthropic and OpenAI, and regional or US cross-region endpoints add 10% on Bedrock, Vertex and Azure. "Fast/Priority" tiers cost 1.75x–2x and buy speed, which Agere's owner deprioritizes. Recommendation: **trial** token counting as a pre-call budget guard; **skip** fast/priority tiers; budget the 1.1x residency uplift as a deployment prerequisite.

### Cited Findings
- Anthropic token counting is free, with separate RPM limits (Start 5,000 / Build 10,000 / Scale 20,000 RPM). The count is an estimate and doesn't use caching. Claude 4.7+ and Fable/Mythos models use a newer tokenizer producing about 30% more tokens for the same text, so recount when migrating. It is HIPAA-ready and ZDR-eligible. — [Anthropic token counting](https://platform.claude.com/docs/en/build-with-claude/token-counting)
- Anthropic long context: Claude 4.6+ models (except Haiku 5.5) bill the full 1M window at standard rates. Haiku 5.5 rises from $0.10/$0.50 to $0.50/$2.50 above 100,000 prompt tokens, with cache tokens counting toward that threshold. — [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing)
- Anthropic data residency: `inference_geo: "us"` applies 1.1x to all token categories on Claude 4.6+. Bedrock regional and Vertex multi-region endpoints carry a 10% premium over global, and Azure Foundry's US Data Zone carries the same 1.1x. — [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing)
- Anthropic fast mode: up to 2.5x output speed at premium prices (Opus 5.5 $8/$40, Fable 5.2 $16/$80 per MTok). It is not available with Batch. — [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing)
- OpenAI: above 272K input tokens, prices double on input (for example gpt-6.1-sol $2 → $4 input, $10 → $15 output). Data residency endpoints add a 10% uplift for models released on or after 2026-03-05, as do FedRAMP endpoints. Priority, renamed "Fast" on 2026-07-30, is 2x Standard (gpt-6.1-sol $4/$20). — [OpenAI API pricing](https://developers.openai.com/api/docs/pricing)
- Gemini: 3.1 Pro Preview $2/$12 (≤200k) vs $4/$18 (>200k). Priority is about 1.8x Standard. Gemini 4 Carbon/Argon double on 2026-12-01 ($2/$10 → $4/$20), and 3.8 Flash doubles on 2027-01-01. 2.5 Pro/Flash are deprecated and route to newer models on 2026-12-01. — [Gemini pricing](https://aistudio.google.com/docs/pricing)
- Bedrock: Priority +75%, Flex −50%, Batch −50%. A non-Claude example shows Global vs US cross-region inference at about +10%. Provisioned throughput for Anthropic requires contacting the account team. — [Bedrock pricing](https://aws.amazon.com/bedrock/pricing/)

### Inferences
- Agere's spend accounting should record, per call: model, effort, TTL, cache read/write tokens, batch/standard tier, residency multiplier and tokenizer generation. Without these, cost-per-question comparisons across rungs are not reproducible.
- Healthcare deployments will likely require US residency, so all list prices above should be uplifted 10% in Agere's cost model.
- The announced Gemini price doubling (Dec 2026 / Jan 2027) is a reason not to anchor cost baselines on current Gemini 4 list prices.

### Gaps
- Per-tier Anthropic/OpenAI message rate limits (RPM/TPM by usage tier) were not opened.
- Bedrock's Claude-specific price table and the Azure OpenAI and Vertex Claude price tables were not extracted. A Bedrock note says Sonnet 5.5 cache-read pricing drops 50% from 2026-10-07, without a base rate.

## Q8. Current per-token list prices of the latest models (as of 2026-11-17, Standard tier, USD per 1M tokens)

### Takeaway
The current top tier ranges from $8–$10 input / $40–$50 output (Claude Fable 5.2, gpt-6-astra). Mid "workhorse" frontier models cost $2–$4 input / $10–$20 output (Opus 5.5, Sonnet 5.5, gpt-6.1-sol, Gemini 4 Carbon/Argon). Small models cost about $0.10 / $0.50 (Haiku 5.5, gpt-6-luna). Cached-read rates now run as low as 0.025x–0.05x on some flagships.

### Cited Findings
**Anthropic** ([Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing))

| Model | Input | Cache read | Output | Notes |
|---|---|---|---|---|
| Claude Fable 5.2 / Mythos 5.2 | $8 | $0.20 | $40 | Mythos is limited availability |
| Claude Opus 5.5 | $4 | $0.20 | $20 | |
| Claude Sonnet 5.5 | $2 | $0.10 | $10 | |
| Claude Sonnet 5 | $2 | — | $10 | Introductory price made permanent |
| Claude Haiku 5.5 | $0.10 | $0.01 | $0.50 | Up to 100k prompt tokens |

**OpenAI** ([OpenAI API pricing](https://developers.openai.com/api/docs/pricing))

| Model | Input | Cached input | Output |
|---|---|---|---|
| gpt-6-astra | $10 | $1.00 | $50 |
| gpt-6.1-sol | $2 | $0.10 | $10 |
| gpt-6.1-terra | $1 | $0.05 | $5 |
| gpt-6-luna | $0.10 | $0.01 | $0.50 |
| gpt-5.4-mini | $0.75 | $0.075 | $4.50 |
| gpt-5.4-nano | $0.20 | $0.02 | $1.25 |

The openai.com/api/pricing page returned 403; the figures come from the developers.openai.com pricing page.

**Google** ([Gemini pricing](https://aistudio.google.com/docs/pricing))

| Model | Input | Output | Notes |
|---|---|---|---|
| Gemini 4 Carbon / Argon | $2 | $10 | Rises to $4 / $20 on 2026-12-01 |
| Gemini 4 Hydrogen | $1.50 | $7.50 | |
| Gemini 3.8 Flash | $0.75 | $3.75 | Rises to $1.50 / $7.50 on 2027-01-01 |
| Gemini 3.1 Pro Preview | $2 | $12 | ≤200k prompt tokens |
| Gemini 3.1 Flash-Lite | $0.25 | $1.50 | |

The Gemini page shows no "last updated" date.

### Inferences
- An illustrative frontier SQL call of 20k cached-prefix input, 2k fresh input and 1.5k output costs about $0.042 on Opus 5.5 with a cache hit (20k×$0.20 + 2k×$4 + 1.5k×$20, per MTok), against about $0.118 with no cache. This is my arithmetic, excluding thinking tokens and the residency uplift. Thinking output at `high` effort can dominate, which is why the effort sweep matters.

### Gaps
- Azure OpenAI and Vertex AI list prices were not checked. Bedrock Claude list prices were not extractable from the page.
- "Latest model" designations are taken as listed on the pages. No independent benchmark comparison between these 2026 models was gathered here.

## Summary of recommendations (feature → Agere use → verdict)

| Feature | Agere use | Verdict | Baseline to beat |
|---|---|---|---|
| Prompt caching (Anthropic explicit 1h/5m; OpenAI 30m/12h) | Cache the semantic-layer/metric/guardrail prefix on the frontier SQL rung; cache note documents across extraction passes | **Trial** | No cache; measure hit rate and $/question |
| Batch API (50% off) | Nightly reserved-eval re-runs, recipe promotion, note backfills | **Trial** on non-PHI; check retention before PHI (Anthropic batch not ZDR) | Synchronous standard calls |
| Strict JSON / strict tool use | SQL plan envelope, extraction envelope | **Trial** with reasoning kept outside the schema | Plain JSON prompt + validator + retry |
| Anthropic Citations (custom content) | Exact span evidence for note reading | **Trial** (cannot combine with structured outputs) | Prompted quote + offset JSON with string verification |
| OpenAI file search / Gemini Search grounding | — | **Park** (file search) / **Skip** (web grounding) | — |
| Effort / adaptive thinking | Pin effort per rung; sweep low/medium/high | **Trial** | Default effort; execution-feedback loop at low effort |
| Cascades / routing | Validate Agere ladder vs always-frontier | **Trial** (own ladder); **Skip** vendor auto-routers | Always-largest model |
| Token counting (free) | Pre-call budget guard, routing on prompt size | **Trial** | No pre-check |
| Fast / Priority tiers | — | **Skip** (speed is deprioritized) | — |
| US data residency (1.1x) | Deployment prerequisite | Budget for it | — |
