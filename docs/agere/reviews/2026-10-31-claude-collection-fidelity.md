# Review: parent-key elision in `collection_retention_issues`

## Verdict

- **Adopt the minimal option**: symmetric normalization of declared, non-null parent-key fields, followed by the existing multiset Counter.
- **Reject the broad subset matcher.** No observed failure needs it, and it widens what the check will accept.
- Both observed rejections (89 objects with an added `patient_id`, 17 objects with a dropped `patient_id`) were false positives of an exact-digest contract. The first draft's omission (`agere_result_61`, 0 of 89 and 0 of 17) is a real loss and must keep failing.

## (1) Is it semantically lossless?

Yes, under three conditions that the existing code mostly enforces:

1. The object stays in the same retained parent row. The final row is located by `key(row, keys)`, and `rename_columns` already forbids renaming key columns, so the parent key value is still present in the final row.
2. The elided child field is a redundant copy of that parent value. It must be non-null and JSON-type-identical to the parent value.
3. Everything else in the object, including nested values, remains digest-exact.

What the normalization deliberately ignores is whether the object *carried* a field that merely restates the enclosing key. That is a container-representation fact, not a source fact. It is recoverable from the parent row, and the join itself guarantees it for declared `on` columns.

Hazards:

- **Null.**
  - Never strip a null child value. `None` is not "equal to the parent", and null vs absent vs value must stay distinguishable.
  - Never strip against a null parent value.
  - Consequence: an original `{patient_id: None, …}` against a final that drops the field still fails. That is conservative and correct.
- **Type.**
  - Compare with `json.dumps(v, sort_keys=True, allow_nan=False)`, as the module's `key()` does. Do not use Python `==`, because `1 == 1.0 == True`.
  - Parent `7` vs child `"7"`, `7.0` or `True` must not strip.
  - Catch `ValueError` from NaN and treat it as not equal.
- **Patient reassignment.**
  - Only values equal to the *this-row* parent are stripped.
  - A foreign `patient_id` (an object under P1 carrying P2) is never stripped on either side, so adding, dropping or changing it still produces a digest mismatch.
- **Grain.**
  - Do not name-match on the assembly `keys` alone. A generic key name such as `id` or `code` could coincide in value with an unrelated child field, and eliding it would drop a real field.
  - Restrict the elidable names to the retained join's own declared identity: for each `(left, right)` in `join['on']`, the child fields named `left` or `right`, compared against `parent[left]`.
  - That is an explicit declaration already present in the assembly coverage record. It is not a name heuristic.
  - If a name maps to conflicting parent columns, do not elide it.
- **Downstream flattening.** The guarantee holds only while the collection stays inside its keyed parent row. An exploder that later drops the parent key would lose attribution. That is outside this check, but worth a note in the checkpoint.
- **Presence conflation.** `[{x}, {x, patient_id=P}]` normalizes to two identical objects. This is intentional and harmless, and the multiplicity of 2 is preserved.

## (2) Why not the broad proposal

The broad version also lets arbitrary top-level extras through. It is unjustified because:

- Any added non-key field, such as a status, flag or inferred adherence, passes silently. That is exactly the "annotation without clinical inference" boundary you want to keep.
- Subset matching is a bipartite assignment problem. A greedy matcher fails in both directions:
  - **False rejection.** Originals `{a:1}` and `{a:1,b:2}` against finals `{a:1,b:2}` and `{a:1}`. If the greedy matcher pairs `{a:1}` with `{a:1,b:2}` first, it strands `{a:1,b:2}`.
  - **False acceptance.** Two duplicate originals `{a:1},{a:1}` against one final `{a:1,b:2}`. Without consumption this accepts a lost duplicate.
- Correct handling needs Hall/Hopcroft–Karp-style matching, worst-case super-linear, for no demonstrated benefit.

Scalar collections and nested values keep exact semantics.

## (3) Minimal implementation

Normalize both sides with one pure function, then use the same Counter subtraction. Each final object then contributes exactly one normalized digest. There is no assignment step, so there is no double-consumption and no pairwise cost.

