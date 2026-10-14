"""Copy the files the web services open but the first migration left behind, from the original project (read only).

The original project's absolute paths in manifests are translated to this project's copies at read time
(rag/portable_paths.py). That only works when the copies exist. Three groups were not copied
(docs/project/SHARED_BASELINE_MIGRATION_2026-10-14.md, section 4); this puts them where `portable_paths` looks:

1. the pilot release's 89 component candidate folders (`rag.release.ReleaseBundle` hash-checks each; 360 MB);
2. the rendered / font-verified PDFs named by `rendered_path` in the pilot's extraction manifests (page images of
   those documents are drawn from them; sha256 is in `rendered_sha256`);
3. the source and parse file of guideline documents whose manifest path is outside data/raw and data/parsed
   (sha256 in the guideline manifest).

Every file is hash-checked after the copy. The original project is never changed. Safe to run again.

Usage: uv run python -m storage.copy_serving_files [--dry-run]
"""
import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

from rag.portable_paths import ROOT, mapped, relative_in_original

PILOT = ROOT / "data/versions/01_pilot_baseline_2026-10-10"
RELEASES = [PILOT / "embedded/rag_finalization_2026-10-10_v2/release_manifest.json"]
EXTRACTION_ROOTS = [PILOT / "preprocessed", ROOT / "data/versions/03_pipeline_h_v2_table_recovery_2026-10-14/preprocessed",
                    ROOT / "data/versions/02_pipeline_h_2026-10-14/preprocessed"]
GUIDELINES = ROOT / "data/guidelines"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def plan():
    """[(source Path in the original project, target Path here, expected sha256 or None, group)]."""
    items = []
    for manifest in RELEASES:
        for item in json.loads(manifest.read_text(encoding="utf-8"))["components"]:
            source = Path(item["candidate"])
            for path in sorted(p for p in source.rglob("*") if p.is_file()):
                rel = relative_in_original(path)
                name = path.relative_to(source).as_posix()
                items.append((path, mapped(rel), item["hashes"].get(name), "component candidates"))
    for base in EXTRACTION_ROOTS:
        for manifest in base.glob("**/extraction_manifest.json"):
            for doc in json.loads(manifest.read_text(encoding="utf-8"))["documents"]:
                rendered = doc.get("rendered_path")
                if rendered and relative_in_original(rendered) is not None:
                    items.append((Path(rendered), mapped(relative_in_original(rendered)), doc.get("rendered_sha256"), "rendered PDFs"))
    for manifest in GUIDELINES.glob("*/manifest.json"):
        for src in json.loads(manifest.read_text(encoding="utf-8")).get("sources", []):
            for key, digest in (("raw_path", "raw_sha256"), ("parsed_path", "parsed_sha256")):
                rel = relative_in_original(src[key])
                if rel is not None:
                    items.append((Path(src[key]), mapped(rel), src[digest], "guideline sources"))
    unique = {}
    for source, target, expected, group in items:
        unique.setdefault(target, (source, target, expected, group))
    return list(unique.values())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    summary, problems = {}, []
    for source, target, expected, group in plan():
        entry = summary.setdefault(group, {"files": 0, "copied": 0, "missing_in_original": 0, "bytes": 0})
        entry["files"] += 1
        if target.is_file() and (expected is None or sha256(target) == expected):
            entry["bytes"] += target.stat().st_size
            continue
        if not source.is_file():
            entry["missing_in_original"] += 1
            problems.append(f"missing in original and here: {source}")
            continue
        if args.dry_run:
            entry["copied"] += 1
            entry["bytes"] += source.stat().st_size
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if expected is not None and sha256(target) != expected:
            problems.append(f"hash mismatch after copy: {target}")
            continue
        entry["copied"] += 1
        entry["bytes"] += target.stat().st_size
    for group, e in summary.items():
        print(f"{group}: {e['files']} files, copied {e['copied']}, missing in original {e['missing_in_original']}, {e['bytes'] / 1e6:.1f} MB"
              + (" (dry run)" if args.dry_run else ""))
    for p in problems:
        print("  PROBLEM:", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
