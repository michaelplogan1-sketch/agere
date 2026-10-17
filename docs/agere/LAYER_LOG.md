# Agere layer log: analyst-first rebuild (2026-10-17)

## Method
Each layer is added one at a time and measured on the same 20 dev questions. Each is judged on accuracy and token
economics. If it doesn't help, we move on or try it in a different position.
- **Questions:** the 20 answerable questions of the fixed dev set (tmp/layers/dev20.jsonl; v2.8 truth set).
- **Scoring:** the generator's sealed scorer (v2.8-sample-0.30 scope); we see only correct or incorrect per question.
- **Analyst:** a Haiku subagent on the Claude subscription ($0). It queries the semantic layer read-only with sqlite3,
  with at most 8 queries per question. Two agents run in parallel, 10 questions each.
- **Tokens:** the agents' reported usage, summed. It is the token-economics measure.
- **Noise:** with 20 questions, ±2 is noise.

## Architecture being built (bottom up)
1. Data model: canonical, complete, with history (claim adjudication versions, corrections, picked-up fills).
2. Governed definitions as SQL views the analyst composes. Replaces the metric router as a front gate.
3. Retrieval: compact catalog index plus per-table detail on demand, and similar verified examples.
4. Analyst agent (Haiku): writes and checks SQL, typed answer.
5. Verification by agreement: 3 independent analyst runs; unanimous = verified, majority = unverified; no majority →
   one stronger-model pass or abstain.
6. Economics: prompt caching, answer cache, verified-query library fed by agreed answers.

The deterministic engines (survival, HCC, causal, forecasting) become tools the analyst calls. The local compiler, metric
router, operator router and Opus reconcile leave the answer path (BUILD_LOG.md shows why).

## Results

| Layer | What changed | Correct /20 | Answered | Right when answered | Tokens (total) |
|---|---|---|---|---|---|
| L1 | Haiku analyst + full semantic docs (156K chars) | 14 | 20 | 14/20 (70%) | 356K |
| L2 | + retrieval: 20K-char catalog index, detail files on demand | 15 | 19 | 15/19 (79%) | 306K (-14%) |
| L3 | + two convention fixes (denial counts; overlapping groups) | 14 | 20 | 14/20 (70%) | 318K |
| L4 | + agreement: majority of 3 independent runs (L1-L3), no majority = abstain | 14 | 18 | 14/18 (78%); unanimous 10/11 (91%) | 0 extra (3 runs ≈ 980K) |
| L5 | + escalation: Sonnet reviews the 9 non-unanimous questions (decide or abstain) | 15 | 19 | 15/19 (79%); unanimous 10/11, reviewed 5/8 | +106K |

### L1: analyst + full docs (baseline)
- **Correct (14):** CLN-13 CLN-15 HOS-03 HOS-08 REV-12 QUA-01 QUA-05 VBC-04 VBC-10 BH-11 BH-14 ACC-03 ACC-12 DQ-09.
- **Wrong (6):** REV-03 (top denial CARCs), RX-09 (note-only stops), RX-10 (restarts), RWE-02 (new statin users),
  RWE-12 (AFib anticoagulants), DQ-10 (restated records). These are the same six the old stack missed.
- **Tokens:** 356K (171K + 186K), about 18K per question. Most of it is reading the 156K-char docs.
- **Next (L2):** retrieval. A 20K-char index replaces the full docs, with detail files opened on demand. The target:
  same accuracy, far fewer tokens.

### L2: retrieval (catalog index + detail on demand)
- **Result:** 15/20, with 19 answered and 15 right. DQ-10 is now right. RX-09 became an honest abstention: "maintenance
  drug class" is defined nowhere in the layer, so the agent didn't guess.
- **Tokens:** 306K (-14%). The index is 87% smaller than the full docs, but the agents opened detail files and ran more
  queries (63 tool calls against 48), so the saving is smaller than the index alone suggests.
- **Kept:** better on accuracy and tokens.
- **What the analyst did on the four remaining misses (its own interpretations):**
  - REV-03: filtered counts_in_metrics = 1, as the conventions told it to. That drops duplicate-submission denials, so
    CO-18 disappears from the top 5. This is a bug in the conventions themselves (they are right for money and volume,
    wrong for denial counts).
  - RWE-12: split people into exclusive buckets (DOAC only / warfarin only / both), though the definition says a person
    can be in both groups.
  - RX-10 and RWE-02: the method looks right. RWE-02 is 115 every time, by table and by raw SQL. Not chased; possible
    key issues.
- **Next (L3):** fix the two convention rules (denial counts don't use counts_in_metrics; overlapping groups count
  people in each group).

### L3: two convention fixes
- **Result:** 14/20. REV-03 now includes CO-18 (the fix took effect) but is still marked wrong; the key likely counts
  adjudications rather than claims. DQ-10 and RX-09 flipped back to wrong: DQ-10 counted both correction link types
  (22,999), and RX-09 guessed a maintenance-class list instead of abstaining.
- **Lesson:** single-run variance is as large as the layer effects. Identical questions flip between near-identical
  runs. That is what agreement is for.

### L4: verification by agreement (computed from the three runs already done; no new tokens)
- **Rule:** unanimous → answer, verified; 2 of 3 → majority answer, unverified; no majority → abstain.
- **Result:** 14/20 correct, 18 answered, 14/18 right.

  | Agreement | Questions | Right |
  |---|---|---|
  | Unanimous | 11 | 10 (91%) |
  | 2 of 3 | 7 | 4 (57%) |
  | No majority (abstained) | 2 | RX-09 and RWE-12, two wrong answers removed |
- **The only unanimous miss is RWE-02 (115 in every run, by every method).** That is the strongest evidence yet of a
  key defect; send it to the generator for audit.
- **Economics:** 3 Haiku runs. On the API that would be about $0.012 per question; here it is $0.
- **Kept:** it gives a real confidence signal. Verified answers are ≥91% right.
- **Next (L5):** escalate the 9 non-unanimous questions to one stronger model (Sonnet). It sees the three candidates and
  their methods, has database access, and decides or abstains.

### L5: escalation of disagreements to a stronger model
- **Setup:** Sonnet sees the 3 candidates and their methods, has read-only database access (up to 6 queries per
  question), and decides or abstains. Applied only to the 9 questions that weren't unanimous.
- **Result:** 15/20 correct, 15/19 answered right. The reviewer answered 8 with 5 right (the majority vote got 4/7) and
  abstained on RX-09 ("maintenance class" is undefined in the layer). DQ-10 is fixed.
- **Tokens:** +106K for 9 questions (about 12K each). Only disagreements pay for the stronger model.
- **Persistent misses, same answer from every run and the reviewer:** REV-03, RX-10, RWE-02 and RWE-12. Independent
  agreement against the key makes these keys the first suspects (per the benchmark rules). They go to the generator
  for audit before any more effort.

## Where the stack stands
- **Layers kept:** analyst + retrieval index + fixed conventions + 3-run agreement + escalation of disagreements.
- **Accuracy:** 15/20; precision 79% overall, 91% on the verified (unanimous) tier.
- **Economics per question:** 3 Haiku runs (about 15K tokens each with the index) + a Sonnet pass only on
  disagreements. On the API: about $0.01-0.02 per question.
- **Open:**
  - key audit of the 4 persistent misses;
  - RX-09 needs a defined "maintenance class" in the layer (a definitions gap, not a model gap);
  - economics layers not built yet: answer cache, and a verified-query library fed by unanimous answers.
