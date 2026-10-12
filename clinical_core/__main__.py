"""Run a lexicon or installed local MLX reader; never use hosted inference."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .bridge import InsightBridge
from .inputs import load_sample
from .runtime import Runtime


def main():
    home = Path(__file__).resolve().parents[2]
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument("--sample", type=Path, default=home / "caresignal-mvp/public/demo/patients_sample.json")
    sources.add_argument("--world", type=Path, help="Full synthetic world directory containing feeds/")
    parser.add_argument("--engine-root", type=Path, default=home / "insight-engine")
    parser.add_argument("--as-of", default="2026-09-30")
    parser.add_argument("--database", type=Path, default=root / "artifacts/core.sqlite3")
    parser.add_argument("--output", type=Path, default=root / "artifacts/latest-run.json")
    parser.add_argument("--reader", choices=("lexicon", "mlx"), default="lexicon")
    parser.add_argument("--model-path", type=Path, default=root / "models/qwen3-4b")
    parser.add_argument("--evaluate", action="store_true", help="Load synthetic truth separately after inference")
    parser.add_argument("--source-coverage", type=Path, help="Optional scoped source-coverage declarations; never inferred from empty feeds")
    parser.add_argument("--report", type=Path, help="Write the Agere evidence-review HTML product view")
    args = parser.parse_args()
    try:
        if args.world:
            from .corpus import load_world
            batch = load_world(args.world, args.as_of)
        else:
            batch = load_sample(args.sample, args.as_of)
        bridge = InsightBridge(args.engine_root)
        reader = None
        if args.reader == "mlx":
            from .mlx_reader import MLXGenerator, ModelReader
            generator = MLXGenerator(args.model_path)
            reader = ModelReader(bridge.reader, generator,
                                 {"manifest": generator.manifest, "versions": generator.versions,
                                  "engine_reader_revision": bridge.version},
                                 root / "artifacts/extraction-cache")
        runtime = Runtime(args.database, bridge)
        try:
            coverage = json.loads(args.source_coverage.read_text()) if args.source_coverage else None
            result = runtime.run(batch, reader=reader, spec=reader.spec if reader else None, source_coverage=coverage)
        finally:
            runtime.close()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2))
        if args.report:
            from .product_view import write_report
            write_report(result, args.report)
        summary = {"output": str(args.output), "patients": result["coverage"]["evaluated"],
                   "reader": result["request"]["reader"]["name"],
                   "cache_hit": result["telemetry"]["cache_hit"], "synthetic": True}
        summary["agere_states"] = result["agere"]["states"]
        if args.report:
            summary["report"] = str(args.report)
        if args.evaluate:
            if args.world:
                from .world_evaluation import evaluate_world
                evaluation = evaluate_world(result["patient_results"], result["insight_cards"], args.world, args.as_of)
            else:
                from .evaluate import evaluate_sample
                evaluation = evaluate_sample(result, args.sample)
            target = args.output.with_name(args.output.stem + "-evaluation.json")
            target.write_text(json.dumps(evaluation, indent=2))
            summary["evaluation"] = str(target)
        print(json.dumps(summary, indent=2))
    except (ValueError, KeyError, FileNotFoundError) as exc:
        parser.exit(2, f"Core run failed: {exc}\n")


if __name__ == "__main__":
    main()
