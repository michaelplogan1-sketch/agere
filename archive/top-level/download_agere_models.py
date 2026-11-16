"""Install public encoder and reranker candidates at immutable revisions.

Downloads data/weights only, never remote Python. No inference API calls.
"""
import hashlib
import json
from pathlib import Path

from huggingface_hub import snapshot_download

ROOT = Path(__file__).resolve().parent
MODELS = {
    "bioclinical-modernbert": {
        "repo": "thomas-sounack/BioClinical-ModernBERT-base",
        "revision": "c3648aa87af95837c809e6f0c5f85d08160db437",
        "license_declared_by_author": "MIT", "role": "clinical representation; needs task adaptation"},
    "granite-english-r2": {
        "repo": "ibm-granite/granite-embedding-english-r2",
        "revision": "47ea694b257b703fee9253d75c2b1f2985180498",
        "license_declared_by_author": "Apache-2.0", "role": "dense evidence retrieval"},
    "bge-reranker-v2-m3": {
        "repo": "BAAI/bge-reranker-v2-m3",
        "revision": "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e",
        "license_declared_by_author": "Apache-2.0", "role": "query-passage cross-encoder relevance"},
}


def main():
    for name, spec in MODELS.items():
        target = ROOT / "models" / name
        snapshot_download(spec["repo"], revision=spec["revision"], local_dir=target,
                          token=False, max_workers=2,
                          allow_patterns=["*.json", "*.safetensors", "*.model", "README.md", "LICENSE*", "NOTICE*"])
        files = {}
        for path in sorted(target.rglob("*")):
            if not path.is_file() or ".cache" in path.relative_to(target).parts:
                continue
            if path.name == "agere-model.json":
                continue
            h = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    h.update(chunk)
            files[str(path.relative_to(target))] = {"bytes": path.stat().st_size, "sha256": h.hexdigest()}
        manifest = {**spec, "name": name, "files": files, "trust_remote_code": False,
                    "clinical_task_head_trained": False, "automatic_clinical_action": False}
        (target / "agere-model.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print(json.dumps({"installed": name, "revision": spec["revision"],
                          "bytes": sum(f["bytes"] for f in files.values())}), flush=True)


if __name__ == "__main__":
    main()
