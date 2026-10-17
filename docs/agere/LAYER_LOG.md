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

## Key audit of the 4 persistent misses (generator, 2026-10-17)
- **REV-03:** key defect. The key counted adjudication rows (an upheld appeal repeating a CARC was counted twice). The
  fix is to count distinct claims per CARC; it is written but not yet applied.
- **RWE-02:** definition defect. The key counts each person once, in the year of their FIRST new-user fill after the
  lookback; the published text doesn't say so. The wording fix is pending; the key doesn't change.
- **RWE-12:** label defect. The values are fine, but the key labels are "anticoagulant DOAC" / "warfarin", never stated.
  Labels to be renamed and stated.
- **RX-10:** key confirmed. Every drug class counts, including acute classes such as antibiotics. Gap = next fill date
  minus (fill date + days_supply), with no -1, which conflicts with our convention text (supply_end = fill_date +
  days_supply - 1). Our convention should follow the confirmed definition.
- **Takeaway:** 3 of the 4 "misses" that every run and the reviewer agreed on were key or definition defects, which
  supports the agreement-against-key rule. Applying the generator's fixes needs Mike's direct approval in that session.

## L6: verified-query library (economics), measured on the 10 held-out questions
- **Library:** 11 entries built from the unanimous dev answers. Each entry is the question plus one SQL query that
  reproduces the agreed answer (all 11 re-run and confirmed; RWE-02 flagged as under key audit). 12K characters.
- **Test:** the 10 held-out questions the library never saw, so a gain means the patterns transfer, not that answers
  are memorized.

  | Run | Correct /10 | Right when answered | Tokens | Queries |
  |---|---|---|---|---|
  | L6a: index only | 8 | 8/10 | 156K | 38 |
  | L6b: index + library | 8 | 8/10 | 145K (-7%) | 18 (-53%) |
- **Result:** accuracy unchanged on new questions, with less work and fewer tokens. For comparison on the same 10: the
  old Agere stack got 7, and Haiku over the old semantic layer or Tuva got 2 and 1.
- **Kept:** it improves economics. Repeat or paraphrased questions would gain much more (direct reuse of a confirmed
  query).
- **Also fixed:** the gap convention now says next fill minus (fill_date + days_supply), and every class counts unless the
  question narrows it (from the RX-10 key confirmation).

## Data model: maintenance drug class (2026-10-17)
- **New:** drug_classes.GROUPS['maintenance']: 49 long-term chronic-condition classes (antihypertensives, statins, every
  diabetes class, anticoagulants/antiplatelets, thyroid, antidepressants, antipsychotics, anticonvulsants, lithium,
  COPD/asthma controllers, PPIs/H2, BPH and bladder drugs, bisphosphonates, ADHD, dementia, antiretrovirals, DMARDs, TNF
  inhibitors, contraceptives, nitrates). This is the sense of PBM maintenance-drug lists.
- **Excluded:** acute and as-needed classes (antibiotics, antivirals, opioids, benzodiazepines, NSAIDs, SABA, triptans,
  antiemetics, systemic steroid courses, antihistamines, muscle relaxants, sedatives, smoking cessation, acetaminophen,
  epinephrine auto-injectors).
- **Live:** in drug_class_vocabulary (class_group = 'maintenance'), with a conventions line pointing the analyst to it.
  RX-09 had no definition to use before this.
- **Audit:** ACC-10 and VBC-02 sent for the same key audit (independent runs agree; the scorer disagrees). ACC-10's
  January and October spikes are in the raw records too, so the appointment table build isn't the cause.

## Key audit, round 2 (generator, 2026-10-17): ACC-10 and VBC-02
- **ACC-10:** key defect. The key dropped 1,105 deep-chart appointments whose latest version has only the record's event
  date (no slot_start or scheduled time). The appointments feed is the only scheduling stream (both schemas).
- **VBC-02:** key defect. The key counted superseded and retro-terminated enrollment-span versions alongside their
  replacements. Labels are the delivered product values.
