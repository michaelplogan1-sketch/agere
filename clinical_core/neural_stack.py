"""Run installed specialists and learned simulator hypotheses over the source ledger.

All inference is local. Research outputs do not overwrite accepted facts or
the conditional clinical workflow. No training truth is loaded by this runner.
"""
from __future__ import annotations

import argparse
import html
import json
import time
from collections import Counter
from pathlib import Path

import numpy as np

from .bridge import InsightBridge
from .contracts import digest
from .corpus import load_world
from .hybrid_retrieval import retrieve
from .inputs import load_sample
from .joint_observation import SUBJECTS, infer_joint, observed_features
from .local_encoders import LocalEncoder, LocalReranker
from .process_monitoring import source_volume_monitors
from .stack import IntegratedStack

ROOT = Path(__file__).resolve().parents[1]
QUERIES = {
    "mra": "Spironolactone eplerenone mineralocorticoid antagonist: current treatment, hold, hyperkalemia, intolerance, outside fill, access barrier or uncertain medication history.",
    "sglt2": "Empagliflozin dapagliflozin SGLT2 inhibitor: current treatment, hold, intolerance, outside fill, affordability, prior authorization or uncertain medication history.",
    "arni": "Sacubitril valsartan Entresto ARNI: current treatment, hold, hypotension, outside fill, affordability or uncertain medication history.",
    "bb": "Carvedilol metoprolol succinate bisoprolol beta blocker: current treatment, hold, bradycardia, intolerance, outside fill or uncertain medication history."}


def write_view(result, path):
    escape = html.escape
    cases = []
    for case in result["specialist_cases"]:
        findings = case["joint_hypothesis"]
        hypotheses = "".join(f"<li>{escape(str(s['state']))}: {s['probability']:.3f}</li>"
                             for s in sorted(findings["joint_distribution"], key=lambda s: -s["probability"])[:3])
        passages = "".join(f"<article><small>{escape(e['note_id'])} · {e['event_date']} · chars {e['start']}–{e['end']}"
                           f" · {'protected exception evidence' if e['protected'] else 'retrieved evidence'}</small>"
                           f"<blockquote>{escape(e['text'])}</blockquote></article>" for e in case["retrieval"]["evidence"])
        cases.append(f"<details><summary>{escape(case['patient_id'])} / {escape(case['subject'])} — {escape(case['evidence_state'])}</summary>"
                     f"<p>{escape(case['next_action'])}</p><h3>Simulator-conditioned hypotheses</h3><ul>{hypotheses}</ul>"
                     f"<p>Distinct training states with the same observed feature vector: {findings['distinct_states_with_same_observed_features']}. "
                     f"These scores are not validated clinical probabilities.</p><h3>Retrieved source passages</h3>{passages}</details>")
    page = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agere · Local specialist run</title><style>
