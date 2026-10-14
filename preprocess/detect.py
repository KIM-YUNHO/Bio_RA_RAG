"""Candidate detection: no OCR, VLM, remote calls, or claims of completeness."""
import re
import fitz
from collection import text_layer

CAPTION = re.compile(r"^\s*(Table|Figure|Fig\.|Graph|Chart)\s+(?:[A-Z]{1,6}[.\-]?)?(?:\d[\w.\-]*|[IVX]+)\b", re.I)
TOC = re.compile(r"(?:\.{3,}|\u2026|\s{4,})\s*\d{1,4}\s*$")


def rectangle(value, bounds):
    rect = fitz.Rect(value) & fitz.Rect(bounds)
    if rect.is_empty or rect.is_infinite:
        return None
    return [round(v, 3) for v in rect]


def overlap(a, b):
    intersection = fitz.Rect(a) & fitz.Rect(b)
    if intersection.is_empty:
        return 0.0
    union = fitz.Rect(a).get_area() + fitz.Rect(b).get_area() - intersection.get_area()
    return intersection.get_area() / union if union else 0.0


def near_caption(box, captions):
    choices = []
    x0, y0, x1, y1 = box
    for cap in captions:
        a, b, c, d = cap["bbox_pdf"]
        shared_x = min(x1, c) - max(x0, a)
        if shared_x <= 0:
            continue
        gap = y0 - d if d <= y0 + 3 else b - y1 if b >= y1 - 3 else None
        if gap is not None and -3 <= gap <= 100:
            choices.append((max(0, gap), cap))
    return [min(choices, key=lambda item: item[0])[1]] if choices else []


def merge_candidates(candidates):
    merged = []
    for candidate in candidates:
        same = next((old for old in merged if old["asset_type"] == candidate["asset_type"]
                     and old["bbox_status"] == candidate["bbox_status"]
                     and overlap(old["bbox_pdf"], candidate["bbox_pdf"]) >= 0.85), None)
        if same:
            same["signals"] = sorted(set(same["signals"] + candidate["signals"]))
            if same["modality"] != candidate["modality"]:
                same["modality"] = "mixed"
            same["warnings"] = sorted(set(same["warnings"] + candidate["warnings"]))
            same["caption_refs"] += [cap for cap in candidate["caption_refs"] if cap not in same["caption_refs"]]
        else:
            merged.append(candidate)
    return sorted(merged, key=lambda c: (*c["bbox_pdf"], c["asset_type"]))


