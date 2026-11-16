"""One live cascade probe within the existing user-authorized total pilot cap."""
import json
from pathlib import Path

from clinical_core.providers import HostedGateway, EvidenceTask, JevProvider, ClaudeProvider, Cascade
from live_provider_pilot import load_backend_keys


def main():
    root = Path(__file__).resolve().parent
    folder = root / "artifacts/live-provider-pilot"
    target = folder / "routing-probe.json"
    if target.exists():
        raise SystemExit("Routing probe already attempted; not spending again")
    prior = json.loads((folder / "summary.json").read_text())
    remaining = prior["authorized_cap_usd"] - prior["reserved_usd"]
    if remaining < 1:
        raise SystemExit("Insufficient remaining reservation for two calls")
    load_backend_keys(root.parent / "insight-engine/.env")
    first = json.loads((folder / "requests.json").read_text())[0]["providers"]["jev"]
    task = EvidenceTask(first["state"]["subject"], first["state"]["as_of"], first["questions"]["reason"]["criteria"], first["state"]["evidence"])
    gateway = HostedGateway(allow_live=True, max_calls=2, max_usd=remaining, cache_dir=root / "artifacts/provider-cache")
    target.write_text(json.dumps({"status": "started", "prior_reserved_usd": prior["reserved_usd"], "additional_reserved_ceiling_usd": 1}))
    result = {"synthetic": True}
    try:
        result["result"] = Cascade(JevProvider(gateway), ClaudeProvider(gateway)).classify(task)
    except Exception as exc:
        result["error"] = type(exc).__name__
    finally:
        result.update(events=gateway.events, total_pilot_reserved_usd=prior["reserved_usd"]+gateway.reserved_usd,
                      estimated_total_api_usd=prior["estimated_api_usd"]+sum(e.get("estimated_api_usd", 0) for e in gateway.events),
                      unpriced_attempts=prior["unpriced_attempts"]+sum("estimated_api_usd" not in e for e in gateway.events))
        target.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
