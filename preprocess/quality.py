"""Reproducible source-bound visual pilot; inference uses the normal upload pipeline."""
import argparse
import json
from pathlib import Path
import shutil
import time

import fitz
from PIL import Image, ImageDraw

from .hybrid import run_hybrid, read_jsonl
from .hybrid_config import load_config
from .ingest import from_catalog, stable_hash
from .inventory import write_json


def load_sample(path, root):
    sample = json.loads(Path(path).read_text(encoding="utf-8"))
    specs = sample["documents"]
    docs = from_catalog(root / "data/catalog/documents.jsonl", root, [s["document_id"] for s in specs])
    lookup = {d.document_id: d for d in docs}
    if set(lookup) != {s["document_id"] for s in specs} or len(lookup) != len(specs):
        raise ValueError("Sample document identity mismatch")
    for spec in specs:
        doc = lookup[spec["document_id"]]
        if spec.get("version_id") and spec["version_id"] != doc.version_id:
            raise ValueError("Source version differs from frozen quality sample")
        spec["version_id"] = doc.version_id
        spec["raw_path"] = doc.raw_path
    return sample, docs


def render_sample(sample, out):
    out.mkdir(parents=True, exist_ok=True)
    previews = []
    for spec in sample["documents"]:
        with fitz.open(spec["raw_path"]) as pdf:
            for number, role in zip(spec["pages"], spec["roles"]):
                name = f"{stable_hash(spec['document_id'])[:10]}_p{number:03d}.png"
                path = out / "reference_pages" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                pix = pdf[number - 1].get_pixmap(dpi=160, alpha=False)
                path.write_bytes(pix.tobytes("png"))
                with Image.open(path) as img:
                    img.thumbnail((650, 880))
                    previews.append((img.copy(), f"{spec['label']} p{number}: {role}"))
    for offset in range(0, len(previews), 4):
        sheet = Image.new("RGB", (1360, 1870), "#e9e9e9")
        draw = ImageDraw.Draw(sheet)
        for i, (img, label) in enumerate(previews[offset:offset+4]):
            x, y = (i % 2) * 680 + 10, (i // 2) * 930 + 30
            draw.text((x, y - 22), label, fill="black")
            sheet.paste(img, (x, y))
        sheet.save(out / f"contact_sheet_{offset // 4 + 1}.png")
    write_json(out / "sample_manifest.json", sample)
    return {"reference_pages": len(previews), "output": str(out)}


def main(argv=None):
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Run/render a fixed RA visual quality pilot")
    parser.add_argument("command", choices=["render", "run"])
    parser.add_argument("--sample", type=Path, default=root / "configs/preprocess.quality.sample.json")
    parser.add_argument("--config", type=Path, default=root / "configs/preprocess.local.json")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--no-qwen", action="store_true")
    parser.add_argument("--max-qwen-calls", type=int)
    parser.add_argument("--seed-cache", type=Path, help="Copy source-bound Paddle caches only; every cache is verified again by the pipeline")
    args = parser.parse_args(argv)
    sample, documents = load_sample(args.sample, root)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "sample_manifest.json", sample)
    if args.command == "render":
        result = render_sample(sample, out)
    else:
        if args.seed_cache:
            destination = out / "paddle_raw"
            destination.mkdir(exist_ok=True)
            for path in (args.seed_cache / "paddle_raw").glob("*.json"):
                if not (destination / path.name).exists():
                    shutil.copy2(path, destination / path.name)
        result = run_hybrid(documents, out, load_config(args.config), use_qwen=not args.no_qwen,
            max_qwen_calls=args.max_qwen_calls,
            pages_by_document={s["document_id"]:s["pages"] for s in sample["documents"]})
        # Preserve first-run metrics and retain subsequent executions separately.
        execution = out / "quality_execution.json"
        if execution.exists():
            execution = out / "execution_history" / f"{time.time_ns()}.json"
        write_json(execution, result)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
