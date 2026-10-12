"""Finite expected value of sample information under EXPLICIT assumptions.

Unlike the prior-free decision kernel, this requires supplied probabilities,
utilities and observation likelihoods. No patient benefit or cost is estimated
here. Utilities and acquisition costs must use the same declared scale.
"""
import math


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _distribution(values, keys):
    if not isinstance(values, dict) or set(values) != set(keys):
        raise ValueError("Distribution states differ")
    if any(not _number(p) or p < 0 or p > 1 for p in values.values()) or abs(sum(values.values()) - 1) > 1e-9:
        raise ValueError("Invalid probability distribution")


def expected_information_value(*, prior, utilities, channels, utility_scale, assumption_source):
    if not isinstance(prior, dict) or not prior or len(prior) > 256 or not all(isinstance(k, str) and k for k in prior):
        raise ValueError("A bounded named-state prior is required")
    _distribution(prior, prior)
    if not isinstance(utility_scale, str) or not utility_scale.strip() or not isinstance(assumption_source, str) or not assumption_source.strip():
        raise ValueError("Utility scale and assumption provenance are required")
    if not isinstance(utilities, dict) or not utilities or len(utilities) > 64:
        raise ValueError("A bounded set of action utilities is required")
    for action, values in utilities.items():
        if not isinstance(action, str) or not action or not isinstance(values, dict) or set(values) != set(prior) or any(not _number(x) for x in values.values()):
            raise ValueError("Each action needs a finite utility for every state")

    def best(weights):
        options = {a: sum(weights[s]*values[s] for s in prior) for a, values in utilities.items()}
        if any(not math.isfinite(value) for value in options.values()):
            raise ValueError("Utility computation exceeds numerical range")
        action = max(sorted(options), key=options.get)
        return action, options[action]

    current_action, current_utility = best(prior)
    if not isinstance(channels, dict) or len(channels) > 64:
        raise ValueError("A bounded channel map is required")
    results = []
    for name, channel in channels.items():
        if not isinstance(name, str) or not name:
            raise ValueError("Channel names must be nonempty strings")
        if not isinstance(channel, dict) or set(channel) != {"available", "cost", "likelihoods", "basis"}:
            raise ValueError("Channel requires availability, cost, likelihoods and basis")
        if type(channel["available"]) is not bool or not _number(channel["cost"]) or channel["cost"] < 0 or not isinstance(channel["basis"], str) or not channel["basis"].strip():
            raise ValueError("Invalid channel cost, availability or provenance")
        likelihoods = channel["likelihoods"]
        if not isinstance(likelihoods, dict) or set(likelihoods) != set(prior):
            raise ValueError("Observation likelihoods must cover every state")
        outcomes = next(iter(likelihoods.values()))
        if not isinstance(outcomes, dict) or not outcomes or len(outcomes) > 64 or not all(isinstance(o, str) and o for o in outcomes):
            raise ValueError("A bounded set of observation outcomes is required")
        for values in likelihoods.values():
            _distribution(values, outcomes)
        after, branches = 0.0, []
        for outcome in sorted(outcomes):
            weights = {s: prior[s]*likelihoods[s][outcome] for s in prior}
            probability = sum(weights.values())
            if probability == 0:
                continue
            posterior = {s: value/probability for s, value in weights.items()}
            action, utility = best(posterior)
            after += probability * utility
            branches.append({"observation": outcome, "assumed_probability": probability,
                             "assumed_posterior": posterior, "best_action_under_assumptions": action})
        gross = max(0.0, after-current_utility)  # Clamp floating-point roundoff.
        if not math.isfinite(gross) or not math.isfinite(gross-channel["cost"]):
            raise ValueError("Information value exceeds numerical range")
        results.append({"channel": name, "available": channel["available"],
                        "gross_value": gross, "net_value": gross-channel["cost"],
                        "assumed_cost": channel["cost"], "basis": channel["basis"], "branches": branches})
    results.sort(key=lambda r: (-r["net_value"], r["channel"]))
    feasible = [r for r in results if r["available"] and r["net_value"] > 0]
    return {"status": "assumption_based_research_calculation", "utility_scale": utility_scale,
            "assumption_source": assumption_source,
            "current_action_under_assumptions": current_action, "current_expected_utility": current_utility,
            "channels": results, "preferred_channel_under_assumptions": feasible[0]["channel"] if feasible else None,
            "automatic_acquisition": False, "clinical_probability_calibrated": False,
            "limitations": ["All probabilities, utilities, observation reliability and costs are supplied assumptions.",
                            "One acquisition is evaluated at a time; correlated acquisitions must not have their values added.",
                            "This result is not a patient treatment recommendation or a measured savings estimate."]}
