"""Normalize local uploads and an existing catalog, without network access."""
from dataclasses import dataclass, asdict
import hashlib
import json
from pathlib import Path
import shutil


BASE_METADATA_FIELDS = ('title', 'product_id', 'source', 'role', 'document_type', 'language', 'url',
                        'final_url', 'source_metadata', 'doc_date')
INTAKE_METADATA_FIELDS = ('ingredient', 'product_kind', 'family', 'author', 'tier', 'default_search',
    'dataset_version', 'evaluation_cohort', 'evaluation_family_id', 'exposure_status',
    'model_execution_allowed', 'baseline_status')


def catalog_metadata(row):
    return {**{key: row.get(key) for key in BASE_METADATA_FIELDS}, 'doc_date': document_date(row),
            **{key: row[key] for key in INTAKE_METADATA_FIELDS if key in row}}


def document_date(row):
    """Use an explicit source date, never download time or a date guessed from a URL."""
    from datetime import date
    meta = row.get('source_metadata') or {}
    value = (row.get('doc_date') or (meta.get('application_doc') or {}).get('ApplicationDocsDate')
             or meta.get('last_updated_date') or meta.get('first_published_date') or meta.get('date'))
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return None


def refresh_catalog_metadata(documents, catalog):
    """Explicit descriptive refresh for a derived run; retain frozen intake policies."""
    rows = {}
    for line in Path(catalog).read_text(encoding='utf-8-sig').split('\n'):
        if not line.strip():
            continue
        row = json.loads(line)
        key = row['document_id'], row['version_id']
        if key in rows:
            raise ValueError(f'Duplicate catalog version: {key}')
        rows[key] = row
    for doc in documents:
        key = doc.document_id, doc.version_id
        if key not in rows:
            raise ValueError(f'Exact source version missing from metadata catalog: {key}')
        row = rows[key]
        if row.get('sha256', doc.version_id) != doc.expected_sha256:
            raise ValueError(f'Metadata catalog source hash differs: {key}')
        for field in ('product_id', 'source', 'role', 'document_type'):
            if doc.metadata.get(field) and row.get(field) != doc.metadata[field]:
                raise ValueError(f'Metadata identity changed: {key}: {field}')
        fresh = catalog_metadata(row)
        doc.metadata.update({field: fresh[field] for field in BASE_METADATA_FIELDS
                             if fresh.get(field) is not None})


def assert_model_execution_allowed(documents):
    """Respect intake decisions before creating status files or starting a model."""
    denied = []
    for doc in documents:
        allowed = doc.metadata.get('model_execution_allowed')
        if 'model_execution_allowed' in doc.metadata and type(allowed) is not bool:
            raise ValueError(f'Invalid model execution policy: {doc.document_id}')
        if allowed is False:
            denied.append(doc.document_id)
    if denied:
        raise ValueError(f'Model execution denied by dataset policy: {sorted(denied)}')


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def asset_identity(document_id, version_id, page_id, revision, source, kind, bbox_status):
    # File relocation is provenance, not a change to the source region identity.
    identity = {"document_id": document_id, "version_id": version_id, "page_id": page_id,
                "revision": revision, "locator": {k: v for k, v in source.items() if k != "path"},
                "type": kind, "bbox_status": bbox_status}
    return "a" + stable_hash(identity)[:28]


@dataclass
class Document:
    document_id: str
    version_id: str
    kind: str
    raw_path: str
    text_path: str
    source_paths: list
    metadata: dict
    expected_sha256: str | None = None
    legacy_dir: str | None = None
    rendered_path: str | None = None
    rendered_sha256: str | None = None
    native_font_repair: dict | None = None

    @property
    def pdf_path(self):
        return self.rendered_path or self.raw_path

    def source_ref(self, number, box):
        ref={'path':self.raw_path,'sha256':self.version_id,'pdf_page':number,'bbox_pdf':box}
        if self.rendered_path:
            ref.update(rendered_path=self.rendered_path,rendered_sha256=self.rendered_sha256,
                       locator_type='html_render_page' if self.kind=='html' else 'verified_cmap_pdf_page')
        return ref

    def to_dict(self):
        return asdict(self)


def from_catalog(path, root, document_ids=None):
    path, root = Path(path).resolve(), Path(root).resolve()
    selected = set(document_ids or [])
    docs, seen = [], set()
    for line in path.read_text(encoding="utf-8-sig").split('\n'):
        if not line.strip():
            continue
        row = json.loads(line)
        if selected and row["document_id"] not in selected:
            continue
        if row.get("kind") not in ("pdf", "html"):
            continue
        identity = (row["document_id"], row["version_id"])
        if identity in seen:
            raise ValueError(f"Duplicate document registration: {identity}")
        seen.add(identity)
        raw = (root / row["raw_path"]).resolve()
        text = (root / (row.get("text_path") or row["raw_path"])).resolve()
        import re
        slug = re.sub(r"[^a-zA-Z0-9_-]", "_", row["document_id"])
        docs.append(Document(row["document_id"], row["version_id"], row["kind"], str(raw), str(text), [str(raw)],
            catalog_metadata(row),
            row.get("sha256") or row["version_id"], str(root / "data/ocr" / slug)))
    missing = selected - {d.document_id for d in docs}
    if missing:
        raise ValueError(f"Requested documents not found: {sorted(missing)}")
    return docs


def from_inputs(paths, out):
    out = Path(out).resolve()
    registered = {}
    for supplied in paths:
        supplied = Path(supplied).resolve()
        if not supplied.exists():
            raise FileNotFoundError(supplied)
        files = sorted(supplied.rglob("*")) if supplied.is_dir() else [supplied]
        for path in files:
            if not path.is_file() or path.suffix.lower() not in (".pdf", ".html", ".htm"):
                continue
            if supplied.is_dir() and path.is_relative_to(out):
                continue
            digest = sha256(path)
            if digest in registered:
                if str(path) not in registered[digest].source_paths:
                    registered[digest].source_paths.append(str(path))
                continue
            kind = "pdf" if path.suffix.lower() == ".pdf" else "html"
            destination = out / "originals" / (digest + (".pdf" if kind == "pdf" else ".html"))
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                shutil.copyfile(path, destination)
            if sha256(destination) != digest:
                raise ValueError(f"Upload changed while snapshotting: {path}")
            registered[digest] = Document("local:" + digest[:24], digest, kind, str(destination), str(destination), [str(path)],
                {"title": path.name, "product_id": None, "source": None, "role": None, "document_type": None, "language": None, "url": None}, digest)
    if not registered:
        raise ValueError("No local PDF/HTML inputs found")
    return list(registered.values())


def assert_sources(documents):
    for doc in documents:
        if doc.kind not in ('pdf','html') or doc.kind=='html' and not (doc.rendered_path and doc.rendered_sha256):
            raise ValueError('HTML must have a registered local rendering before extraction')
        actual = sha256(doc.raw_path)
        if actual != doc.version_id or doc.expected_sha256 and actual != doc.expected_sha256:
            raise ValueError(f"Original checksum mismatch: {doc.document_id}")
        if doc.rendered_path and sha256(doc.rendered_path)!=doc.rendered_sha256:
            raise ValueError(f'HTML rendering checksum mismatch: {doc.document_id}')
        if doc.kind=='pdf' and doc.rendered_path:
            from preprocess.source.native_fonts import validate as validate_fonts
            validate_fonts(doc)
