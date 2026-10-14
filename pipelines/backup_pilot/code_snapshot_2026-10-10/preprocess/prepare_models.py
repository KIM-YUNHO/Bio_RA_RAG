"""Explicit deployment preparation; never imported by the offline inference worker."""
import argparse
import json
from pathlib import Path

from .paddle_adapter import model_fingerprint

MODELS = {
    "PP-DocLayoutV3_safetensors": {
        "repo": "PaddlePaddle/PP-DocLayoutV3_safetensors",
        "revision": "e4f489dc536556fc1be02b973ba06756e0d4a2ac"},
    "PaddleOCR-VL-1.6": {
        "repo": "PaddlePaddle/PaddleOCR-VL-1.6",
        "revision": "c5630abae1d940eafe0697512a0325494b02ab42"},
}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Prepare pinned official model snapshots before offline deployment")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1] / "models/paddle")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--verify-only", action="store_true", help="Check a transferred model manifest without network")
    mode.add_argument("--record-existing", action="store_true", help="Record already downloaded pinned snapshots without network")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    manifest_path = root / "model_manifest.json"
    if args.verify_only:
        saved = json.loads(manifest_path.read_text(encoding="utf-8"))
        if set(saved["models"]) != set(MODELS):
            raise ValueError("Unexpected model manifest entries")
        for name in MODELS:
            if saved["models"][name]["fingerprint"] != model_fingerprint(root / name):
                raise ValueError(f"Transferred model checksum mismatch: {name}")
        print("Local model manifest verified; no network used.")
        return 0
    if not args.record_existing:
        from huggingface_hub import snapshot_download
        for name, spec in MODELS.items():
            snapshot_download(repo_id=spec["repo"], revision=spec["revision"], local_dir=root / name,
                allow_patterns=["*.json", "*.safetensors", "*.bin", "*.model", "*.yml", "*.yaml",
                                "*.py", "*.jinja", "*.txt", "LICENSE*", "README.md"])
    models = {}
    for name, spec in MODELS.items():
        if args.record_existing:
            metadata = root / name / ".cache/huggingface/download/config.json.metadata"
            if metadata.read_text(encoding="utf-8").splitlines()[0] != spec["revision"]:
                raise ValueError(f"Downloaded snapshot revision differs: {name}")
        models[name] = {**spec, "fingerprint": model_fingerprint(root / name)}
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = manifest_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps({"models": models}, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(manifest_path)
    print(f"Pinned model hashes saved: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
