# Agent memory, context engineering and long-running agent design (frontier labs and academia), as of 2026-11-17

Scope note: every page cited below was opened with a fetch tool on 2026-11-17. For several arXiv entries only the abstract page was read. Those are marked "(abstract only)". No PDFs were downloaded. Items described from background knowledge without opening a page are confined to the Gaps sections and are labelled there.

## Q1. What components of agent memory does the Anthropic page define, and what does it recommend?

### Takeaway
"The components of agent memory" (Anthropic Engineering, Lance Martin, 6 Nov 2026) defines memory as readable context that persists across sessions. It describes four layers (transcripts, memory notes, memory representations such as linked wikis, and injected context) and an offline "dreaming" pass that checks notes against transcripts and reorganises them. It does not use the episodic, semantic or procedural taxonomy, and it does not define a formal write/retrieve/update/forget API. It shows large gains from memory on a database task, and it shows that wrong notes persist unless something checks them against the source of record.

### Cited Findings
- **Date and author:** published 6 Nov 2026 by Anthropic Engineering. The author is Lance Martin. Topics are agents, context engineering and agent memory. — [Anthropic: The components of agent memory](https://www.anthropic.com/research/components-of-agent-memory)
- **Definition:** memory is "knowledge that Claude gains from experience". It is retained across sessions and saved as readable context. The definition explicitly excludes weight updates, in-context learning and compaction. — [Anthropic](https://www.anthropic.com/research/components-of-agent-memory)
- **Four layers, bottom to top:**
  1. Transcripts: a searchable event log.
  2. Memory notes: salient observations Claude writes, such as files in a memory folder.
  3. Memory representations: for example, a wiki of linked pages.
  4. Injected context: loaded into every session.
  — [Anthropic](https://www.anthropic.com/research/components-of-agent-memory)
- **Operations are described as behaviours, not as an API:**
  - Write: tag salient events or write notes.
  - Retrieve: read memory before answering, or search transcripts and wikis.
  - Correct: dreaming checks notes against transcripts and rewrites them.
  - Consolidate and prune: merge duplicates, prune wiki pages, repoint links.
  - Promote and demote: move notes into the injected layer, or push details down and leave a link.
  - Forgetting is not a named operation. It appears only in examples, such as Sonnet 3.7 deleting files.
  — [Anthropic](https://www.anthropic.com/research/components-of-agent-memory)
- **Dreaming:** an offline pass that reflects on logs and reorganises memory. It builds on Letta and UC Berkeley's sleep-time compute idea. — [Anthropic](https://www.anthropic.com/research/components-of-agent-memory)
- **CL-Bench database task, mean reward without memory and with memory:**

  | Model | Stateless | With memory |
  |---|---|---|
  | Opus 5.5 | 0.655 | 0.838 |
  | Sonnet 5.5 | 0.644 | 0.833 |
  | Opus 5 | 0.564 | 0.828 |
  | Opus 4.7 | 0.383 | 0.700 |
  | Sonnet 5 | 0.306 | 0.610 |
  | Sonnet 4.6 | 0.330 | 0.364 (0.641 with refined prompting) |

  Sonnet 4.6 read its memory files less than half the time. Sonnet 5 read them 9 times in 10 without being prompted. — [Anthropic](https://www.anthropic.com/research/components-of-agent-memory)
- **A rigid writing rule reduced the gain:** "record only failures" changed the memory gain by −0.234 for Opus 5, −0.150 for Opus 4.7 and −0.020 for Sonnet 4.6. — [Anthropic](https://www.anthropic.com/research/components-of-agent-memory)
- **Wiki over a corpus of about 350,000 files (about 420M tokens):** 25 questions, 128 Opus 5 sessions.
  - Wiki alone rose from 13% to 48% of graded criteria.
  - Wiki plus search rose from 54% to 61%, against 53% for search alone.
  - Wiki plus search saved about 20,000 tokens per question.
  - On 24 merger questions, the wiki scored 87% against 73% for search alone on multi-document and judgment items.
  - On the LAB benchmark, one wiki grew to 1.77× its 115 KB corpus and left some pages unlinked. Another task showed no clear gain over search.
  — [Anthropic](https://www.anthropic.com/research/components-of-agent-memory)
- **Granularity matters:** a shared table captured 41 of 57 contract facts (72%). One page per contract, with terms quoted verbatim, captured 57 of 57 (100%). — [Anthropic](https://www.anthropic.com/research/components-of-agent-memory)
- **Wrong memory persists unless it is checked against the source:**
  - In the Mt. Moon test, a note wrongly placed the PokéCenter on the east side. The raw-memory run looped and never reached Cerulean City. The run with dreaming-corrected memory reached Cerulean at action 1,935. The no-memory run never reached it.
  - Quote: "The transcript gave Claude a ground truth to validate its memory against."
  - Opus 4.7 marked facts as checked or unconfirmed. Opus 5 recorded how it knew a rule was true.
  - Concurrent writers to one shared file caused overwrites, where "one agent's save erases another's lines".
  — [Anthropic](https://www.anthropic.com/research/components-of-agent-memory)
- **Recommendations on the page:**
  - Tell the model what memory is for, but not how to use it.
  - Avoid fixed rules such as "record only failures".
  - Prompt older models to read memory.
  - Keep the injected layer small and push details down behind links.
  - Give each entity its own page rather than a shared table.
  - Treat transcript search as the foundation and add representations selectively.
  - Focus dreaming on information likely to change and tune its cadence to access frequency. This last point is proposed, not tested.
  - On cost: dreaming "requires compute", injected memory uses context tokens, and Claude Code loads only the first 200 lines or 25 KB of MEMORY.md.
  — [Anthropic](https://www.anthropic.com/research/components-of-agent-memory)
- **What the page does not cover:** privacy, access control or retention of user data. — [Anthropic](https://www.anthropic.com/research/components-of-agent-memory)
- **Related Anthropic guidance, "Effective context engineering for AI agents" (29 Sep 2025):**
  - Defines context engineering as curating "the optimal set of tokens" at inference, and describes "context rot", where recall falls as context grows.
  - Techniques: compaction, structured note-taking (for example NOTES.md), sub-agents returning 1,000–2,000-token summaries, just-in-time retrieval through lightweight references (paths, queries) with a recommended hybrid approach, and tool-result clearing.
  - The article gives no benchmark numbers.
  — [Anthropic Engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- **Context management launch post (29 Sep 2025):**
  - Memory tool plus context editing improved an internal agentic-search eval by 39% over baseline. Context editing alone improved it by 29%.
  - On a 100-turn web-search eval, context editing cut token use by 84%.
  - These are internal evals with no public dataset.
  — [Claude blog: Context management](https://claude.com/blog/context-management)
- **Memory tool documentation:**
  - Client-side, file-based (`/memories`) commands: view, create, str_replace, insert, delete, rename.
  - Eligible for zero data retention and for HIPAA.
  - Security guidance: strip sensitive data before writing, cap file sizes, expire files not accessed in a long time, and validate every path against traversal (including URL-encoded `../`).
  - Multi-session pattern: an initializer session sets up a progress log and a feature checklist. A feature is marked complete only after end-to-end verification.
  — [Claude docs: Memory tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool)

### Inferences
- The four layers map onto what Agere already has:
  - Transcripts: question and run logs, plus versioned evidence in the decision context.
  - Memory notes: customer preferences and clarifications.
  - Representations: governed definitions and the recipe library.
  - Injected context: active tenant definitions.
- Dreaming, the most transferable idea, is an offline job that re-validates memory against the source of record. For Agere, that means re-running saved recipes against the current definition version and the warehouse, and flagging drift.
- The CL-Bench gains are large but come from a lab-internal task. They show that memory helps a capable model on repeated database work. They do not show that free-form memory beats Agere's existing saved-recipe reuse. That comparison has to be run on Agere's own question set.
- "One page per entity, verbatim" supports keeping one governed record per definition or recipe, quoted exactly, rather than summarised facts in a shared table.

### Gaps
- The page reports no cost-per-task figures for dreaming and no comparison with a plain embedding retrieval baseline. It compares against stateless runs and transcript search only.
- No public dataset or code for CL-Bench was identified.

## Q2. Is there evidence that structured memory beats plain retrieval or long context, with numbers and costs?

### Takeaway
On LoCoMo and LongMemEval, structured memory (Mem0, Zep) cuts tokens and latency by roughly 90% or more relative to full context. Its accuracy is mixed. On LoCoMo, full context still scored highest (72.9 J against Mem0's 66.9). On LongMemEval, Zep beat full context by 8–11 points. Against tuned plain RAG, the gain is modest (Mem0 66.9 against best RAG 61.0). Vendor-authored papers dominate this evidence.

### Cited Findings
- **Mem0 (28 Apr 2025), LoCoMo, LLM-judge J score, mean over 10 runs:**

  | Method | Overall J | p95 latency | Tokens per query |
  |---|---|---|---|
  | Full context | 72.90 | 17.1 s | 26,031 |
  | Mem0g (graph variant) | 68.44 | 2.59 s | 3,616 |
  | Mem0 | 66.88 | 1.44 s | 1,764 |
  | Zep | 65.99 | 2.93 s | 3,911 |
  | Best RAG (k=2, 256-token chunks) | 60.97 | 1.91 s | not stated |
  | LangMem | 58.10 | 60.4 s | 127 |
  | OpenAI memory | 52.90 | 0.89 s | 4,437 |
  | A-Mem | 48.38 | 4.37 s | 2,520 |

  — [Mem0 paper (HTML)](https://arxiv.org/html/2504.19413v1)
- **Mem0 headline claims:** "91% lower p95 latency" and more than 90% token savings against full context, and about 26% relative improvement over OpenAI memory. — [arXiv abstract](https://arxiv.org/abs/2504.19413)
- **Mem0 limitations it reports itself:**
  - LoCoMo has only 10 conversations, and the adversarial category was excluded.
  - The LLM judge is stochastic.
  - The OpenAI baseline's memories often lacked timestamps.
  - Zep beat Mem0 on open-domain questions.
  - The graph variant gave no gain on multi-hop questions.
  - Storage: Mem0 about 7k tokens per conversation, Mem0g about 14k, Zep more than 600k.
  — [Mem0 HTML](https://arxiv.org/html/2504.19413v1)
- **Zep/Graphiti (20 Jan 2025), LongMemEval:**

  | Model | Memory | Accuracy | Latency | Context tokens |
  |---|---|---|---|---|
  | gpt-4o-mini | Full context | 55.4% | 31.3 s | about 115k |
  | gpt-4o-mini | Zep | 63.8% | 3.20 s | about 1.6k |
  | gpt-4o | Full context | 60.2% | 28.9 s | about 115k |
  | gpt-4o | Zep | 71.2% | 2.58 s | about 1.6k |

  — [Zep paper HTML](https://arxiv.org/html/2501.13956v1)
- **Zep did worse on some question types:** single-session-assistant fell from 94.6% to 80.4% with gpt-4o. Knowledge-update fell from 76.9% to 74.4% with gpt-4o-mini. The paper calls this an unresolved exception. On DMR, Zep scored 94.8% against MemGPT's 93.4%. — [Zep HTML](https://arxiv.org/html/2501.13956v1); [abstract](https://arxiv.org/abs/2501.13956)
- **LongMemEval benchmark (Oct 2024, ICLR 2025):**
  - 500 questions testing five abilities: information extraction, multi-session reasoning, temporal reasoning, knowledge updates and abstention.
  - Commercial assistants and long-context LLMs show about a 30% accuracy drop across sustained interactions.
  - Proposed fixes: session decomposition, fact-augmented key expansion and time-aware query expansion.
  - Gain figures are not on the abstract page. (abstract only)
  — [arXiv 2410.10813](https://arxiv.org/abs/2410.10813)
- **A-MEM (Feb 2025, NeurIPS 2025):** Zettelkasten-style notes with automatic linking and "memory evolution" that rewrites older notes. It claims improvements over baselines across six models (abstract only). Mem0's independent run placed A-Mem last on LoCoMo at 48.38 J. — [A-MEM abstract](https://arxiv.org/abs/2502.12110); [Mem0 HTML](https://arxiv.org/html/2504.19413v1)
- **Anthropic wiki versus search:** 61% with wiki plus search against 53% with search alone, saving about 20k tokens per question. On one LAB task the wiki gave no gain. — [Anthropic](https://www.anthropic.com/research/components-of-agent-memory)

### Inferences
- These are conversational-chat benchmarks: recalling what the user said. They do not test whether a reused SQL procedure is correct. LoCoMo and LongMemEval are therefore weak evidence for Agere's main memory use, recipe reuse. LongMemEval's knowledge-update and abstention categories are the closest analogue to definition changes, and Zep regressed on knowledge-update with one model.
- The consistent finding is the cost result: about 90% fewer tokens and latency against full context, with accuracy that is similar or somewhat lower. This matches Agere's owner rule of beating a simple baseline on cost. The accuracy comparison has to come from Agere's own evaluation.
- Mem0 reported Zep at 66.0 and Zep reported itself well ahead of baselines. The benchmark numbers are vendor-run and should be treated as indicative only.

**Per-idea assessment (benchmark memory systems)**

| Idea | Agere problem | Maturity | Recommendation |
|---|---|---|---|
| Mem0-style fact extraction with ADD/UPDATE/DELETE/NOOP | Remembering customer preferences and clarifications across sessions | Open-source plus SaaS; LoCoMo evidence only | Park. A hosted service would breach the no-new-services boundary. The pattern could be reproduced locally if preference recall becomes a measured failure. |
| Zep/Graphiti temporal knowledge graph | Definition versions, provenance | Open-source Graphiti; vendor-run benchmarks | Trial only the bi-temporal schema idea, inside Agere's existing store (see Q3). Skip the graph engine. |
| A-MEM | Linking memories | Research | Skip. It scored weakest in an independent rerun. |
| Long context (full history) | Baseline | Available | Keep as a baseline arm in evaluation. |
| Plain RAG over past Q&A | Baseline | Available | Keep as the required simple-baseline arm. |

### Gaps
- Mem0's and Zep's own pages do not report their LongMemEval and LoCoMo numbers in a common, independently run setup. Zep has publicly disputed Mem0's configuration of Zep (background knowledge; that dispute page was not opened).
- No benchmark found tests memory of executable analytic procedures (SQL recipes) under schema or definition change.
- Recent (2026) LongMemEval leaderboards were not opened.

## Q3. How do these systems handle memory invalidation, versioning and conflicts (Agere's stale-recipe and definition-change risks)?

### Takeaway
The best-engineered mechanism is Zep's bi-temporal edge invalidation: old facts are marked invalid rather than deleted, each fact links back to its source episode, and history stays queryable. Mem0 and the OpenAI cookbook resolve conflicts with an LLM using recency precedence. Anthropic's dreaming re-validates memory against transcripts. None of these re-executes a stored procedure to check it, which is what Agere needs for recipes.

### Cited Findings
- **Zep/Graphiti bi-temporal model:**
  - Timeline T (valid time) carries t_valid and t_invalid. Timeline T′ (transaction time) carries t′created and t′expired.
  - On a contradiction that overlaps in time, the older edge's t_invalid is set to the new edge's t_valid. Old facts are kept as history.
  - Episodic edges with bidirectional indices trace every fact to its source episode, so it can be cited.
  — [Zep HTML](https://arxiv.org/html/2501.13956v1)
- **Mem0 conflict handling:**
  - For each candidate fact, Mem0 retrieves the top 10 similar memories and asks an LLM to choose ADD, UPDATE, DELETE or NOOP.
  - A contradiction leads to DELETE.
  - The graph variant marks conflicting relations invalid instead of removing them.
  — [Mem0 HTML](https://arxiv.org/html/2504.19413v1)
- **OpenAI Agents SDK cookbook (context personalization):**
  - Separate global and session notes. At session end, an LLM consolidates them, preferring the most recent `last_update_date`.
  - Precedence: the latest user message overrides session memory, which overrides global memory. Global notes are "advisory".
  - On a material conflict, the agent asks one focused question.
  - Pruning stale, superseded or low-confidence notes is essential.
  - Recommended evals cover distillation precision, recall and safety; injection over-influence; and consolidation "non-invention".
  — [OpenAI cookbook](https://developers.openai.com/cookbook/examples/agents_sdk/context_personalization)
- **Anthropic dreaming:** checks notes against transcripts. Models marked facts "checked/unconfirmed" or "confirmed after grading". A wrong note was trusted in later sessions until corrected. — [Anthropic](https://www.anthropic.com/research/components-of-agent-memory)
- **Anthropic memory tool docs:** recommend periodic expiry of files not accessed in a long time. — [Claude docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool)
- **Anthropic multi-session harness pattern:** mark a feature complete only after end-to-end verification. — [Claude docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool)

### Inferences
- A concrete pattern for Agere, which is a design inference rather than a published method:
  - Give every saved recipe and remembered customer fact valid-time and transaction-time fields, the definition version it depends on, and a link to its source run in the decision context.
  - When a governed definition gets a new version, set t_invalid on dependent recipes, or move them to "needs re-verification", instead of reusing them silently.
  - An offline re-verification job (Agere's equivalent of dreaming) re-executes each recipe against the reference cohort and compares results.
- This addresses both known risks directly. It needs no LLM for the invalidation trigger, because the trigger is a deterministic dependency check, so its cost is near zero.
- The OpenAI precedence rule (current request, then session, then tenant default) plus "ask one focused question on conflict" fits Agere's clarifying conversation.
- Evaluation design: seed definition changes and a deliberately wrong saved recipe. Measure silent-reuse rate, detection rate and false invalidations across four arms: no memory, embedding retrieval of past Q&A, current recipe reuse, and versioned recipe reuse with invalidation.

### Gaps
- No found source measures stale-memory error rates under schema or definition change for analytic agents.
- LongMemEval's knowledge-update gains for its proposed fixes were not on the abstract page.

## Q4. Procedural memory and skill libraries as an analogue to saved recipes: does reusing learned procedures improve accuracy or cost?

### Takeaway
Yes, in web, embodied and software-engineering tasks. Agent Workflow Memory reported +24.6% (Mind2Web) and +51.1% (WebArena) relative success. ReasoningBank reported +8.3% on WebArena and +4.6% on SWE-Bench-Verified over no memory, with about 3 fewer steps per task. Voyager's code skill library drove 3.3× more unique items and up to 15.3× faster milestones. Anthropic's Agent Skills is the production analogue, with no published accuracy numbers. ReasoningBank's finding that learning from failures beats storing only successful routines bears directly on Agere's wrong-recipe risk.

### Cited Findings
- **Agent Workflow Memory (Sep 2024; Wang, Mao, Fried, Neubig):**
  - Induces reusable workflows offline or online and supplies them selectively.
  - +24.6% relative success on Mind2Web and +51.1% on WebArena.
  - Fewer steps on WebArena (no figure given).
  - Beats baselines by 8.9–14.0 absolute points as the train/test gap widens. (abstract only)
  — [arXiv 2409.07429](https://arxiv.org/abs/2409.07429)
- **ReasoningBank (Google Research):**
  - arXiv posted 29 Sep 2025, revised Mar 2026, ICLR 2026. Blog post 21 Apr 2026.
  - Distils strategies from self-judged successes and failures.
  - With Gemini-2.5-Flash on ReAct: +8.3% success on WebArena and +4.6% on SWE-Bench-Verified over a memory-free agent, saving almost 3 steps per task on SWE-Bench-Verified.
  - Memory-aware test-time scaling (k=5) added +3% and 0.4 fewer steps on WebArena.
  - Claims to beat trajectory memory (Synapse) and success-only workflow memory (AWM). The exact baseline numbers are in a figure only.
  - Robust to noise in the self-judgment.
  — [Google Research blog](https://research.google/blog/reasoningbank-enabling-agents-to-learn-from-experience/); [arXiv](https://arxiv.org/abs/2509.25140)
- **Voyager (May 2023):**
  - An "ever-growing skill library of executable code".
  - About 3.3× more unique items, 2.3× longer distances and up to 15.3× faster tech-tree milestones than prior state of the art.
  - Reuses the library zero-shot in a new world. (abstract only; ablation not read)
  — [arXiv 2305.16291](https://arxiv.org/abs/2305.16291)
- **Anthropic Agent Skills (16 Oct 2025):**
  - A skill is a folder with SKILL.md (name and description frontmatter), scripts and resources.
  - Progressive disclosure: only the name and description are preloaded, and the full content loads when relevant.
  - No accuracy or cost figures are published. The argument is qualitative: running code is cheaper and more deterministic than generating tokens.
  - Security: malicious skills can exfiltrate data, so install from trusted sources and audit.
  - Future direction: agents creating and evaluating their own skills.
  — [Anthropic Engineering](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)
- **Anthropic CL-Bench:** a rigid "record only failures" rule reduced memory gains, so the policy for what gets written matters. — [Anthropic](https://www.anthropic.com/research/components-of-agent-memory)

### Inferences
- Agere's verified SQL recipes are already a governed, executable skill library, closer to Voyager's code skills than to AWM's text workflows. The literature supports reuse for accuracy and cost, but always against a no-memory baseline, never against an already-curated library.
- Agere's gap is not reuse itself. It is quality control on admission and on retirement.
- **Trial:** a ReasoningBank-style "pitfall" memory. Store failed or rejected recipe attempts and reviewer corrections as negative lessons ("for metric X, do not join on Y"), attached to the definition. This is the closest published evidence for reducing repeat errors. It needs an Agere evaluation against current recipe reuse.
- **Trial:** Skills-style progressive disclosure for the recipe and definition catalogue. Inject only names and descriptions, then load the full SQL on demand. This is a token-cost lever that can be measured directly.
- **Park:** autonomous skill or recipe creation without human verification. Agere's verified-recipe gate is stricter than any published system and should stay.

### Gaps
- AWM and ReasoningBank exact baseline tables were not read. The PDF and HTML were not opened for those.
- No study found compares learned-procedure reuse with a curated, human-verified procedure library, or measures how often a wrong stored procedure propagates.

## Q5. Safety and privacy of memory (poisoning, cross-user leakage) and mitigations for PHI

### Takeaway
Memory poisoning is practical against a healthcare agent. MINJA injected malicious records into EHRAgent's memory (MIMIC-III and eICU) through ordinary queries alone, with a 95–98% injection rate and a 57–90% attack success rate. LLM-judge detection did not generalise. Vendor guidance pushes isolation, integrity and sanitisation onto the application. Session IDs are not authentication, encryption does not guarantee integrity, and delimiters are "not a security boundary".

### Cited Findings
- **MINJA (Mar 2025, revised Feb 2026):**
  - The attacker only queries the agent and has no memory access.
  - Average injection success 98.2% and attack success 76.8%.
  - EHRAgent (GPT-4): injection success 95.6% on MIMIC-III and 98.5% on eICU; attack success 57.0% and 90.0%. Victim and target pairs were patient IDs and medications.
  - Defences:
    - A GPT-4o judge with an EHR-specific prompt caught 131/135 malicious queries on MIMIC-III, but 0/135 on another agent.
    - A general prompt raised 34 false positives on 50 benign EHR queries.
    - The authors argue embedding-level sanitisation fails because poisoned and benign records are entangled. This argument is untested.
  — [MINJA HTML](https://arxiv.org/html/2503.03704)
- **OpenAI Agents SDK Sessions:**
  - A session ID "does not authenticate or authorize". The application must control access.
  - `EncryptedSession` adds encryption and TTL but no integrity or ordering guarantees.
  — [OpenAI Agents SDK: Sessions](https://openai.github.io/openai-agents-python/sessions/)
- **OpenAI cookbook:** block sensitive data (passport, payment, full DOB) and instruction-like content at capture, consolidation and injection. Delimiters are "not a security boundary". Log blocked-write rate. — [OpenAI cookbook](https://developers.openai.com/cookbook/examples/agents_sdk/context_personalization)
- **Anthropic memory tool docs:**
  - Client-side storage keeps retention under the application's control. Eligible for zero data retention and HIPAA.
  - "Claude usually refuses to write sensitive information", but the docs recommend validation that strips sensitive data.
  - Map `/memories` to a per-user store, prevent path traversal, cap sizes and expire files.
  — [Claude docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool)
- **Anthropic Agent Skills:** malicious skills can direct exfiltration, so audit them. — [Anthropic Engineering](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)
- **Anthropic memory components page:** concurrent writers overwrite each other, and wrong notes are trusted later. — [Anthropic](https://www.anthropic.com/research/components-of-agent-memory)

### Inferences
- For PHI, the defensible design is structural, not detection-based:
  - Memory keyed by tenant, and by user where needed, enforced by authorisation in the storage layer.
  - Remembered artefacts restricted to governed objects (definitions, recipes, preferences) that pass a PHI scrubber and a schema check.
  - No free-text patient-level content stored in memory.
  - Writes to shared, tenant-level memory, such as a new saved recipe, require the existing verification gate, never an LLM decision alone.
- MINJA shows that a query-only user can plant memories when the agent auto-saves its own successful-looking trajectories. Agere's human- or test-verified recipe admission is a direct mitigation, and it should be preserved.
- Provenance on every memory record (who wrote it, from which run, against which definition version) supports audit and rollback after poisoning.

### Gaps
- Not opened, cited from background knowledge only:
  - AgentPoison (NeurIPS 2024)
  - Microsoft Research or Meta memory-specific papers
  - ChatGPT Memory product documentation or controls
  - MemGPT/Letta papers and sleep-time compute numbers
  - Generative Agents memory stream ablations (recency, importance, relevance retrieval)

  They should be checked if needed. In this session they were left out in favour of the more decision-relevant sources above.
- No source found quantifies cross-tenant memory leakage rates in deployed multi-tenant agents.
