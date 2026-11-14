# Review: next increment for source-dependency verification

## Recommendation

Do **not** start with the deduplication as proposed. The next increment should be a short, mostly model-free sequence:

1. **Offline wrapper-reconstruction audit and critical-path profile.** No model calls.
2. **Narrow whole-packet vs source-local trial** on authored mutations.
3. **Deduplication as a default-off candidate**, only if steps 1 and 2 justify it.

If forced to pick one item, pick the trial, with step 1 as its free first stage. The deduplication targets a hypothesized redundancy, not the measured failure.

## Observed defects vs hypotheses

**Observed (from your summary):**
- All 66 packets have selected Choice probabilities of .32–.56, below the .80 threshold. At the field-check level, 1494 of 2005 are below it, 508 clear it, and 3 select unknown.
- Fallback was unavailable: 3 failed fallback attempts, plus 1 failed actor attempt.
- Each semantic assertion appears 3× in a child (3→9, 8→24), in 3 inspected inputs.
- Warm runtime was 1226s. The frontier source-span repair union (452s) dominates the overlapping stage unions. The field-review union is only 16s.

**Hypotheses (not established):**
- The 3× redundancy is general beyond 3 inspected inputs. They may not be representative, for example if they were inspected because they were oversize.
- The wrapper variants are mechanically derivable and add no meaning.
- Removing the redundancy affects wall-clock time, cost, or the below-threshold rate.
- Packet shape (size, ~36 fields, shared-reference layout) causes the low probabilities.

**Things in the summary that need clarification before acting:**
- **Unit mismatch.** "All 66 packets below .80" sits alongside "508 field checks clear .80". Is the packet figure an aggregate, a minimum, or a different score? If packet-level pass requires every check to pass, the uniform .32–.56 band says little about the checks themselves. Report the check-level distribution (bimodal? clustered near the minimum?) before theorizing.
- **A narrow band is suspicious.** A tight .32–.56 band across all packets looks like a structural property: option count, probability normalization, or a diffuse readout. It does not look like per-claim uncertainty. This is still a hypothesis, but it argues for testing discrimination before optimizing throughput.
- **Verifier share of runtime is unmeasured.** The verifier's critical-path share is not shown, and the largest stage is frontier repair, which dedup does not touch. A 3× cut in verifier requests may move the 1226s very little. Typed-model requests may also carry no API-equivalent cost, in which case the saving is mostly latency. Neither point is currently evidenced.
- **Fallback.** "Unavailable" is an observed wiring defect. It means below-threshold items have no escalation path. Diagnose the failure class from the existing error records (config, auth, quota, timeout, schema). That is not a reason to change the threshold or add services.

## Flaws in the proposed deduplication

1. **Lost field-meaning checks (the main risk).** The third variant carries originating-field metadata, which is the part most likely to be field-meaning verification. Merging checks per assertion yields one verdict about assertion-vs-source. Each field use then needs its own verdict about whether the assertion supports that field's value under that field's definition. One shared "supported" must not fan out to uses whose meaning differs, for example the same negated assertion feeding both `has_X` and `no_X`. Unless a deterministic native check or a retained model check exists per distinct field-use tuple, field-meaning coverage is lost.
   - The 9→3 and 24→8 reductions hold only if the field metadata and uses are identical across occurrences. Treat the reduction as a measured range (3 to 9 per triplet), not a fixed ratio.

2. **False equivalence.** "Proven-identical assertion" must be keyed on structured fields of the immutable assertion, not on rendered text or text hash alone:
   - patient, question ID, source object ID, source family, document ID, source clock/as-of, quote offsets;
   - assertion hash, polarity, subject (patient vs family/other), relation, entity, temporal anchor and status (historical/hypothetical/current), certainty;
   - wrapper schema version.

   Identical text at different offsets or in different documents is different evidence. The same assertion reaching two fields with different meanings is two obligations.

3. **"Mechanical reconstruction" must be proven by byte-equality.** Re-render each wrapper from the key with a versioned, whitelisted schema, then compare byte-for-byte against the original retained object.
   - Exact match: eligible to merge.
   - Any difference, unknown schema, or extra field: independent check or fail closed.
   - Never use similarity or "looks like the known wrapper".
   - Reconstruction should depend only on the bound assertion and its declared context, and must not pull in unrelated source text.

4. **Source binding and fan-out.**
   - Merge only within one child and one source object. Never merge across children or sources.
   - Persist a group map: canonical check ID → original object IDs → field-use IDs.
   - Invariants: every original object appears in exactly one group; every field-use obligation appears exactly once; counts sum to the originals; reconstruction remains exact with all originals retained; parent and every child still pass.
   - Verdicts must record which canonical check they came from, so audit can trace them.

