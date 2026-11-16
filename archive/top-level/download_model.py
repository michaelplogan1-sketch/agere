"""Download public weights only; no patient files or account tokens are used."""
import hashlib
import json
import os
from pathlib import Path

os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
from huggingface_hub import HfApi, snapshot_download


def main():
    repo = "mlx-community/Qwen3-4B-Instruct-2507-4bit"
    root = Path(__file__).resolve().parent / "models" / "qwen3-4b"
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "manifest.json"
    previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else None
    revision = previous["revision"] if previous else HfApi(token=False).model_info(repo).sha
    print(json.dumps({"repo": repo, "revision": revision}), flush=True)
    snapshot_download(repo, revision=revision, token=False, local_dir=root,
                      allow_patterns=["*.json", "*.safetensors", "*.txt", "*.jinja", "README.md", "LICENSE*"],
                      max_workers=4)
    hashes = {}
    for p in sorted(root.iterdir()):
        if not p.is_file() or p.name == "manifest.json":
            continue
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
                h.update(chunk)
        hashes[p.name] = {"sha256": h.hexdigest(), "bytes": p.stat().st_size}
    manifest = {"repository": repo, "revision": revision, "license": "apache-2.0",
                "purpose": "General instruction model; not healthcare fine-tuned or clinically validated",
                "files": hashes}
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"Verified local files: {manifest_path}", flush=True)


if __name__ == "__main__":
    main()
