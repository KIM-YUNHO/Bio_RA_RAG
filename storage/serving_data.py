"""The data files the web services need to serve a release, computed from the release's own manifests.

Serving a release (`rag_service`, `review_viewer`) opens: the release folder and its component folders (run + candidate),
every document's source PDF and rendered PDF named in the extraction manifests, the guideline snapshot with its sources,
and the catalog. This lists exactly those files (this project's copies), their size, and can write them to a zip that
keeps the relative paths, so a teammate unpacks it at the project root.

Usage:
  uv run python -m storage.serving_data                       # pilot + latest: folders and sizes
  uv run python -m storage.serving_data --release latest --list
  uv run python -m storage.serving_data --zip D:\\share\\biosim_data.zip
"""
import argparse
import json
import sys
import zipfile
from pathlib import Path

from rag.portable_paths import ROOT, resolve

VERSIONS = json.loads((ROOT / "data/versions/versions.json").read_text(encoding="utf-8"))
RELEASE_DIRS = {"pilot": VERSIONS["roles"]["pilot"], "latest": VERSIONS["roles"]["latest"]}
GUIDELINES = "data/guidelines/ra_guidelines_native_2026-10-12_v2"   # scripts/start_ra_service.ps1
ALWAYS = ["data/catalog", "data/reviewed", "data/summary.json", "data/versions/versions.json", GUIDELINES]


def tree(path):
    path = Path(path)
    return [p for p in path.rglob("*") if p.is_file()] if path.is_dir() else [path] if path.is_file() else []


def release_files(role):
    version = next(v for v in VERSIONS["versions"] if v["id"] == RELEASE_DIRS[role])
    folder = ROOT / version["embedded_path"]
    files = set(tree(folder))
    manifest = json.loads((folder / "release_manifest.json").read_text(encoding="utf-8"))
    files |= set(tree(resolve(manifest["candidate"])))
    for item in manifest["components"]:
        run, candidate = resolve(item["run"]), resolve(item["candidate"])
        files |= set(tree(run)) | set(tree(candidate))
        extraction = json.loads((run / "extraction_manifest.json").read_text(encoding="utf-8"))
        for doc in extraction["documents"]:
            for key in ("raw_path", "rendered_path"):
                if doc.get(key):
                    files |= set(tree(resolve(doc[key])))
    return files


def guideline_files():
    files = set(tree(ROOT / GUIDELINES))
    manifest = json.loads((ROOT / GUIDELINES / "manifest.json").read_text(encoding="utf-8"))
    for source in manifest["sources"]:
        for key in ("raw_path", "parsed_path"):
            files |= set(tree(resolve(source[key])))
    return files


def collect(roles):
    files = guideline_files()
    for entry in ALWAYS:
        files |= set(tree(ROOT / entry))
    for role in roles:
        files |= release_files(role)
    outside = [p for p in files if not p.is_relative_to(ROOT)]
    if outside:
        raise SystemExit(f"{len(outside)} files are outside the project (not copied from the original yet): {outside[:3]}")
    return sorted(files)


def group(path):
    parts = path.relative_to(ROOT).parts
    if parts[:2] == ("data", "versions") and len(parts) > 4:
        return "/".join(parts[:4])
    return "/".join(parts[:2]) if len(parts) > 1 else parts[0]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--release", choices=["pilot", "latest"], action="append", help="default: both")
    ap.add_argument("--list", action="store_true", help="print every file")
    ap.add_argument("--zip", type=Path, help="write the files to this zip (relative paths kept)")
    args = ap.parse_args(argv)
    files = collect(args.release or ["pilot", "latest"])
    sizes = {}
    for p in files:
        g = group(p)
        n, s = sizes.get(g, (0, 0))
        sizes[g] = (n + 1, s + p.stat().st_size)
    for g, (n, s) in sorted(sizes.items()):
        print(f"{s / 1e6:9.1f} MB  {n:6d} files  {g}")
    print(f"{sum(s for _, s in sizes.values()) / 1e6:9.1f} MB  {len(files):6d} files  TOTAL")
    if args.list:
        for p in files:
            print(p.relative_to(ROOT).as_posix())
    if args.zip:
        with zipfile.ZipFile(args.zip, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for p in files:
                z.write(p, p.relative_to(ROOT).as_posix())
        print(f"wrote {args.zip} ({args.zip.stat().st_size / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
