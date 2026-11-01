**Verdict: no concrete material defect found.** Two hypotheses and one test gap are noted below.

**Termination**
- Every non-stopping iteration either adds a new key to `reviewed` or a new key to `compiler_reviewed`. Compiler history is cleared only when a non-compiler key not already in `reviewed` is about to be added.
- The longest chain is therefore bounded by the number of distinct non-compiler failure states, plus the compiler states between them. S1→C1→S1 stops, and S1→C1→S2→C1→S1 stops at the second S1. Both are the behaviour you want, and the second test covers the first.
- `reviewed` is never cleared, so semantic failure history is fully preserved. The clear sits before `history.add` and only runs when `key not in reviewed`. A repeated semantic key still hits the stop/escalate branch and never reaches the clear.

**Progress is real, not cosmetic**
- A clear needs a non-compiler assessment, meaning the compiler passed. It also needs a key not seen before, so a repeated source fault cannot re-arm the compiler budget.
- Compiler churn with no semantic stage in between (same key, renamed keys, numeral wording, unchanged queries) still hits `compiler_reviewed` and stops. This is the pre-existing behaviour, and the first test covers it.
- Escalation on a repeated key is unchanged for both histories. The `elif` that escalates on a fresh semantic repair is untouched.

**Failure preservation**
- `result['answer']` is still set to the partial/unverified candidate before any stop. Tool calls are untouched, and the test asserts they are unchanged.

**Hypotheses, not defects**
1. A non-compiler `unresolved_verification` state also counts as the "new non-compiler state" that clears compiler history. It is still bounded by `reviewed`, so I would leave it.
2. With `max_tools=None`, a reviewer whose semantic feedback varies per candidate (no stable `progress_state`) can produce a new key each turn. That was already unbounded before this change. The change roughly doubles the turns per novel semantic key, since each one can be followed by one compiler cycle. It does not create a new loop.

**Test gap (optional, one small test)**
- Nothing asserts that compiler churn still stops after a clear. A sequence like S_new → C1 → C1 should end in `no_progress`. It is a two-line scripted-assessment test in the style of the existing second test. It is not required for correctness, because the code path is the same `key in history` check.
