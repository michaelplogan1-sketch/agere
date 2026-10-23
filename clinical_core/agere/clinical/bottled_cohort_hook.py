"""Hook: the narrow bottled slot rung (bottled.cohort_slots) as a free step inside the cohort-family plan.

plan() has cohort_families.plan's signature and return shape. With AGERE_BOTTLED_COHORT off (the default) or no adapter
on disk it IS cohort_families.plan (no model is loaded, nothing changes). When on, the order is:

  deterministic parser (complete parse)          -> used as today
  parser declines, or leaves slots missing       -> bottled slot rung ($0, k decodes must agree, slots must round-trip
                                                    through the parser); a model answer for a different family than a
                                                    partial parse found is not used
  still missing                                  -> Haiku slot filler, as today (needs the ledger)

The bottled step never appears in 'calls' (the planner counts calls as paid API calls); its receipt rides on
receipt['bottled']. Wiring: docs/agere/BOTTLING_INTEGRATION.md.
"""
import time

from . import answer_budget, bottled, cohort_families as cf, tier_timing
from .foreground_priority import LOCAL_MODEL_LOCK

SLOT_FILLER = 'cohort_slot_filler'  # tier_timing key of the local slot fill (the cohort-family tiers run no model otherwise)


class SlotFillGate:
    """Decides whether the local slot fill may start for this question and runs it under the local-model lock. One per question (the
    early and late cohort-family tiers share it): it starts only when the deterministic parse needs it (the hook asks only then), when the
    answer budget and the tier's time share cover its measured cost (tier_timing, key cohort_slot_filler), when the lock is free in time
    (a foreground caller is served before a background one), and at most once per question. Otherwise it skips with a reason and the
    tier declines at once (heal: later tiers try). .receipt() -> {'ran', 'seconds', 'skipped'} for the planner receipt."""
    def __init__(self, stack_path=None, *, clock=time.monotonic, lock=LOCAL_MODEL_LOCK):
        self.stack_path, self.clock, self.lock = stack_path, clock, lock
        self.ran, self.seconds, self.skipped = False, None, []

    def estimate(self):
        return tier_timing.estimate(self.stack_path, SLOT_FILLER)

    def skip(self, reason):
        self.skipped.append(reason)
        return None

    def run(self, fn):
        """-> fn() under the local-model lock, or None when it should not start (reason in self.skipped)."""
        if self.ran: return self.skip('slot filler already ran for this question')
        est = self.estimate()
        why = answer_budget.cannot_finish(est)
        if why: return self.skip(why)
        wait = answer_budget.current().remaining()
        wait = None if wait == float('inf') else max(0.0, wait - est)  # waiting for the lock must leave room to run
        if not self.lock.acquire(timeout=wait): return self.skip(f'local model busy (waited {wait:.0f}s)')
        try:
            why = answer_budget.cannot_finish(est)  # the wait may have used the time
            if why: return self.skip(why)
            self.ran, started = True, self.clock()
            try: return fn()
            finally:
                self.seconds = round(self.clock() - started, 3)
                tier_timing.record(self.stack_path, {SLOT_FILLER: [self.seconds]})
        finally:
            self.lock.release()

    def receipt(self):
        return {'ran': self.ran, 'seconds': self.seconds, 'skipped': list(self.skipped)}


def plan(path, question, as_of, *, ledger=None, receipts=None, model=None, gate=None):
    if cf.scope_rejection(question):
        return None  # The model cannot erase a constraint rejected by the same tool.
    use = bottled.narrow_enabled() and (model is not None or bottled.NarrowModel.available())
    if not use:
        return cf.plan(path, question, as_of, ledger=ledger, receipts=receipts)
    parsed = cf.parse(question, as_of)
    bottled_receipt = None
    if parsed is None or parsed['missing']:
        try:
            call = lambda: bottled.cohort_slots(question, as_of, model=model)
            if gate is None:
                with LOCAL_MODEL_LOCK: got, bottled_receipt = call()  # the cohort-family tiers no longer hold the lock for the whole tier
            else:
                ran = gate.run(call)
                got, bottled_receipt = ran if ran is not None else (None, {'version': bottled.NARROW_VERSION, 'declined': 'slot filler skipped: ' + '; '.join(gate.skipped[-1:])})
        except (ValueError, OSError, RuntimeError, ImportError) as exc:  # the free rung never blocks the ladder
            got, bottled_receipt = None, {'version': bottled.NARROW_VERSION, 'declined': f'{type(exc).__name__}: {exc}'[:300]}
        if got is not None and (parsed is None or got['family'] == parsed['family']):
            parsed = got
    if parsed is None: return None
    calls = []
    if parsed['missing']:
        if ledger is None or receipts is None: return None
        parsed, call = cf.model_fill(question, parsed, ledger, receipts, as_of)
        calls.append(call)
    spec = cf.validate({'family': parsed['family'], 'parameters': parsed['parameters'], 'returns': parsed['returns']})
    tables = cf._tables(path) if path is not None else None
    receipt = cf.receipt_for(spec, as_of, parsed)
    if gate is not None and (gate.ran or gate.skipped): receipt['slot_filler'] = gate.receipt()
    if bottled_receipt is not None:
        receipt['bottled'] = {k: v for k, v in bottled_receipt.items() if k != 'decodes'}
        receipt['bottled']['used'] = bool(parsed.get('slots'))
    return {'graph': cf.to_graph(spec, as_of, tables), 'receipt': receipt, 'calls': calls, 'spec': spec}