def _scan_page(page, strategies):
    bounds = [0, 0, page.cropbox.width, page.cropbox.height]
    warnings = []
    check, replacements = text_layer.inspect(page)
    text = text_layer.normalize(page.get_text("text"), replacements)
    if text_layer.private_use_count(text):
        check = {**check, "needs_ocr": True, "reasons": check["reasons"] + ["private_use_remaining"]}
    blocks = [(b, text_layer.normalize(b[4], replacements)) for b in page.get_text("blocks") if b[6] == 0]
    captions = []
    for block, content in blocks:
        first = " ".join(content.strip().splitlines()[:2])
        match = CAPTION.match(first)
        box = rectangle(block[:4], bounds)
        if match and not TOC.search(first) and box:
            captions.append({"text": first, "bbox_pdf": box, "block_id": str(block[5]),
                "asset_type": "table" if match.group(1).lower() == "table" else "figure",
                "continued": bool(re.search(r"\bcontinued\b", first, re.I))})
    drawings = page.get_drawings()
    candidates = []
    native_counts = {}
    for strategy in strategies if text.strip() else []:
        try:
            tables = page.find_tables(strategy=strategy, paths=drawings).tables
            native_counts[strategy] = 0
            for table in tables:
                box = rectangle(table.bbox, bounds)
                if box and table.row_count >= 2 and table.col_count >= 2:
                    native_counts[strategy] += 1
                    candidates.append({"asset_type": "table", "modality": "native", "bbox_pdf": box,
                        "bbox_status": "region_candidate", "signals": ["native_grid_" + strategy],
                        "structure_hint": {"rows": table.row_count, "columns": table.col_count,
                                           "structure_verified": False}, "caption_refs": near_caption(box, captions),
                        "warnings": ["text_alignment_may_be_prose"] if strategy == "text" else []})
        except Exception as error:
            warnings.append(f"table_detector_{strategy}: {type(error).__name__}: {error}")
    images = page.get_image_info()
    image_boxes = []
    page_area = fitz.Rect(bounds).get_area()
    for image in images:
        box = rectangle(image["bbox"], bounds)
        if not box:
            continue
        image_boxes.append(box)
        caps = near_caption(box, captions)
        kind = caps[0]["asset_type"] if caps else "unknown_visual"
        fraction = fitz.Rect(box).get_area() / page_area
        candidates.append({"asset_type": kind, "modality": "raster", "bbox_pdf": box,
            "bbox_status": "region_candidate", "signals": ["raster_placement"] + (["page_raster"] if fraction >= 0.75 else []),
            "structure_hint": {"image_width": image["width"], "image_height": image["height"], "page_area_fraction": round(fraction, 4)},
            "caption_refs": caps, "warnings": ["raster_may_contain_multiple_assets"] if fraction >= 0.75 else []})
    try:
        for value in page.cluster_drawings(drawings=drawings, x_tolerance=8, y_tolerance=8):
            box = rectangle(value, bounds)
            if not box:
                continue
            # Retain line art; an enclosing table region already accounts for its grid.
            if any(c["asset_type"] == "table" and fitz.Rect(c["bbox_pdf"]).contains(fitz.Rect(box)) for c in candidates):
                continue
            caps = near_caption(box, captions)
            candidates.append({"asset_type": caps[0]["asset_type"] if caps else "unknown_visual", "modality": "vector",
                "bbox_pdf": box, "bbox_status": "region_candidate", "signals": ["vector_cluster"],
                "structure_hint": {}, "caption_refs": caps, "warnings": ["line_art_may_be_decoration"]})
    except Exception as error:
        warnings.append(f"vector_detector: {type(error).__name__}: {error}")
    for cap in captions:
        if not any(cap in c["caption_refs"] or fitz.Rect(c["bbox_pdf"]).contains(fitz.Rect(cap["bbox_pdf"])) for c in candidates):
            candidates.append({"asset_type": cap["asset_type"], "modality": "native", "bbox_pdf": cap["bbox_pdf"],
                "bbox_status": "caption_only", "signals": ["unassociated_caption"], "structure_hint": {},
                "caption_refs": [cap], "warnings": ["body_extent_unknown"]})
    full_raster = any(fitz.Rect(box).get_area() / page_area >= 0.75 for box in image_boxes)
    needs_ocr = bool(not text.strip() or check["needs_ocr"])
    modality = "suspect_text_layer" if check["needs_ocr"] else "scan" if not text.strip() and images else "mixed" if images else "native"
    for candidate in candidates:
        if needs_ocr and candidate["modality"] == "native":
            candidate["signals"].append("needs_ocr")
    return {"modality": modality, "text_chars": len(text), "needs_ocr": needs_ocr, "text_quality": check,
            "signals": {"raster_placements": len(images), "vector_paths": len(drawings), "native_table_candidates": native_counts,
                        "caption_candidates": len(captions), "full_page_raster": full_raster},
            "page_geometry": {"coordinate_space": "pymupdf_unrotated_crop_points_top_left", "bounds": bounds,
                              "cropbox": list(page.cropbox), "mediabox": list(page.mediabox), "rotation": page.rotation},
            "warnings": warnings}, merge_candidates(candidates)


def scan_page(page, strategies):
    # PyMuPDF 1.26 table detection uses rotated coordinates; other geometry APIs
    # use unrotated coordinates. Normalize the in-memory page, never save it.
    rotation = page.rotation
    try:
        if rotation:
            page.set_rotation(0)
        result, candidates = _scan_page(page, strategies)
        result["page_geometry"]["rotation"] = rotation
        return result, candidates
    finally:
        if rotation:
            page.set_rotation(rotation)
