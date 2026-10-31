**Scope:** This is a read-only review of the pasted evidence. I read no control files and ran no checker, so alignment with the current gate is unverified.

## 1. Over-propagation, hidden dependencies and guards

**Yes, it over-propagates for this fault shape.** Both faults sit in one child, `patient_SYN-340EFC7B-89F__sources_0`, on documentary assertions that carry `patient_id`, `source.patient_id` and `subject=patient`. Halting 122 other packets over that is unjustified. The old `stopped` flag is a single global boolean, and nothing in `check_support_packets` distinguishes fault scope.

**Risks, in order of importance:**

1. **Locality is currently unprovable from the fault row.** The feedback row has `claim_id`, `path` and `source_assertion_id` but no patient binding. Deriving the patient from the fault, or from the packet's encoded text, is guessing.
   - Guard: the producer adds `source_dependency_patient_id` and a `source_claim_assertions` map (`claim_id` → full assertion) to each child dict, and the same patient id to the parent.
   - These stay as dict side-data, outside `text`, so the provider envelope and fingerprints are unchanged.
   - The verifier checks that `claim_id == 'source_%d' % i`, that `path == ['answer_rows', i, 'observation']`, and that the assertion at that index has an id equal to the fault's `source_assertion_id`.
   - It then recompiles the id with the existing id function and checks that `patient_id`, `source.patient_id` and the packet and parent patient ids all match, with `subject == 'patient'`.

2. **Non-family packets that consume the faulted observation.** Cohort, aggregate or count packets have no `source_dependency_parent_id`.
   - Guard: after a local fault, only packets in a validated other family continue. Any other packet stays deferred as a barrier unless it declares dependency ids disjoint from the faulted patient and assertion.

3. **Quarantine side effect and baseline integrity.** The fault rows show `source_observations_quarantined: 1`, yet the summary says `source_unchanged: true`.
   - I cannot tell from the evidence what the quarantine wrote to. Either it hit a run-local copy or the "unchanged" check does not cover quarantine state.
   - Resolve this before widening the run. A mutated store would make any later narrow-versus-whole comparison non-comparable.
   - Quarantine is applied inline on a fault the code itself labels "Unadjudicated". Prefer recording quarantine as intent and applying it after the run.
   - Quarantine only the located assertion ids, never on a global fault with no id.

4. **Rubric disagreement vs. source fault.** The fault is `jev` against `perplexity` on the same assertion, not a demonstrated defect in the source data. Continuing is still correct, because coverage is 40 of 162. But record a fault signature (`field`, `relation`, `question_digest`, `label`) per family and report repeats.
   - Do not auto-promote repeats to global without a recorded policy decision. That would be a new threshold.

5. **Cost and time scale.** The remaining 122 packets roughly triple the call volume. That is a rough extrapolation from 40 packets at 148 s and $0.30. It is not a regression, since spend is uncapped and accounted, but expect longer runs.

## 2. Smallest implementation

Replace the boolean with `stopped` (global) plus `blocked = set()` of family ids. Family = `source_dependency_parent_id or id`. The sets are touched only in the main thread, where `wait()` results are processed, so no lock is needed.

```python
def local_family(packet, res, by_id):
    # return parent id iff every decisive fault is provably patient-local, else None
    p = packet.get('source_dependency_parent_id'); pid = packet.get('source_dependency_patient_id')
    if not p or p not in by_id or packet['id'] not in by_id[p].get('required_source_packet_ids', []): return None
    if not pid or by_id[p].get('source_dependency_patient_id') != pid: return None
    if res.get('decided_by') != 'typed_claim_checks': return None
    faults = [f for f in res.get('claim_feedback', []) if f.get('decided_by') and f['label'] != 'unknown']
    if not faults: return None
    for f in faults:
        a = packet.get('source_claim_assertions', {}).get(f.get('claim_id'))
        if (f.get('packet_id') != packet['id'] or not a or f.get('source_assertion_id') != a.get('id')
            or compile_assertion_id(a) != a['id'] or a.get('patient_id') != pid
            or a.get('source', {}).get('patient_id') != pid or a.get('subject') != 'patient'
            or f.get('path') != ['answer_rows', int(f['claim_id'][7:]), 'observation']): return None
    return p
```

