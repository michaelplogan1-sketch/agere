"""Bounded, prior-free analysis of versioned Boolean decision programs.

Certificates are conditional logical guarantees under the supplied program and
admissible states, not guarantees of clinical truth or a newly invented theorem.
There is no inference from missing observations to a negative clinical fact.
"""
from __future__ import annotations

import itertools
import math
from datetime import date

from .contracts import digest

MAX_FACTS = 8
MAX_EXPRESSION_NODES = 128


def _date(value):
    # Comparison below relies on canonical ISO calendar-date ordering. Python's
    # parser also accepts compact and week dates, which cannot be mixed safely.
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Dates must use YYYY-MM-DD")


def _expression(expr, variables, budget):
    budget[0] -= 1
    if budget[0] < 0:
        raise ValueError("Program exceeds expression budget")
    if type(expr) is bool:
        return
    if not isinstance(expr, dict) or len(expr) != 1:
        raise ValueError("Expressions must be Boolean literals or one allowed operator")
    op, value = next(iter(expr.items()))
    if op == "fact":
        if not isinstance(value, str) or value not in variables:
            raise ValueError("Unknown fact")
    elif op == "not":
        _expression(value, variables, budget)
    elif op in {"all", "any"}:
        if not isinstance(value, list) or not value:
            raise ValueError("Logical groups must be nonempty lists")
        for child in value:
            _expression(child, variables, budget)
    else:
        raise ValueError("Operator not allowed")


def _evaluate(expr, assignment):
    if type(expr) is bool:
        return expr
    op, value = next(iter(expr.items()))
    if op == "fact":
        return assignment[value]
    if op == "not":
        return not _evaluate(value, assignment)
    return (all if op == "all" else any)(_evaluate(e, assignment) for e in value)