```python
def canon(v):
    try: return json.dumps(v, sort_keys=True, allow_nan=False)
    except ValueError: return None

def elision_map(join, parent):
    m = {}; bad = set()
    for left, right in (join.get('on') or {}).items():
        pv = parent.get(left)
        if pv is None or isinstance(pv, (dict, list)): continue
        for name in {left, right}:
            if m.setdefault(name, pv) is not pv and canon(m[name]) != canon(pv):
                bad.add(name)
    return {k: v for k, v in m.items() if k not in bad}

def elide(item, m):
    if not isinstance(item, dict) or not m: return item
    drop = [f for f, pv in m.items()
            if f in item and item[f] is not None
            and canon(item[f]) is not None and canon(item[f]) == canon(pv)]
    return {k: v for k, v in item.items() if k not in drop} if drop else item
```

Integration points:

- Keep the existing raw `wanted - have` computation per address as the fast path.
- Only if the shortfall is nonzero and the elision map is nonempty, recompute with `digest(elide(item, m))` on both expected and final objects. Recompute per address, and never pool across addresses.
- Build the elision map from the source `row`. The final parent values are identical by `ident` equality.
- Apply `elide` only at the top level and never recurse.
- Map normalized digest to raw object, so `records` and `missing_objects` still report the original objects and record IDs.
- Extend the failure message to say that fields restating the row key are ignored, so the model stops spending repair callbacks on that.
- Optionally record an `elided_parent_key_objects` count in the diagnostics so the leniency is auditable.

## (4) Should exact comparison remain the fast path?

Yes. `elide` is a deterministic function of (object, parent), so equal objects stay equal after normalization. Normalized acceptance is therefore a strict superset of exact acceptance, and the fast path only saves cost and keeps the old diagnostics byte-identical for unaffected results. It cannot change any verdict.

## Required regression checks

Must pass:

- Identical collections (fast path).
- The 89-object case: final adds `patient_id` equal to the parent.
- The 17-object case: final drops a duplicated child `patient_id`.
- A JSON-string-encoded collection with elision.
- Elision combined with a `rename_columns` alias address.
- Heterogeneous shapes `[{a},{a,pid=P}]` against `[{a,pid=P},{a}]`.
- Key-order variation.

Must fail:

- Entire collection omitted (the `agere_result_61` fixture).
- Lost duplicate: original ×2, final ×1.
- Added `patient_id` ≠ parent, or original foreign `patient_id` dropped, altered or rewritten to the parent.
- Objects swapped between two parents.
- Null cases: original `None` vs added parent value, and original `None` vs absent.
- Type cases: parent `7` vs `"7"`, `7.0` and `True`. Also confirm the existing digest distinguishes `1`/`1.0`/`True`/`"1"` in non-key fields.
- An added non-key field, such as a flag or status.
- Any dropped or changed `record_id`, date, dose or unit.
- A field with the same value as the parent but not in `on`, when dropped.
- Nested `patient_id` or nested assertion changes (no recursion).
- A scalar collection with any change.
- A heterogeneous multiset that a greedy subset matcher would mis-handle: originals `{a},{a,b}` against finals `{a,b},{a,b}`.
- A composite key where only one component matches.

Properties and cost:

- Property test: exact acceptance implies normalized acceptance, and `elide` is idempotent.
- A 10⁵-object collection completes in a single linear pass. Count digest calls to assert there is no pairwise matching.
- The reported `missing_objects` are raw originals.

## Scope notes

- The current check lets the final collection contain extra objects, including foreign-patient ones, because it only computes `wanted - have`. This is pre-existing and this change makes it neither better nor worse. It is a separate gap worth a separate tracked item.
- Label this a candidate checker contract (for example `collection-retention/2`) and re-evaluate the stored attempts under it. Do not mutate or relabel the immutable observations or the prior rejection records.
- This is an engineering fidelity repair. It does not constitute source, model or clinical qualification, and it does not touch any of the ten product areas.