- **After each result:** if `fault(res)`, compute `fam = local_family(...)`. Add it to `blocked` if not None, else set `stopped = True`.
- **In `fill()`:** skip indices whose family is in `blocked`. Do not submit them, and leave them out of `observed`.
- **Sequential path:** `break` becomes the same logic with `continue`.
- **`scheduled[0]`** keeps the global behavior. Parents have no `source_dependency_parent_id`, so they fall out as global.
- **Return value:** add `blocked_family_ids`, `global_stop`, and a per-packet unchecked status (`awaiting_local_repair` vs `awaiting_dependency_repair`).
  - `failed_packet_ids`, the overall failing label and `all_dependencies_checked=False` must still hold.
  - A mechanical-closure parent must not read as accepted while its children are unchecked.
- **Store the existing fault payload:** the `reason` string is the label definition, not a case rationale. Persist the provider's existing per-claim response and a prompt digest in the fault row, with no new calls.

**Regression cases** (fake decider, no model calls):
1. Local fault in family A: B and C still checked, A's later siblings unchecked with reason, overall failure kept.
2. Tampered assertion value, so the id does not recompile: global.
3. Patient mismatch in any of the packet, parent, `assertion.patient_id` or `source.patient_id`, or `subject != 'patient'`: global.
4. Fault with no `source_assertion_id`, or one local fault plus one id-less fault: global.
5. `claim_id` and `path` index disagree: global.
6. Unknown-only feedback: no block and no stop, same as today.
7. First-packet fault: no further submissions.
8. Parent does not list the child, or fingerprint mismatch: global or `ValueError`.
9. Two siblings in flight, one local fault: the sibling finishes, and its receipts and attempts are retained.
10. A global fault arriving after a local one stops new submissions and keeps the block.
11. Sequential path matches parallel on blocked and unchecked sets.
12. Quarantine called only for located ids.
13. Accounting: no packet submitted twice, and attempts equal the sum of the receipts.

## 3. What remains unresolved

I cannot see the whole-context packet-level label for SYN. The visible conflict is `jev` claim-level `missing_evidence` against `perplexity` packet-level `supported` (0.95) on the same two assertions.

- **Different rubrics.** The whole path adds a field-meaning check through `final_field_uses` and an unseen localizer prompt. The narrow path checks the exact source against the bound question.
- **Defensible readings on both sides.** The quote gives a reason: "did not escalate the blood pressure medication on this single office measurement". But it never names that medication as a member of the GDMT group, so the bridge is inferential. The question also asks for a documented target dose or constraint.
- **Possible over-broad value.** The asserted value is the whole 436-character span, including pill counts that are not an explanation. A checker may be rejecting the value as an explanation, not the quote's existence. That is a hypothesis, not a finding.
- **Possible grading mismatch.** The assertions are `machine_annotation` with `current_clinical_state_established: false`. The fault wording, "asserts a fact that is not established", suggests a clinical-claim rubric applied to a documentary observation. I cannot confirm that without the localizer prompt.
- **A further oddity.** Two records 19 months apart share an identical `document_text_digest` and identical text. That looks like templated text. It is relevant to temporal "current" binding but does not decide the disagreement.

**Resolve without voting or threshold changes.** Compare the stored prompts, inputs and option sets of the two paths offline. Then split the verdict into three:
- source fidelity (quote exists and says this),
- field fit (satisfies the titration-reason meaning),
- clinical state (never asserted).

A documented-but-unbridged observation can then be reported as a supported unknown for the field. Add one adjudicated fixture for these two cases, labeled as a test and not as clinical review.

## 4. What this would and would not establish

**It would establish:**
- One located, provably patient-local documentary fault no longer suppresses unrelated independent families.
- Coverage of the 122 deferred packets becomes measurable, with explicit blocked, unchecked and in-flight accounting.
- The overall answer still fails.
- Ambiguous scope still stops the run.

**It would not establish:**
- That the fault or the narrow pass is correct.
- That the answer is accepted.
- Any clinical or model-superiority claim.
- Absence of undeclared cross-patient dependence. The guard is only as good as the producer metadata.
- Progress on the other product areas. It is a verifier-scheduling candidate.
- Speed. The run becomes longer and costlier, and the metric is coverage.

Default activation needs workflow evidence and a known-good recovery point. The real blocker for the gate is the field-fit adjudication in section 3, not scheduling.