:root{color-scheme:dark}body{margin:0;background:#071827;color:#eef4ed;font:17px/1.6 system-ui,sans-serif}main{max-width:1100px;margin:auto;padding:44px 24px}
h1{font-size:clamp(36px,6vw,64px);line-height:1.1;margin:12px 0}small,.eyebrow{color:#79d7d3}p{max-width:88ch}details{border-top:1px solid #355268;padding:20px 0}
summary{cursor:pointer;font-weight:600}article{padding:14px 0;border-top:1px solid #294255}blockquote{margin:8px 0;white-space:pre-wrap;overflow-wrap:anywhere}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#102b3c;padding:20px;border-radius:8px}a{color:#86e5de}h3{color:#edc481;font-weight:500}
</style><main><div class="eyebrow">AGERE / SYNTHETIC LOCAL RESEARCH</div><h1>The specialists are running.</h1>
<p>Versioned source evidence → lexical and Granite retrieval → BGE reranking → BioClinical representations.
The learned patient/source prototype supplies separate research hypotheses. Clinical conclusions remain conditional and require review.</p>
<p>This is an execution report, not a clinical performance claim. Source quotes remain recoverable; selected passages do not guarantee complete evidence.</p>"""
    page += "<h2>Execution receipt</h2><pre>" + escape(json.dumps(result["summary"], indent=2)) + "</pre>"
    page += "<h2>Patient evidence</h2>" + "".join(cases) + "<h2>Compute receipt</h2><pre>" + escape(json.dumps(result["compute"], indent=2)) + "</pre></main></html>"
    path.write_text(page)


def run(batch, *, namespace, engine, database, vector_cache, model_root, joint_model, max_cases=12, rerank=True):
    if type(max_cases) is not int or not 1 <= max_cases <= 100:
        raise ValueError("Explicit bounded specialist case count required")
    started = time.perf_counter()
    stack = IntegratedStack(database, InsightBridge(engine))
    encoders = []
    try:
        base = stack.run(batch, namespace=namespace, max_packets=0)
        snapshot = stack.last_batch
        eligible = [c for c in base["cases"] if c["subject"] in SUBJECTS]
        hypotheses = {c["case_id"]: infer_joint(joint_model, observed_features(c)) for c in eligible}
        # Round-robin across observed review states; no truth-based selection.
        groups = {}
        for c in eligible:
            groups.setdefault(c["state"], []).append(c)
        selected = []
        while len(selected) < max_cases and any(groups.values()):
            for state in sorted(groups):
                if groups[state] and len(selected) < max_cases:
                    selected.append(groups[state].pop(0))
        granite = LocalEncoder(model_root / "granite-english-r2", pooling="cls", cache=vector_cache)
        encoders.append(granite)
        bio = LocalEncoder(model_root / "bioclinical-modernbert", pooling="mean", cache=vector_cache)
        encoders.append(bio)
        ranker = LocalReranker(model_root / "bge-reranker-v2-m3", cache=vector_cache) if rerank else None
        if ranker:
            encoders.append(ranker)
        cases, representations = [], {}
        for case in selected:
            notes = snapshot.patients[case["patient_id"]]["notes"]
            note_ids = {digest(n): n["note_id"] for n in notes}
            protected = [{"note_id": note_ids[a["reference"]["source_hash"]], "start": a["reference"]["start"],
                          "end": a["reference"]["end"]} for a in case["evidence"]
                         if a["reference"]["source_hash"] in note_ids and a["reference"]["start"] is not None
                         and a["kind"] in {"reported_outside", "documented_exception", "documented_barrier"}]
            query = QUERIES[case["subject"]]
            packet = retrieve(granite, case["patient_id"], notes, query, snapshot.as_of,
                              top_k=24 if ranker else 8, protected=protected, provenance=base["source_provenance"])
            if ranker:
                candidates = len(packet["evidence"])
                packet["evidence"] = ranker.rank(query, packet["evidence"], top_k=8)
                packet["reranker"] = {"identity": ranker.identity, "candidate_count": candidates,
                                      "score_interpretation": "Relevance logit, not clinical probability"}
                packet["unselected_chunks"] = packet["indexed_chunks"] - len(packet["evidence"])
                if packet.get("protected_chunks", 0) > 8:
                    packet["status"] = "protected_evidence_overflow"
            features = bio.encode([e["text"] for e in packet["evidence"]])
            representations[case["case_id"]] = features
            cases.append({"case_id": case["case_id"], "patient_id": case["patient_id"], "subject": case["subject"],
                          "evidence_state": case["state"], "next_action": case["next_action"],
                          "retrieval": packet, "joint_hypothesis": hypotheses[case["case_id"]],
                          "clinical_representation": {"encoder_identity": bio.identity, "shape": list(features.shape),
                              "task_head_trained": False, "meaning": "Frozen pretrained features; not clinical predictions"},
                          "conditional_analysis": case["conditional_analysis"]})
        result = {"schema_version": "agere-local-specialists/1", "synthetic": True, "namespace": namespace,
                  "baseline_run_id": base["run_id"], "snapshot_id": snapshot.snapshot_id,
                  "as_of": snapshot.as_of, "joint_hypotheses": hypotheses, "specialist_cases": cases,
                  "source_process_monitors": source_volume_monitors(snapshot.patients, snapshot.as_of),
                  "models": {"retriever": granite.manifest, "clinical_encoder": bio.manifest,
                             "reranker": ranker.manifest if ranker else None, "joint_model_id": joint_model["model_id"]},
                  "summary": {"patients": len(snapshot.patients), "baseline_assessments": len(base["cases"]),
                              "joint_research_assessments": len(hypotheses), "neural_cases": len(cases),
                              "neural_patients": len({c["patient_id"] for c in cases}),
                              "retrieval_states": dict(Counter(c["retrieval"]["status"] for c in cases)),
                              "clinical_workflow_states": base["summary"]["conditional_states"]},
                  "compute": {"baseline": base["compute"], "granite": dict(granite.stats), "bioclinical": dict(bio.stats),
                              "bge": dict(ranker.stats) if ranker else None, "device": "cpu",
                              "total_elapsed_seconds": time.perf_counter() - started,
                              "external_model_calls": 0, "paid_api_usd": 0, "total_operating_cost_usd": None},
                  "limitations": ["Synthetic simulator-conditioned hypotheses; no automatic clinical action.",
                      "Clinical encoder has no trained Agere clinical task head yet.",
                      "Retrieval and reranking are proposals, not entailment or evidence-completeness guarantees.",
                      "Neural execution is bounded to the reported case subset; joint inference covers supported baseline cases.",
                      "No held-out clinical accuracy, total-cost advantage or production-scale claim."]}
        result["run_id"] = digest({k: v for k, v in result.items() if k != "compute"})
        return result, representations
    finally:
        for encoder in encoders:
            encoder.close()
        stack.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sources = p.add_mutually_exclusive_group(required=True)
    sources.add_argument("--world", type=Path)
    sources.add_argument("--sample", type=Path)
    p.add_argument("--namespace", required=True)
    p.add_argument("--as-of", default="2026-09-30")
    p.add_argument("--engine", type=Path, default=ROOT.parent / "insight-engine")
    p.add_argument("--database", type=Path, default=ROOT / "artifacts/agere-neural/runtime.sqlite3")
    p.add_argument("--vector-cache", type=Path, default=ROOT / "artifacts/agere-neural/encoder-cache.sqlite3")
    p.add_argument("--model-root", type=Path, default=ROOT / "models")
    p.add_argument("--joint-model", type=Path, default=ROOT / "artifacts/agere-joint-v1/joint-model.json")
    p.add_argument("--max-cases", type=int, default=12)
    p.add_argument("--skip-reranker", action="store_true")
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    if args.output.exists():
        p.error("Choose a new output directory")
    batch = load_world(args.world, args.as_of) if args.world else load_sample(args.sample, args.as_of)
    result, vectors = run(batch, namespace=args.namespace, engine=args.engine, database=args.database,
        vector_cache=args.vector_cache, model_root=args.model_root, joint_model=json.loads(args.joint_model.read_text()),
        max_cases=args.max_cases, rerank=not args.skip_reranker)
    args.output.mkdir(parents=True)
    (args.output / "run.json").write_text(json.dumps(result, indent=2) + "\n")
    np.savez_compressed(args.output / "clinical-representations.npz", **vectors)
    write_view(result, args.output / "index.html")
    print(json.dumps({"summary": result["summary"], "compute": result["compute"]}, indent=2))


if __name__ == "__main__":
    main()
