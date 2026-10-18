# Narrow typed-plan bottling: integration patch for the cohort-family rung

Status: built, measured on held-out, **flag default off**. The patch below needs the owner of
`economical_planner.py`. Nothing in the planner changes until the patch is applied, and nothing changes after it until
`AGERE_BOTTLED_COHORT=on` is set.

## What it is

`bottled.cohort_slots` is a $0 rung. The local Qwen3-4B plus a LoRA (`artifacts/bottling/adapters-narrow-v1`) maps a
question to compact **cohort-family slots**, for example
`{"family":"event_followup_gap","ask":"list","anchor":"ed","condition":"hf","lookback":{"months":12},"followup":{"kind":"visit","days":14}}`.
The model never writes SQL, code sets or ICD prefixes. The slots name vocabulary keys, and from there the existing code
takes over:

slots → `canonical_question()` → `cohort_families.parse` → `validate` → `render`

So a bottled answer gets the same defaults, code sets and SQL as a parser hit. The rung only runs where the
deterministic parser declines or leaves slots missing. Five decodes (one greedy, four sampled) must agree on the
normalized slots, and the slots must round-trip through the parser. Benchmark questions and the parser's unsupported
words (rates, breakdowns, costs, starts and stops, ...) are never claimed. Every other case falls through unchanged.

`bottled_cohort_hook.plan()` has `cohort_families.plan`'s signature and return shape. With the flag off, or with no
adapter on disk, it *is* `cohort_families.plan`.

## The patch (economical_planner.py, `_cohort_family`)

```diff
@@ def _cohort_family(stack,question,ledger,meta,start,local_decline,early):
         live=bool(stack.allow_live) and ledger is not None
-        res=cf.plan(stack.path,question,as_of,ledger=ledger if live else None,receipts=stack.receipts if live else None)
+        from . import bottled_cohort_hook
+        res=bottled_cohort_hook.plan(stack.path,question,as_of,ledger=ledger if live else None,receipts=stack.receipts if live else None)
         if res is None:return None,{'reason':'no cohort family fits (or slots missing without a model)'}
```

That is the whole patch. The bottled step adds nothing to `res['calls']`, so `new_paid_api_calls`, `model` and the cost
step stay correct. Its receipt (slots, canonical wording, agreement, decode seconds) is in
`res['receipt']['bottled']`, which the planner already copies into `cohort_family`.

An optional second hunk is for the owner of `cohort_families.py`. `record()` writes `[MODEL]` (Haiku) into the
learning store whenever `model_filled` is set. A bottled fill sets `model_filled=['bottled_slots']`, so a bottled answer
would be recorded as Haiku-filled. To keep provenance exact:

```diff
-                             [MODEL] if fam.get('model_filled') else [], 'deterministic_cohort_family')
+                             ([] if fam.get('model_filled') == ['bottled_slots'] else [MODEL]) if fam.get('model_filled') else [],
+                             'deterministic_cohort_family')
```

## Flags

| env | default | meaning |
|---|---|---|
| `AGERE_BOTTLED_COHORT` | `off` | `on` lets the hook consult the bottled rung |
| `AGERE_BOTTLED_COHORT_K` / `_AGREE` | `5` / `5` | the acceptance rule measured below |
| `AGERE_BOTTLED_COHORT_ADAPTER` | `artifacts/bottling/adapters-narrow-v1` | adapter path |

## Held-out result (gate)

Data comes from `tools/bottling/narrow_data.py` → `artifacts/bottling/data-narrow-v1`:

* **Train:** 4,125 rows (3,079 template + 1,046 Haiku paraphrases).
* **Valid:** 210 rows.
* **Held-out:** 754 rows (350 template + 404 Haiku paraphrases, 65 of them non-family).

Every label is a slots object that round-trips through parse, validate and render. The held-out split uses its own
phrasing frames and held-out-only surface forms, and slot instances are disjoint across splits. The exception is
screening, which has only 6 possible instances. 13 held-out rows were removed because their frame dropped the sex
slot from the wording; the generator is now fixed. Evaluation comes from `tools/bottling/narrow_eval.py` →
`artifacts/bottling/eval-narrow-v1/full.json`. "Same list" means the same patient list or count on a scratch copy of
pilot-native-v2-12, checked only where specs differ.

**Questions the parser declines (398 family + 65 non-family): the only questions the rung ever sees**

| method | coverage | exact-spec precision | same-list precision | false claims (of 65) | $ / question | median latency |
|---|---|---|---|---|---|---|
| parser alone | 0% | – | – | 0 | 0 | 0.1 ms |
| parser + Haiku slot filler (today) | 0% | – | – | 0 | 0 | – |
| Haiku given the slots task (8-shot) | 100% | 0.915 | 0.925 | 0 | ~$0.0002 | 0.9 s |
| **bottled k=5, m=5** | **81.7%** | **0.927** | **0.942** | 2 | $0 | 1.9 s (p90 2.8 s)* |
| bottled k=3, m=3 | 86.2% | 0.913 | 0.931 | 3 | $0 | – |
| bottled greedy only | 95.7% | 0.874 | 0.897 | 8 | $0 | – |

\* Measured on a heavily loaded machine. Model load is a one-time cost per process, with roughly 2.5 GB resident.

The parser never left a slot "missing" on this set, so the Haiku slot filler never ran. Today, every question the
parser misses goes on to the downstream rungs and the hosted SQL ladder. On the pilot those cost a median of
$0.024 (mean $0.046) per question, and their truth precision is 0.60-0.69 per the bottled.py note.

**Gate:** bottled precision at its chosen coverage must meet or beat the paid alternative. With k=5, m=5 it does:
0.927 / 0.942 against Haiku's 0.915 / 0.925 on the same task, and against roughly 0.6 on the ladder these questions
reach today. Coverage is lower than Haiku's (82% vs 100%). The 2 false claims on non-family questions were "ED
encounter ... and were admitted" and "patients taking an antiplatelet". **Recommendation:** apply the patch and enable
with `AGERE_BOTTLED_COHORT=on`, K=5, AGREE=5.

**Whole held-out set:**

| method | coverage | exact | same list |
|---|---|---|---|
| parser | 42% | 0.794 | 0.814 |
| cascade (parser → bottled k5m5) | 89% | 0.864 | 0.882 |

The deterministic parser itself misreads about 1 in 5 of the paraphrases it claims. Examples:

* "beneath 60 percent": the threshold falls back to 80%.
* "without a follow-up visit in 90 days": the window falls back to 30 days.
* "in the last year": read as the calendar year for adherence.

These parser gaps are the main source of wrong cascade answers, and they belong to the cohort_families owner.

## Reproduce

```
.venv/bin/python tools/bottling/narrow_data.py artifacts/bottling/data-narrow-v1 --haiku 400 150
.venv/bin/python -m mlx_lm lora -c tools/bottling/lora-narrow.yaml      # stopped at iter 600 (val 0.054/0.047/0.046 at 200/400/600)
.venv/bin/python tools/bottling/narrow_eval.py artifacts/bottling/data-narrow-v1 <scratch semantic.sqlite3> out.json --adapter artifacts/bottling/adapters-narrow-v1 --k 5
.venv/bin/python -m unittest test_bottled_narrow
```