- **Root cause in the key builder:** supersedes_record_id was ignored, which also affects keys built from corrected labs
  and re-adjudicated fills. Fixing it moves VBC-01, VBC-02, VBC-11 and ACC-10 beyond tolerance; 12 others shift within it.
- **Tally:** of the 6 misses every run agreed on, 5 were key defects and 1 was our gap-convention bug (fixed). Waiting
  on Mike's approval in the generator session to rewrite the key files; no re-score until confirmed.

## Re-score on the patched keys (2026-10-17; REV-03, RWE-12, RWE-02 wording and RX-10 wording applied; ACC-10 and VBC-02 still pending)

| Layer | Old keys | Patched keys |
|---|---|---|
| L1 analyst + full docs | 14/20 | 15/20 |
| L2 + retrieval index | 15/20 | 16/20 (16/19 answered right) |
| L3 + convention fixes | 14/20 | 16/20 |
| L4 majority of 3 | 14/20 | 14/20; unanimous 10/11 |
| L5 + Sonnet escalation | 15/20 | 17/20 (17/19 answered right, 89%) |
| L6 held-out 10 | 8/10 | 8/10 (both misses are the pending ACC-10 / VBC-02 key defects) |

- **Remaining dev misses:**
  - RX-10: answered under our old off-by-one gap convention (now fixed); needs a re-run.
  - RWE-02: answered before the definition stated first-new-user-per-person; needs a re-run.
- **Expected once everything is applied:** about 17-19/20 dev and about 10/10 held-out.
- **Lesson:** most of the "accuracy problem" was the test bed. The case for the frozen, tier-based test design (and the
  10 clinical workflow tasks) stands.

## RX-10 / RWE-02 re-run with the corrected definitions (3 independent Haiku runs, 2026-10-17)
- **All three runs agree:** RX-10 = 996, RWE-02 = 74. Unanimous, so no escalation.
- **Full stack on the 20 dev questions:** 18/20 correct, 18/19 answered right (95%). RWE-02 is now correct. RX-09
  abstains. The only miss is RX-10, unanimous (996) against the confirmed key; the earlier 1,019 under the old gap
  formula was also wrong. Flag it for one more key audit (class coverage, or the gap-boundary convention) rather than
  tune to it.
- **Generator feasibility for the 10 clinical workflow tasks:**
  - feasible on v2.8: 1 (HFrEF GDMT gaps), 3 (deep-chart dose vs target), the patient sets for 4-7, and 9;
  - not feasible: anything about reasons (the simulation has no reasons for gaps), 8 (no incident MIs) and 10 (no
    designed conflicts);
  - the generator proposes a v2.13 "clinical decisions" layer and is confirming it with Mike.

## RX-10 key audit, round 3 (2026-10-17)
- **Key defect.** The key assigned drug classes from the generator's private drug catalog, which the warehouse doesn't
  ship, so it couldn't be reproduced by anyone. The earlier "key confirmed" was wrong.
- **New definition:** 16 named chronic classes, matched on drug name; ARNI is not ARB; everything else (antibiotics,
  unclassified) is ignored; superseded claim versions are replaced.
- **RX-05 (top drug classes)** probably has the same flaw; treat it as unreliable.
- **Tally:** all 6 misses that every run agreed on were key defects. Our gap convention was also off by one (fixed).
- **Waiting on:** Mike's full key regeneration (v2.8-v2.12 + samples) for VBC-02, ACC-10, RX-10 and the
  supersede handling; then re-score everything. The generator is building the feasible v2.8 clinical task keys
  (1, 3, 4-7, 9), then v2.13.

## Measurement repaired and frozen (2026-10-17)
- **Scorer** `questions-2026-10-17.1` (generator): exact sets and tables, empty answers can be correct, malformed is
  its own count, small-value tolerance bound, multi-column keys. All 24 of Agere's scorer cases pass
  (`bench/scorer_cases.py`).
- **Agere side:** same-ingredient fill merge; table adapter keeps every column; every scored run writes a manifest
  and fail-closed costs (`bench/manifest.py`, `bench/layer_score.py`).
