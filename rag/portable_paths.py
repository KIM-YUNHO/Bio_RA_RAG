"""Resolve the original project's absolute paths to this project's copies, when a file is opened.

Release manifests, extraction manifests and chunk provenance carry absolute paths of the project the data was made in
(`C:\\Users\\Admin\\Documents\\biosimilar_collection\\...`). The files are hash-checked and must not be edited, so the
paths are translated at read time instead (docs/project/PGVECTOR_INTEGRATION_MAP_2026-10-14.md, section 3).

`resolve(value)` returns the path unchanged unless it lies under a `biosimilar_collection` folder. For those it returns
the copy under this project (rules below, from docs/project/migration_manifest_2026-10-14.json) when that exists, and
otherwise the original path. Set BIOSIM_PATHS=original to always keep the original path.
"""
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = "data/versions/"
PILOT = VERSIONS + "01_pilot_baseline_2026-10-10/"
H_RUN = VERSIONS + "02_pipeline_h_2026-10-14/"
LATEST = VERSIONS + "03_pipeline_h_v2_table_recovery_2026-10-14/"
H_V2_DEV = "data/runs/h_v2_rag_development_2026-10-14_luna_v1/native_candidate_v2/"

# (prefix relative to the original project root, replacement relative to this project). First match wins.
RULES = [
    ("data/rag/candidates/", PILOT + "embedded/components/"),
    ("data/rag/releases/rag_finalization_2026-10-10_v2", PILOT + "embedded/rag_finalization_2026-10-10_v2"),
    ("data/rag/supplemental/", "data/guidelines/"),
    ("data/rag/service_release.json", PILOT + "records/service_release.json"),
    ("data/preprocessed/", PILOT + "preprocessed/"),
    (H_V2_DEV + "extraction", LATEST + "preprocessed/extraction"),
    (H_V2_DEV + "candidate_final", LATEST + "embedded/candidate_final"),
    (H_V2_DEV + "release_final", LATEST + "embedded/release_final"),
    ("pipeline_h/runs/ustekinumab_20261014_v2/candidate", H_RUN + "embedded/candidate"),
    ("pipeline_h/runs/ustekinumab_20261014_v2/release", H_RUN + "embedded/release"),
    ("pipeline_h/runs/ustekinumab_20261014_v2/", H_RUN + "preprocessed/"),
    ("pipeline_h/", "pipelines/pipeline_h/"),
]
# `<anything>/biosimilar_collection/<rest>` in either slash style, with or without a drive.
ORIGINAL = re.compile(r"^(?:[A-Za-z]:)?[\\/].*?[\\/]biosimilar_collection[\\/](?P<rest>.*)$", re.I)


def relative_in_original(value):
    """Path relative to the original project root (forward slashes), or None when `value` is not under it."""
    match = ORIGINAL.match(str(value))
    return match["rest"].replace("\\", "/") if match else None


def mapped(rel):
    """This project's location for a path relative to the original root (it may not exist)."""
    folded = rel.casefold()
    for prefix, target in RULES:
        if folded.startswith(prefix.casefold()):
            return ROOT / (target + rel[len(prefix):])
    return ROOT / rel


def resolve(value):
    """`value` (str or Path) translated to this project's copy when it exists, else unchanged. Returns a Path."""
    path = Path(value)
    if os.environ.get("BIOSIM_PATHS", "project") == "original":
        return path
    rel = relative_in_original(value)
    if rel is None:
        return path
    copy = mapped(rel)
    return copy if copy.exists() else path
