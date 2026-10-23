"""System One questions for note frames and the code that combines passage readings (STUB: interface only, filled next)."""
from __future__ import annotations


def evaluate(stack, predicate, patient_ids, *, as_of, ledger=None, workers=4, allow_live=True, all_details=False):
    """Interface stub: every patient is 'unknown' until the reader lands. Shape is final."""
    return {pid: {'status': 'unknown', 'detail': {}, 'evidence': [], 'p': None, 'holds': None, 'undecided': 0,
                  'note': 'note_questions stub: reader not implemented yet'} for pid in patient_ids}