5. **Incomplete aggregation.** Obligations existing on only one occurrence (for example only the metadata variant) must survive in the union. Check the current pass rule too. If it is not strictly all-must-pass, collapsing 3 checks to 1 changes pass semantics, not just cost. A below-threshold single check now fails every use at once, producing correlated failure. That is acceptable if declared, but it should be explicit.

6. **Verdict drift.** Fewer and shorter checks change the model input, so probabilities may shift in either direction. Merging cannot be asserted verdict-preserving. It has to be measured on a small sample.

7. **Not a promotion mechanism.** It does not address the 1494 below-threshold checks. Do not report it as quality progress. Under your own drift rule, work elimination that does not move the named gate or a measured failure should not count as gate progress.

## Free analysis from retained data

Before any new model call, check whether retained outputs for the three variants of the same assertion differ in verdict or probability.
- If they disagree, that is direct evidence of format sensitivity. It would also show the wrappers are not behaviorally equivalent, and dedup would be hiding that.
- If they agree and sit in the same low band, redundancy is mere cost and the low band is not a wrapper issue.

## Why the source-local trial is preferable first

Deduplication only makes a step cheaper. It is only worth doing if that step discriminates. The mutation trial tests exactly that. Whole-packet vs source-local verification on equivalent visible evidence tells you whether packet shape matters and whether the verifier separates clean from mutated evidence at all. Ground truth comes from authored construction and native checks, not from clinical judgment or new labels.

**Trial design (narrow):**
- Use a small fixed set of retained inputs, including the 3 inspected ones. Predeclare the sample and metrics.
- Authored mutations: entity swap; date outside the window/clock; wrong source ID; negation flip; subject swap; relation change; field-use mismapping; same text at a different offset or document; same assertion feeding two differently defined fields; wrapper tampering (extra metadata).
- Run each case through whole-packet and source-local verification on identical visible evidence, with native checks as a deterministic reference.
- Metrics: mutated-case detection rate; false support on mutated cases; agreement with native checks on clean cases; unknown rate; probability distribution; tokens and latency; run-to-run determinism on a small repeat.
- Use fixtures authored in the test corpus. Keep generator truth and reserved evaluations out. Do not tune on the trial and then cite it as qualification.
- No threshold change, no model catalog, no cohort rerun, no retraining, no invented labels.

## Concrete steps and acceptance

**Step 1 (no model calls):**
- Wrapper-reconstruction audit over retained packets. Report the exact-match rate, unknown-wrapper rate and mismatch rate, with examples of each.
- Critical-path profile: non-overlapping wall-clock per stage, with the verifier's share and the typed-model request count by stage. Include the error class for the 3 fallback failures and 1 actor failure.
- Triplet-variance analysis (above).
- Proceed to the dedup candidate only if exact reconstruction covers a clear majority of occurrences (suggest ≥90%, a threshold you should set up front) and the verifier's critical-path share is non-trivial.

**Step 2 (trial):** the design above.
- Dedup acceptance: whole-packet and source-local verification both discriminate mutated from clean at an acceptable rate, and the source-local result is no worse.
- A mutation that passes undetected in both means the verifier representation is not the lever.

**Step 3 (dedup candidate, default off):**
- Implement in place in the existing partitioner, with no runtime patches and no copied modules. Mark it as a candidate, and record a known-good git recovery point.
- Acceptance:
  - zero false merges across the authored mutation suite, including all cases in the trial list;
  - every original object and field-use obligation mapped exactly once, with reconstruction still exact;
  - fan-out gives every field use its own recorded verdict, with a native check or a retained model check for field meaning;
  - on a small matched sample, every verdict change versus the unmerged run is explained;
  - measured reduction (checks, tokens, and wall-clock share) is reported, not assumed;
  - activation by default only with workflow evidence.

## Stop conditions

- **Any false merge**, any unmapped original or field use, or any unexplained verdict flip: stop and keep the candidate off.
- **Reconstruction rate low or critical-path share negligible:** drop dedup. Its value is unproven and it risks adding a helper path.
- **Neither packet shape discriminates mutations:** stop optimizing transport. Reassess how the Choice probability is computed and read (output format, option structure, calibration for ~36-field answers). That is not a call for an architecture rewrite and not a reason to relax the threshold.
- **Source-local clearly better:** pursue source-local verification as the correction, and consider dedup afterwards.
- **Gate rule:** if two successive increments produce no gate progress or measured failure reduction, record the reassessment in the control decision log before another feature increment. The increment should name the gate, the measured failure (below-threshold checks, fallback unavailability, or critical-path latency), and the workflow check, each as a separate item.

## Boundaries on conclusions

- None of these results is a clinical error rate or a model ranking.
- Source-local vs whole-packet outcomes are engineering evidence about verifier behavior, not qualification.
- The one defect that can be treated as unambiguous engineering wiring is the unavailable fallback. Diagnose it from its recorded failure class.
