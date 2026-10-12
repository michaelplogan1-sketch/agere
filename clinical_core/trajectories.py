"""Connect observed lab series to the existing bounded astronomical primitive."""
from collections import defaultdict
from datetime import date
import math

from .contracts import digest
from .signal_math import gaussian_change_points


def analyze_measurements(patients, as_of):
    groups = defaultdict(list)
    for pid, feeds in sorted(patients.items()):
        for row in feeds["labs"]:
            if row["result_date"] <= as_of and (row.get("available_date") is None or row["available_date"] <= as_of):
                # Assay/source changes must not be merged into a physiological shift.
                key = (pid, row["test"], row.get("unit"), row.get("assay"), row.get("lab"))
                groups[key].append(row)
    results = []
    for (pid, test, unit, assay, lab), rows in sorted(groups.items(), key=lambda item: str(item[0])):
        rows = sorted(rows, key=lambda row: (row["result_date"], digest(row)))
        reasons = []
        if any(not isinstance(value, str) or not value.strip() for value in (unit, assay, lab)):
            reasons.append("Explicit common unit, assay and laboratory context required")
        if len(rows) < 3:
            reasons.append("Fewer than three observed measurements")
        if any(type(row.get("standard_error")) not in (int, float) or not math.isfinite(row["standard_error"]) or row["standard_error"] <= 0 for row in rows):
            reasons.append("Positive source-supplied measurement errors required; none are guessed")
        if any(type(row.get("value")) not in (int, float) or not math.isfinite(row["value"]) for row in rows):
            reasons.append("Finite numeric measurements required")
        if len({row["result_date"] for row in rows}) != len(rows):
            reasons.append("Same-day observations require finer timestamps or reviewed aggregation")
        result = {"patient_id": pid, "test": test, "unit": unit, "assay": assay, "lab": lab,
                  "observations": len(rows), "source_hashes": [digest(row) for row in rows],
                  "status": "not_computed" if reasons else "measurement_pattern_only", "reasons": reasons,
                  "diagnosis": None, "automatic_action": False}
        if not reasons:
            try:
                result["analysis"] = gaussian_change_points(
                    [date.fromisoformat(row["result_date"]).toordinal() for row in rows],
                    [row["value"] for row in rows], [row["standard_error"] for row in rows])
            except ValueError as exc:
                result.update(status="not_computed", reasons=[str(exc)])
        results.append(result)
    return results
