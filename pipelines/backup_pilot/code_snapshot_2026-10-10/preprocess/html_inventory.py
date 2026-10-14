"""Locate DOM tables/images in saved HTML; never fetch referenced images."""
from html.parser import HTMLParser


class DomInventory(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.text = text
        self.line_offsets = [0]
        for line in text.splitlines(keepends=True):
            self.line_offsets.append(self.line_offsets[-1] + len(line))
        self.tables, self.images, self.stack = [], [], []

    def source_offset(self):
        line, column = self.getpos()
        return self.line_offsets[line - 1] + column

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            table = {"start": self.source_offset(), "end": None, "rows": 0, "cells": 0,
                     "merged_cells": 0, "node_path": f"table[{len(self.tables) + 1}]"}
            self.tables.append(table)
            self.stack.append(table)
        elif tag == "tr" and self.stack:
            self.stack[-1]["rows"] += 1
        elif tag in ("td", "th") and self.stack:
            self.stack[-1]["cells"] += 1
            attrs = dict(attrs)
            if attrs.get("rowspan", "1") != "1" or attrs.get("colspan", "1") != "1":
                self.stack[-1]["merged_cells"] += 1
        elif tag == "img":
            attrs = dict(attrs)
            self.images.append({"start": self.source_offset(), "end": self.source_offset() + len(self.get_starttag_text()),
                "node_path": f"img[{len(self.images) + 1}]", "src": attrs.get("src"), "alt": attrs.get("alt")})

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag == "table":
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag == "table" and self.stack:
            table = self.stack.pop()
            table["end"] = self.text.find(">", self.source_offset()) + 1


def inspect_html(text):
    parser = DomInventory(text)
    parser.feed(text)
    parser.close()
    candidates = []
    for table in parser.tables:
        incomplete = table["end"] is None
        table["end"] = table["end"] or len(text)
        candidates.append({"asset_type": "table", "modality": "html", "bbox_pdf": None,
            "bbox_status": "dom_node", "signals": ["html_table_node"], "caption_refs": [],
            "structure_hint": {k: table[k] for k in ("rows", "cells", "merged_cells")},
            "html_ref": {k: table[k] for k in ("start", "end", "node_path")},
            "warnings": ["unclosed_table_node"] if incomplete else []})
    for image in parser.images:
        candidates.append({"asset_type": "unknown_visual", "modality": "html", "bbox_pdf": None,
            "bbox_status": "dom_node", "signals": ["html_image_node"], "caption_refs": [],
            "structure_hint": {"src": image["src"], "alt": image["alt"]},
            "html_ref": {k: image[k] for k in ("start", "end", "node_path")},
            "warnings": ["referenced_image_not_fetched"]})
    return candidates
