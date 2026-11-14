# Claude review: dependency parsing performance

Exact subscription review of417c82c. No tests or clinical adjudication executed by the reviewer.

**Verdict: I found no concrete introduced omission, mis-parse or security defect from reading the diff and measurements.** The checks below are by reading only. I did not run anything.

**Why dependency loss looks unlikely**
- The only edit to the probe text is replacing a single-quoted string literal with `''`. A string literal cannot name a table or column, so it cannot be the only place a dependency appears.
- The replacement happens only when the literal is the first non-comment token after `json_each(` or `json_tree(`. The `previous`/gap check clears `first_argument` for anything else, such as `x, '…'`, `((…))`, `:p`, `X'..'` or `data||'…'`. The tests cover these.
- Quoted strings, identifiers (`"`, backtick, `[]`) and comments are lexed leftmost-first. A `json_each(` inside a string, identifier or comment is therefore never treated as a function call.
- `executable_text`, `_sqls`, the `lru_cache` key and the `_saved_row` comparison all still use the original SQL. Definition, literal and drift checks therefore see unchanged text.
- An unterminated literal is not replaced, so sqlglot's `TokenError` still fires. The tests pin this.
- The 24 healthcare and 22 financial queries give identical table and column sets before and after.

**Observations, none of them defects**
1. **Literal followed by an operator.** `json_each('…' || expr)` still has its literal replaced, because the code never checks that the argument ends right after the string. The result is `'' || expr`, which parses the same way, and `expr` is preserved. This is cosmetic, but the comment "literal first argument" slightly overstates what is checked.
2. **Unterminated `/*`.** SQLite treats the rest of the text as a comment, but the regex does not match it. The scanner then lexes inside the comment, and a `json_each('…')` there could be shortened. Only a string literal is ever removed, so dependencies cannot be hidden this way. It is a lexer-versus-SQLite divergence that sqlglot's own tokenizer governs.
3. **Odd function names.** `x.json_each(` or a schema-qualified call also matches `\bjson_each\s*\(`. The effect is still just replacing a first-argument literal, so it is harmless.

**Evidence gaps (hypotheses, not observed failures)**
- Equivalence was shown only on 46 real queries and hand-written tests. There is no differential check of `_parsed_dependencies` on the old and new paths over adversarial or mutated SQL.
- The tests lack cases for `json_each` inside subqueries or CTEs combined with `||`, an unterminated `/*`, and a `"` or `[` inside a JSON payload next to real quoted identifiers.
- The whole-load measurement is one cold run per arm, which the report states itself. Of the 7.2s saved, about 6.9s is the parse (6.97s → 0.055s). The remaining 4.77s is outside this change.

**Suggestion (optional, no architecture change):** add a randomized old-versus-new equivalence test that compares `_parsed_dependencies` with and without `_dependency_sql` on generated SQL. That would turn "matches on 46 queries" into a stronger guarantee.


## Owner follow-through

The reviewer found no concrete introduced defect. The description now says leading literal in the first argument, reflecting concatenation behavior. Twelve generated nested/quoted SQL cases compare old-versus-new parsing directly, including concatenation, comments and quote characters inside JSON.78connected regressions pass. Actual24healthcare/22financialquery dependencies match, and the complete current returned context/source/policy remain identical. The code never executes shortened SQL and never drops source evidence. One matched isolated cold-parser whole-load comparison changes11.983s to4.770s; the full frontend request remains separately measured.
