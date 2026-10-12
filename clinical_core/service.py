"""Read-only loopback API for the computed synthetic corpus and question registry."""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from .questions import catalog


class CorpusService:
    def __init__(self, root, agere_run=None, reasoning_run=None, stack_run=None):
        self.root = root
        self.summary = json.loads((root / "summary.json").read_text())
        self.worlds = {w["world"] for w in self.summary["worlds"]}
        self.agere_run = agere_run
        self.reasoning_run = reasoning_run
        self.stack_run = stack_run

    def get(self, path, query):
        if path == "/health":
            return {"status": "ok", "synthetic": True, "hosted_inference_enabled": False}
        if path == "/questions":
            return catalog()
        if path == "/stack":
            if self.stack_run is None or not self.stack_run.is_file():
                raise ValueError("No integrated stack run configured")
            result = json.loads(self.stack_run.read_text())
            if result.get("schema_version") != "agere-integrated-stack/1" or result.get("synthetic") is not True:
                raise ValueError("Unsupported integrated stack artifact")
            return result
        if path == "/reasoning":
            if self.reasoning_run is None or not self.reasoning_run.is_file():
                raise ValueError("No conditional-reasoning run configured")
            result = json.loads(self.reasoning_run.read_text())
            if result.get("schema_version") != "agere-conditional-decisions/1" or result.get("synthetic") is not True:
                raise ValueError("Unsupported conditional-reasoning artifact")
            return result
        if path == "/agere":
            if self.agere_run is None or not self.agere_run.is_file():
                raise ValueError("No Agere run configured; generate one with clinical_core first")
            run = json.loads(self.agere_run.read_text())
            if "agere" not in run:
                raise ValueError("Saved run predates the Agere evidence layer")
            return {"run_id": run["run_id"], "as_of": run["request"]["as_of"], **run["agere"]}
        if path == "/worlds":
            return self.summary
        if path.startswith("/answers/"):
            question = path.removeprefix("/answers/")
            world = query.get("world", ["hard16_llm"])[0]
            if world not in self.worlds:
                raise ValueError("Unknown synthetic world")
            answers = json.loads((self.root / world / "answers.json").read_text())
            if question not in answers:
                raise ValueError("Unknown question")
            return answers[question]
        raise ValueError("Unknown endpoint")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8876)
    parser.add_argument("--corpus", type=Path, default=Path(__file__).resolve().parents[1] / "artifacts/corpus")
    parser.add_argument("--agere-run", type=Path, default=Path(__file__).resolve().parents[1] / "artifacts/agere/sample-run.json")
    parser.add_argument("--reasoning-run", type=Path, default=Path(__file__).resolve().parents[1] / "artifacts/agere-reasoning/sample.json")
    parser.add_argument("--stack-run", type=Path, default=Path(__file__).resolve().parents[1] / "artifacts/agere-stack/run.json")
    args = parser.parse_args()
    service = CorpusService(args.corpus, args.agere_run, args.reasoning_run, args.stack_run)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parts = urlparse(self.path)
            try:
                value, status = service.get(parts.path, parse_qs(parts.query)), 200
            except ValueError as exc:
                value, status = {"error": str(exc)}, 404
            data = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Synthetic read-only API: http://127.0.0.1:{args.port}/worlds", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
