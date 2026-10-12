"""Classical CUSUM/EWMA research primitives; flags are not clinical findings."""
import math
from collections import Counter
from datetime import date, timedelta
from statistics import mean, stdev

from .inputs import DATE_FIELDS


def monitor(values, *, reference_mean, reference_sd, allowance=0.5, threshold=5.0, weight=0.2):
    params = (reference_mean, reference_sd, allowance, threshold, weight, *values)
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in params):
        raise ValueError("Finite numeric series and parameters required")
    if reference_sd <= 0 or allowance < 0 or threshold <= 0 or not 0 < weight <= 1:
        raise ValueError("Invalid process monitoring assumptions")
    up, down, smoothed = 0.0, 0.0, reference_mean
    result = []
    for i, value in enumerate(values):
        z = (value - reference_mean) / reference_sd
        up, down = max(0, up + z - allowance), min(0, down + z + allowance)
        smoothed = weight * value + (1 - weight) * smoothed
        result.append({"index": i, "observed": value, "cusum_up": up, "cusum_down": down,
                       "ewma": smoothed, "investigate_source_change": up >= threshold or down <= -threshold})
    return {"status": "assumption_based_process_monitor", "points": result,
            "parameters": {"reference_mean": reference_mean, "reference_sd": reference_sd,
                           "allowance": allowance, "threshold": threshold, "weight": weight},
            "interpretation": "Investigate collection, volume and source changes; not evidence of worsening care."}


def source_volume_monitors(patients, as_of):
    """Flag volume changes in observed feeds, without inferring complete capture."""
    cutoff = date.fromisoformat(as_of)
    next_month = (cutoff.replace(day=28) + timedelta(days=4)).replace(day=1)
    last_complete = cutoff.strftime("%Y-%m") if cutoff == next_month - timedelta(days=1) else (cutoff.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    results = []
    for source in ("notes", "pharmacy_claims", "labs"):
        counts = Counter()
        for feeds in patients.values():
            for record in feeds[source]:
                event = record[DATE_FIELDS[source]]
                if event <= as_of and (record.get("available_date") or as_of) <= as_of and event[:7] <= last_complete:
                    counts[event[:7]] += 1
        if not counts:
            results.append({"source": source, "status": "no_observed_records"})
            continue
        month = date.fromisoformat(min(counts) + "-01")
        months = []
        while month.strftime("%Y-%m") <= last_complete:
            months.append(month.strftime("%Y-%m"))
            month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
        values = [counts[m] for m in months]
        base = {"source": source, "months": months, "record_counts": values,
                "baseline_months": months[:6], "population": "Supplied roster; no changing-coverage adjustment",
                "basis": "First six complete observed calendar months as an engineering reference, not a validated stable process"}
        if len(values) < 8 or stdev(values[:6]) == 0:
            results.append({**base, "status": "insufficient_variable_reference"})
            continue
        result = monitor(values[6:], reference_mean=mean(values[:6]), reference_sd=stdev(values[:6]))
        for point in result["points"]:
            point["month"] = months[point["index"] + 6]
        results.append({**base, **result})
    return results