class DecisionProgram:
    def __init__(self, spec):
        required = {"id", "version", "synthetic", "effective_from", "effective_to",
                    "authority", "facts", "constraints", "branches", "default"}
        if not isinstance(spec, dict) or set(spec) != required or spec["synthetic"] is not True:
            raise ValueError("Expected a complete synthetic decision-program specification")
        for key in ("id", "version", "authority", "default"):
            if not isinstance(spec[key], str) or not spec[key].strip():
                raise ValueError("Program identity, authority and outcomes must be explicit")
        for key in ("effective_from", "effective_to"):
            _date(spec[key])
        if spec["effective_from"] > spec["effective_to"]:
            raise ValueError("Invalid policy effective interval")
        facts = spec["facts"]
        if not isinstance(facts, dict) or not 1 <= len(facts) <= MAX_FACTS:
            raise ValueError("Program exceeds bounded fact count")
        for key, value in facts.items():
            if not isinstance(key, str) or not key or not isinstance(value, dict) or set(value) != {"question", "source"}:
                raise ValueError("Each fact needs a question and intended source")
            if not all(isinstance(v, str) and v.strip() for v in value.values()):
                raise ValueError("Empty fact definition")
        if not isinstance(spec["constraints"], list) or not isinstance(spec["branches"], list) or not spec["branches"]:
            raise ValueError("Expected explicit constraints and ordered branches")
        budget = [MAX_EXPRESSION_NODES]
        for expr in spec["constraints"]:
            _expression(expr, facts, budget)
        for branch in spec["branches"]:
            if not isinstance(branch, dict) or set(branch) != {"when", "outcome"} or not isinstance(branch["outcome"], str) or not branch["outcome"]:
                raise ValueError("Invalid branch")
            _expression(branch["when"], facts, budget)
        # JSON round-trip keeps caller mutation from changing a compiled program.
        import json
        self.spec = json.loads(json.dumps(spec, allow_nan=False))
        self.revision = digest(self.spec)
        self.names = sorted(facts)
        self.table = []
        for values in itertools.product((False, True), repeat=len(self.names)):
            assignment = dict(zip(self.names, values))
            if not all(_evaluate(expr, assignment) for expr in self.spec["constraints"]):
                continue
            outcome = next((b["outcome"] for b in self.spec["branches"] if _evaluate(b["when"], assignment)), self.spec["default"])
            self.table.append((assignment, outcome))
        if not self.table:
            raise ValueError("Program constraints admit no state")

    def analyze(self, observations, as_of, acquisition=None):
        if digest(self.spec) != self.revision:
            raise ValueError("Compiled program was mutated")
        _date(as_of)
        if not self.spec["effective_from"] <= as_of <= self.spec["effective_to"]:
            raise ValueError("Program is not effective at this cutoff")
        if not isinstance(observations, dict) or set(observations) - set(self.names):
            raise ValueError("Observation refers to an unknown fact")
        acquisition = {} if acquisition is None else acquisition
        if not isinstance(acquisition, dict) or set(acquisition) - set(self.names):
            raise ValueError("Unknown acquisition target")
        for item in acquisition.values():
            if not isinstance(item, dict) or set(item) != {"available", "cost_units", "basis"}:
                raise ValueError("Acquisition needs availability, cost units and assumption provenance")
            if type(item["available"]) is not bool or type(item["cost_units"]) not in (int, float) or not math.isfinite(item["cost_units"]) or item["cost_units"] < 0:
                raise ValueError("Invalid acquisition cost or availability")
            if not isinstance(item["basis"], str) or not item["basis"].strip():
                raise ValueError("Acquisition cost needs a stated basis")
        known, unaccepted = {}, {}
        for name, item in observations.items():
            if not isinstance(item, dict) or set(item) != {"value", "status", "available_date", "valid_from", "valid_to", "support_ids"}:
                raise ValueError("Fact requires value, provenance, review state and temporal validity")
            if item["value"] is not None and type(item["value"]) is not bool:
                raise ValueError("Facts must be Boolean or explicitly unknown")
            if item["status"] not in {"reviewed", "source_record", "proposal", "conflicting", "unknown"}:
                raise ValueError("Unknown fact status")
            for key in ("available_date", "valid_from", "valid_to"):
                if item[key] is not None:
                    _date(item[key])
            if item["valid_from"] and item["valid_to"] and item["valid_from"] > item["valid_to"]:
                raise ValueError("Invalid fact validity interval")
            if not isinstance(item["support_ids"], list) or any(not isinstance(x, str) or not x for x in item["support_ids"]):
                raise ValueError("Invalid support identifiers")
            reason = None
            if item["value"] is None:
                reason = "unknown_value"
            elif item["status"] not in {"reviewed", "source_record"}:
                reason = "unaccepted_or_conflicting_assertion"
            elif item["available_date"] is None:
                reason = "unknown_arrival_time"
            elif item["available_date"] > as_of:
                reason = "unavailable_at_cutoff"
            elif item["valid_from"] is None or item["valid_to"] is None:
                reason = "unknown_validity_interval"
            elif not item["valid_from"] <= as_of <= item["valid_to"]:
                reason = "outside_validity_interval"
            elif not item["support_ids"]:
                reason = "unsupported_assertion"
            if reason:
                unaccepted[name] = reason
            else:
                known[name] = item["value"]
        feasible = [(a, o) for a, o in self.table if all(a[k] == v for k, v in known.items())]
        outcomes = sorted({o for _, o in feasible})
        result = {"program_id": self.spec["id"], "program_version": self.spec["version"],
                  "program_revision": self.revision, "as_of": as_of, "synthetic": True,
                  "accepted_facts": known, "unaccepted_facts": unaccepted,
                  "possible_outcomes": outcomes, "admissible_completions": len(feasible),
                  "status": "inconsistent_inputs" if not feasible else "determined" if len(outcomes) == 1 else "unresolved",
                  "outcome": outcomes[0] if len(outcomes) == 1 else None,
                  "probabilities": None, "automatic_action": False,
                  "certificate": None, "questions": [], "minimal_resolution_sets": [],
                  "limitations": ["Conditional on the supplied facts, constraints and policy; not clinical truth.",
                                  "Completions are possibilities, not equally likely patient states.",
                                  "Logical influence is not a causal treatment effect or expected clinical benefit.",
                                  "Source IDs and review status are caller-supplied; their authenticity is not established here."]}
        if not feasible:
            return result
        if len(outcomes) == 1:
            # First minimal-cardinality certificate. Re-expansion includes all
            # other facts, so irrelevant context can be omitted without changing
            # this program's result; raw chart evidence must remain recoverable.
            for size in range(len(known) + 1):
                found = False
                for subset in itertools.combinations(sorted(known), size):
                    expanded = {o for a, o in self.table if all(a[k] == known[k] for k in subset)}
                    if expanded == set(outcomes):
                        result["certificate"] = {"facts": {k: known[k] for k in subset},
                            "support_ids": sorted({s for k in subset for s in observations[k]["support_ids"]}),
                            "minimal_cardinality": True, "scope": "this program and declared admissible states"}
                        found = True
                        break
                if found:
                    break
            return result
        unknown = [n for n in self.names if n not in known]
        for name in unknown:
            branches = {str(v).lower(): sorted({o for a, o in feasible if a[name] == v}) for v in (False, True)}
            groups = defaultdict_outcomes(feasible, [n for n in self.names if n != name])
            pivotal = (any(len(values) > 1 for values in groups.values())
                       or (bool(branches["false"]) and bool(branches["true"])
                           and branches["false"] != branches["true"]))
            worst = max(map(len, branches.values()))
            result["questions"].append({"fact": name, **self.spec["facts"][name],
                "can_change_outcome": pivotal, "outcomes_if_resolved": branches,
                "worst_case_remaining_outcomes": worst,
                "guaranteed_outcome_reduction": len(outcomes) - worst,
                "acquisition": acquisition.get(name), "automatic_fetch": False})
        minimal_sets = []
        for size in range(1, len(unknown) + 1):
            for subset in itertools.combinations(unknown, size):
                if any(set(previous) <= set(subset) for previous in minimal_sets):
                    continue
                grouped = defaultdict_outcomes(feasible, subset)
                if all(len(values) == 1 for values in grouped.values()):
                    minimal_sets.append(subset)
        for subset in minimal_sets:
            costs_known = all(n in acquisition for n in subset)
            result["minimal_resolution_sets"].append({"facts": list(subset),
                "available": all(acquisition[n]["available"] for n in subset) if costs_known else None,
                "assumed_cost_units": sum(acquisition[n]["cost_units"] for n in subset) if costs_known else None})
        candidates = [s for s in result["minimal_resolution_sets"] if s["available"] is True]
        result["least_cost_declared_resolution_set"] = min(candidates, key=lambda s: (s["assumed_cost_units"], s["facts"])) if candidates else None
        result["questions"].sort(key=lambda q: (-q["guaranteed_outcome_reduction"], -int(q["can_change_outcome"]), q["fact"]))
        return result


def defaultdict_outcomes(rows, names):
    grouped = {}
    for assignment, outcome in rows:
        grouped.setdefault(tuple(assignment[n] for n in names), set()).add(outcome)
    return grouped
