"""Simple source-page view for cited evidence: page image, highlight boxes, region crop.

Documents resolve only through the active release bundle and the guideline
snapshot, both hash-checked when the service starts. Clients send a stored job
ID, result index and evidence ID, never a file path.
"""
from functools import lru_cache
from pathlib import Path
import re
from threading import Lock

from rag.portable_paths import resolve

PAGE_WIDTH = 1100
CROP_ZOOM = 2.0
MAX_QUOTE_SEGMENTS = 8
_render_lock = Lock()


def _rotated(page, rect):
    import fitz
    return fitz.Rect(rect) * page.rotation_matrix


@lru_cache(maxsize=96)
def _render_page(path, page_number, width):
    import fitz
    with _render_lock, fitz.open(path) as pdf:
        if not 1 <= page_number <= pdf.page_count: raise KeyError(page_number)
        page = pdf[page_number-1]
        zoom = width/page.rect.width
        return page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False).tobytes('png')


@lru_cache(maxsize=64)
def _render_clip(path, page_number, clip):
    import fitz
    with _render_lock, fitz.open(path) as pdf:
        if not 1 <= page_number <= pdf.page_count: raise KeyError(page_number)
        page = pdf[page_number-1]
        return page.get_pixmap(matrix=fitz.Matrix(CROP_ZOOM, CROP_ZOOM), clip=fitz.Rect(clip), alpha=False).tobytes('png')


def quote_segments(quotes):
    """Line-sized pieces of the cited quotes that a PDF text search can match."""
    segments = []
    for quote in quotes:
        for line in re.split(r'\n+|(?<=[.;:])\s+', quote or ''):
            line = re.sub(r'\s+', ' ', line.replace('...', ' ')).strip()
            if len(line) >= 12 and '<' not in line and line not in segments:
                segments.append(line[:80])
    return segments[:MAX_QUOTE_SEGMENTS]


def evidence_quotes(result, eid):
    quotes = []
    for claim in result.get('claims') or []:
        quotes += [s.get('quote') for s in claim.get('sources') or [] if s.get('evidence') == eid]
    for item in result.get('items') or []:
        if eid in (item.get('evidence') or []): quotes.append(item.get('quote'))
        quotes += [c.get('quote') for c in item.get('conditions') or [] if c.get('evidence') == eid]
    return [q for q in dict.fromkeys(quotes) if isinstance(q, str) and q.strip()]


class EvidencePages:
    def __init__(self, pipeline):
        self.paths = {}
        bundle = getattr(pipeline, 'bundle', None)
        for doc in (getattr(bundle, 'docs', None) or {}).values():
            self.paths[(doc['document_id'], doc['version_id'])] = str(Path(doc.get('rendered_path') or doc['raw_path']))
        guidelines = getattr(pipeline, 'guidelines', None)
        if guidelines is not None:
            raw = {(s['document_id'], s['raw_sha256']): s['raw_path'] for s in guidelines.manifest['sources']}
            for chunk in guidelines.chunks:
                identity = (chunk['document_id'], chunk['version_id'])
                if identity in raw: self.paths[identity] = str(resolve(raw[identity]))

    @staticmethod
    def lookup(job, result_index, eid):
        if job.get('state') != 'completed': raise KeyError(eid)
        results = (job.get('report') or {}).get('results') or []
        if not 0 <= result_index < len(results): raise KeyError(result_index)
        evidence = (results[result_index].get('evidence') or {}).get(eid)
        if not isinstance(evidence, dict): raise KeyError(eid)
        return results[result_index], evidence

    def path(self, evidence):
        identity = (evidence.get('document_id'), evidence.get('version_id'))
        if identity not in self.paths: raise KeyError('Unregistered document version')
        return self.paths[identity]

    def page_count(self, path):
        import fitz
        with _render_lock, fitz.open(path) as pdf: return pdf.page_count

    def detail(self, job, result_index, eid, page=None):
        result, evidence = self.lookup(job, result_index, eid)
        path = self.path(evidence)
        total = self.page_count(path)
        cited = evidence.get('pdf_page')
        page = cited if page is None else page
        if not isinstance(page, int) or not 1 <= page <= total: raise ValueError('페이지 범위를 확인하세요.')
        method, boxes = self.highlights(path, page, evidence, evidence_quotes(result, eid))
        meta = evidence.get('document_metadata') or {}
        return {'eid': eid, 'result_index': result_index, 'title': evidence.get('title'),
            'source': evidence.get('source'), 'jurisdiction': evidence.get('jurisdiction'),
            'product_id': evidence.get('product_id'), 'kind': (evidence.get('structure') or {}).get('kind'),
            'cited_page': cited, 'page': page, 'page_count': total,
            'pdf_page_label': evidence.get('pdf_page_label') if page == cited else None,
            'document_status': meta.get('registered_status') or meta.get('status'),
            'status_checked': meta.get('status_checked_on') or meta.get('checked'),
            'source_url': evidence.get('source_url'), 'review_links': evidence.get('review_links') or [],
            'highlight_method': method, 'boxes': boxes,
            'has_crop': bool(self.crop_rect(path, evidence))}

    def highlights(self, path, page_number, evidence, quotes):
        """Normalized [x0,y0,x1,y1] boxes: stored region boxes first, else quote text search."""
        import fitz
        with _render_lock, fitz.open(path) as pdf:
            page = pdf[page_number-1]
            width, height = page.rect.width, page.rect.height
            rects = [_rotated(page, r['bbox_pdf']) for r in evidence.get('source_refs') or []
                     if r.get('pdf_page') == page_number and isinstance(r.get('bbox_pdf'), list) and len(r['bbox_pdf']) == 4]
            method = 'region' if rects else None
            if not rects and page_number == evidence.get('pdf_page'):
                for segment in quote_segments(quotes):
                    rects += [_rotated(page, r) for r in page.search_for(segment)]
                method = 'quote' if rects else 'none'
        boxes = []
        for r in rects:
            box = [round(min(max(v, 0.0), 1.0), 4) for v in (r.x0/width, r.y0/height, r.x1/width, r.y1/height)]
            if box[2] > box[0] and box[3] > box[1] and box not in boxes: boxes.append(box)
        return method or 'none', boxes

    def crop_rect(self, path, evidence):
        refs = [r['bbox_pdf'] for r in evidence.get('source_refs') or []
                if r.get('pdf_page') == evidence.get('pdf_page') and isinstance(r.get('bbox_pdf'), list) and len(r['bbox_pdf']) == 4]
        if not refs: return None
        pad = 6
        return (min(r[0] for r in refs)-pad, min(r[1] for r in refs)-pad, max(r[2] for r in refs)+pad, max(r[3] for r in refs)+pad)

    def page_png(self, job, result_index, eid, page):
        _, evidence = self.lookup(job, result_index, eid)
        return _render_page(self.path(evidence), page, PAGE_WIDTH)

    def crop_png(self, job, result_index, eid):
        _, evidence = self.lookup(job, result_index, eid)
        path = self.path(evidence)
        rect = self.crop_rect(path, evidence)
        if rect is None: raise KeyError('No stored region')
        return _render_clip(path, evidence['pdf_page'], rect)

    def document_pdf(self, job, result_index, eid):
        _, evidence = self.lookup(job, result_index, eid)
        path = Path(self.path(evidence))
        if path.suffix.lower() != '.pdf': raise KeyError('Not a PDF source')
        return path.read_bytes()
