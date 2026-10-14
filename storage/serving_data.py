"""The data files the web services need to serve a release, computed from the release's own manifests.

Serving a release (`rag_service`, `review_viewer`) opens: the release folder and its component folders (run + candidate),
every document's source PDF and rendered PDF named in the extraction manifests, the guideline snapshot with its sources,
and the catalog. This lists exactly those files (this project's copies), their size, and can write them to a zip that
keeps the relative paths, so a teammate unpacks it at the project root.

Usage:
  uv run python -m storage.serving_data                       # pilot + latest: folders and sizes
  uv run python -m storage.serving_data --release latest --list
  uv run python -m storage.serving_data --check               # referenced files that are missing here (exit 1 if any)
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


def take(path, missing):
    """Files under `path`; a path that does not exist is recorded in `missing` (a list)."""
    path = Path(path)
    if not path.exists():
        missing.append(path)
    return set(tree(path))


def read_json(path, missing):
    if not Path(path).is_file():
        missing.append(Path(path))
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def release_files(role, missing):
    version = next(v for v in VERSIONS["versions"] if v["id"] == RELEASE_DIRS[role])
    folder = ROOT / version["embedded_path"]
    files = take(folder, missing)
    manifest = read_json(folder / "release_manifest.json", missing)
    if manifest is None:
        return files
    files |= take(resolve(manifest["candidate"]), missing)
    for item in manifest["components"]:
        run, candidate = resolve(item["run"]), resolve(item["candidate"])
        files |= take(run, missing) | take(candidate, missing)
        extraction = read_json(run / "extraction_manifest.json", missing)
        for doc in (extraction or {}).get("documents", []):
            for key in ("raw_path", "rendered_path"):
                if doc.get(key):
                    files |= take(resolve(doc[key]), missing)
    return files


def guideline_files(missing):
    files = take(ROOT / GUIDELINES, missing)
    manifest = read_json(ROOT / GUIDELINES / "manifest.json", missing)
    for source in (manifest or {}).get("sources", []):
        for key in ("raw_path", "parsed_path"):
            files |= take(resolve(source[key]), missing)
    return files


def collect(roles, missing=None):
    """Sorted files needed to serve `roles`. Referenced paths that do not exist are appended to `missing` when given."""
    missing = [] if missing is None else missing
    files = guideline_files(missing)
    for entry in ALWAYS:
        files |= take(ROOT / entry, missing)
    for role in roles:
        files |= release_files(role, missing)
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
    ap.add_argument("--check", action="store_true", help="only report referenced files that are missing here (exit 1 if any)")
    args = ap.parse_args(argv)
    missing = []
    files = collect(args.release or ["pilot", "latest"], missing)
    if args.check:
        for path in missing:
            print("MISSING", path)
        print(f"{len(files)} files present, {len(missing)} missing")
        return 1 if missing else 0
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