- **Re-scored under the new scorer** (old → new, correct):

  | Layer | Set | Old | New | Flipped to wrong |
  |---|---|---|---|---|
  | L1 | dev20 | 15 | 14 | RWE-12 (extra total/"both" rows) |
  | L2 | dev20 | 16 | 15 | RWE-12 (extra rows) |
  | L3 | dev20 | 16 | 16 | |
  | L4 | dev20 | 14 | 14 | |
  | L5b | dev20 | 17 | 17 | |
  | **L7 (full stack)** | dev20 | 18 | **18** | |
  | L6a / L6b | ho10 | 8 | 7 | CLN-06 (close but not an exact patient set) |

- **Frozen baselines:** `bench/baselines/dev20.json` (L7, 18/20) and `bench/baselines/ho10.json` (L6a, 7/10). Every
  `layer_score` run now prints COMPARABLE or NOT COMPARABLE against them; only questions, scorer, data, cache policy
  and pricing count as the ruler.
- **Not yet pinned:** the answer keys themselves (they are private). The generator is asked to publish a key digest;
  until then a key regeneration is only visible as a scorer-version or release change.
- **Answer keys now pinned** (same day): the scorer reports `answers_sha256`, every run manifest records it, and it is
  part of the ruler. Baselines re-frozen on key `fa34377c…` (v2.8 sample). Mike's pending key regeneration (RX-10,
  VBC-02, ACC-10, supersede handling) will make runs NOT COMPARABLE until the baselines are re-scored and re-frozen.

## H1: hybrid core (plan step 5), 2026-10-17
- **What it is:** `clinical_core/agere/hybrid` + `bench/hybrid_run.py`. Haiku writes an analysis contract (grain,
  population, numerator, denominator, window + anchor, perspective, exclusions, group_by, row_limit, unit, governed
  metric, unresolved) and ONE SQL query; it no longer reports numbers. Code validates the contract, compiles a governed
  metric only when its grain, output and time anchor match, executes the SQL read-only, builds the typed answer from the
  rows (table keys = the contract's group_by), checks shape and grain, and votes only among runs that passed. No
  majority -> one Sonnet pass through the same checks. Nothing is promoted to the library.
- **Also fixed on the way:** tmp/layers/dev20.jsonl carried the pre-patch text of REV-03, RX-10, RWE-02 and RWE-12;
  question files are now generated from the release (`bench/question_set.py` -> `bench/questions/`), and the manifest
  showed NOT COMPARABLE until the baseline was re-frozen on the current text (L7 still 18/20).
- **Result (dev20, frozen ruler, COMPARABLE with the L7 baseline):**

  | | Correct | Answered | Right when answered |
  |---|---|---|---|
  | L7 baseline (analyst answers, agreement, escalation) | 18/20 | 19 | 18/19 |
  | H1 hybrid core | 18/20 | 20 | 18/20 |

  Agreement: 14 unanimous, 5 majority, 1 escalated (HOS-08, two off-by-one category counts; Sonnet decided). All 66
  run outputs passed validation and checks after two harness fixes (exact integer votes; the read-only guard rejected
  replace()).
- **Misses, both unanimous:** RX-10 (996; key regeneration pending in the generator) and RX-09 (23; "maintenance drug
  class" and "no discontinued order" are not defined in the published question; sent to the generator as a key
  definition question). L7 abstained on RX-09.
- **Governed metrics:** used 0 times. No dev question's definition matched a governed metric exactly (e.g. HOS-03 counts
  claim lines, the metric counts stays), and the compile check correctly refuses a mismatch.
- **Tokens:** about 330K per full run of 20 (subagent usage), similar to L1-L3. Cost unmetered ($0 subagents).
- **Next:** wait for the key regeneration, then re-score and re-freeze; run H1 on ho10 (held-out) before any claim.
- **Held-out (ho10, COMPARABLE with the L6a baseline):** H1 7/10, 10 answered (8 unanimous, 2 majority); L6a 7/10.
  ACC-10 and VBC-02 are unanimous misses on confirmed key defects (pending regeneration). CLN-06 (eGFR-indicated
  kidney disease without a diagnosis) is a majority answer with an inexact patient set and no known key defect; L6a
  missed it too. It is the next real miss to examine after the regeneration.
