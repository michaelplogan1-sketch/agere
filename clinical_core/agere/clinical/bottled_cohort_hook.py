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
from . import bottled, cohort_families as cf


def plan(path, question, as_of, *, ledger=None, receipts=None, model=None):
    if cf.scope_rejection(question):
        return None  # The model cannot erase a constraint rejected by the same tool.
    use = bottled.narrow_enabled() and (model is not None or bottled.NarrowModel.available())
    if not use:
        return cf.plan(path, question, as_of, ledger=ledger, receipts=receipts)
    parsed = cf.parse(question, as_of)
    bottled_receipt = None
    if parsed is None or parsed['missing']:
        try:
            got, bottled_receipt = bottled.cohort_slots(question, as_of, model=model)
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
    if bottled_receipt is not None:
        receipt['bottled'] = {k: v for k, v in bottled_receipt.items() if k != 'decodes'}
        receipt['bottled']['used'] = bool(parsed.get('slots'))
    return {'graph': cf.to_graph(spec, as_of, tables), 'receipt': receipt, 'calls': calls, 'spec': spec}