- **RX-09 resolved as a key defect (generator):** the key came from simulation truth and counted (person, drug) stop
  pairs, not members; the corrected definition (delivered notes with the stop phrase, any drug, no list condition) ships
  with the pending regeneration.
- **After the regeneration:** re-score H1 and L7 on the new key digest, re-freeze both baselines, then look at CLN-06.

## Regeneration check and CLN-06 (2026-10-17)
- **Regeneration not applied yet:** after Mike ran it, the v2.8 sample key digest is still `fa34377c` (the frozen
  baseline's), questions.jsonl still has the old RX-09 text, and no file under releases/ is newer than the baseline. The
  manifest caught it; no re-score was done on an unchanged key. Asked the generator where it ran.
- **CLN-06 (held-out miss):** Agere's set is 63 persons under every reading tested: eGFR current or superseded
  versions, excluded rows in or out, event or arrival clock, N18 from icd10 or icd10_received, paid or any-status
  claims; "<60" = value_canonical < 60 (2,483 ">60" censored results excluded). The 3 runs differed only in dropping
  2 duplicate charts (WHP-1691C78EF591, WHP-D8D13E3EE095), which have no demographic twin, so delivered data can't map
  them to their person. Not a stack miss on the evidence; sent to the generator for audit (person resolution).

## H3: regenerated keys + current question text, fresh runs (2026-10-17)
- **Ruler:** the generator's key regeneration landed (scorer cases 24/24). 14 of the 30 questions were reworded; the
  question files were rebuilt from the release (7d143d0). Baselines re-frozen on H3 / H3ho; older runs are NOT COMPARABLE.
- **Stack changes since H1:**
  - Analyst docs: EHR problem-list evidence lives in two places, problem_list_entry (problem_lists stream) and condition
    rows with source_table='conditions'. A "documented" check must union both. The condition doc had wrongly listed
    problem_lists as a source. This was the CLN-06 miss: 11 of 63 patients had N18 only in `conditions`.
  - Contract check: a `value` answer (median/mean) needs no numerator.
  - Vote: a run that notes a caveat and still answers is a vote; abstain only when a run declined to answer.
- **Held-out 10 (H3ho):** 10/10, 10 answered (8 unanimous, 2 majority). Fresh Haiku runs, $0 subagents.
- **Dev 20 (H3):** 18/20, 19 answered, 18/19 right when answered. DQ-10 wrong; HOS-08 split 3 ways (no escalation pass
  run). RX-09 and RX-10 are now correct on the reworded questions.
- **Note:** the analyst docs live in tmp/layers (gitignored); their hash is pinned in each run manifest, but the files are
  not versioned.

## CT1: clinical workflow tasks through the hybrid core (2026-10-17)
- **Ruler:** v2.8 published tasks (`questions/clinical_tasks.jsonl`: CT-01, 03, 04, 05, 06, 07; 12 steps), index date
  2028-12-31, the generator's sealed clinical-task scorer on the v2.8 sample-0.30, key digest `5831842f`. Run manifest
  `tmp/hybrid/CT1/manifest.json` (`bench/task_score.py`).
- **Core extension (general, no per-task code):** a `rows` answer type. Each task step becomes one question with its
  own contract and ONE self-contained SQL, executed read-only and checked by code. The key columns are the step's
  string output columns (patient_id; patient_id + drug for CT-03). Other columns are typed (int, number, list as a
  JSON array) and checked. A task's steps are batched together, with the shared definitions printed once.
  `execute` writes `submission.jsonl` in the scorer's format. Voting covers the whole row set, attributes included.
  Tests: `bench/test_hybrid_rows.py`.
- **Runs:** 3 independent Haiku runs ($0 subagents, about 1.2M subagent tokens in total), 3 batches of 2 tasks each.
  All 36 run outputs passed checks. 5 steps were unanimous and 7 were 2 of 3; no escalation was needed.

  | Step | Rows | Precision | Recall | F1 | Attributes | Agreement |
  |---|---|---|---|---|---|---|
  | CT-01.a HFrEF | 43 | 0.977 | 1.0 | 0.988 | | 2/3 |
  | CT-01.b missing pillars | 43 | 0.977 | 1.0 | 0.988 | missing_pillars 100% | 2/3 |
  | CT-03.a dose below target | 21 | 1.0 | 1.0 | **1.0** | daily dose 100%, target 100% | 2/3 |
  | CT-04.a T2D + ASCVD/CKD | 297 | 1.0 | 1.0 | **1.0** | | 2/3 |
  | CT-04.b no SGLT2i/GLP-1 | 191 | 1.0 | 1.0 | **1.0** | | 2/3 |
  | CT-05.a AFib, CHA2DS2-VASc >= 2 | 84 | 1.0 | 1.0 | **1.0** | score 100% | 3/3 |
  | CT-05.b no OAC | 45 | 1.0 | 1.0 | **1.0** | | 3/3 |
  | CT-06.a CKD 3-5 + UACR >= 30 | 98 | 0.959 | 1.0 | 0.979 | | 3/3 |
  | CT-06.b no ACEi/ARB/ARNI | 60 | 0.983 | 1.0 | 0.992 | | 3/3 |
  | CT-06.c no SGLT2i | 87 | 0.966 | 1.0 | 0.982 | | 3/3 |
  | CT-07.a statin-indicated | 825 | 1.0 | 0.969 | 0.984 | | 2/3 |
  | CT-07.b no statin | 471 | 1.0 | 0.950 | 0.974 | | 2/3 |

  6 of 12 steps are exact. Every attribute (pillars, doses, targets, CHA2DS2-VASc) is 100% on the shared keys.
- **Misses by cause:**
  - **CT-07.a/b (data model; explained exactly).** 26 statin-indicated people are missed (25 in step b). Their only
    qualifying fact is a delivered LDL-C >= 190 on a lab record with no event_date (historical import, recorded
    2026-08-28). The semantic layer keeps these 27,569 measurement rows with event_date NULL, and every run filtered
    `event_date <= index`, which drops them. Dating them by recorded_date gives 851 and 496 people, which are exactly
    the key sizes implied by the recall. Fix where it lives: the measurement model needs a defined specimen date for
    undated results (or documentation saying how to date them). The generator should confirm how the key dates a lab
    with no event_date before we change it. Not re-run; tuning to the key after seeing the score would not be clean.
  - **CT-06.a/b/c (unresolved; 4, 1 and 3 extra people; the 3 runs agree).** The 3 runs give identical sets, so this
    is not an analyst slip. Every extra person has a documented N18.3-N18.6 code and a latest dated UACR >= 30 under
    the published rules. Candidate cause: the same undated labs. 119 UACR rows have no event_date, and 5, 4 and 3
    people in the three sets have an undated UACR < 30. That count matches step c only. Suspected key or definition
    question: what is the "specimen date" of an undated result? Sent to the generator together with CT-07.
  - **CT-01.a/b (1 extra person; 2 of 3 runs agree).** Ruled out: duplicate charts, deaths, and EF in the 4 report
    streams that note_fact doesn't cover (raw delivered text was checked). The two EF-only patients have a latest
    report LVEF of 35-40% and 30-35% (lower value 35 and 30), so they qualify under the stated range rule. The one
    run that differed left out a person whose I50.22 appears only in EHR `conditions` records. The key counts
    `conditions`-only people in CT-07 (23 people, precision 1.0), so that is not the cause. Unexplained on the
    aggregates; classed as a possible key question.
  - Definition reading and analyst error: no misses attributed (all attribute values exact; agreeing runs).
- **Process notes:** (1) `ecosystem/eval_clinical_tasks.py` holds the key-construction logic (delivered_key) in the
  same public module as the scorer, so anyone can read it. A grep for the submission contract showed a few of its
  lines. Nothing from them was used, and the run SQL was written by the analysts before scoring. The generator
  should move key construction under private/. (2) Another session's commit c41e439 picked up this session's
  `bench/hybrid_run.py` task changes along with its own; they are intact and tested. (3) One analyst reported an
  injected "AGERE build continuity" block appended to its prompt-file read (a hook). It ignored the block.
