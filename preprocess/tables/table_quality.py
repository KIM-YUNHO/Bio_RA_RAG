"""Conservative HTML table checks. Passing is structural, not semantic approval."""
from collections import Counter
from html.parser import HTMLParser
import re
import unicodedata

QUALITY_VERSION = "table-quality-6"
NUMBER = re.compile(r"(?<![\w.])[-+−]?\d+(?:[.,]\d+)*(?:[eE][-+]?\d+)?%?(?!\w|\.\d)")
REDACTION = re.compile(r"\(b\)\s*\([46]\)|\[redacted\]|\bredacted\b", re.I)


def comparable_text(text):
    text = unicodedata.normalize("NFKC", text or "").replace("−", "-").replace('\\n', '\n')
    text = text.replace('$', '').replace('\\pm', '±')
    # Match native t1/2 and model t _{{1/2}} without creating a new number 1.
    text = re.sub(r"([A-Za-zμ])\s*[_^]\s*\{+([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}+",
                  lambda m:m[1]+m[2].replace('{','').replace('}',''), text)
    return text


def numbers(text):
    text = comparable_text(text)
    return Counter(NUMBER.findall(text))


class TableHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.row, self.cell = [], None, None
        self.table_depth = 0
        self.tables = 0
        self.errors = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.tables += 1
            self.table_depth += 1
            if self.table_depth > 1:
                self.errors.append("nested_table")
        elif tag == "tr" and self.table_depth:
            if self.row is not None:
                self.errors.append("unclosed_row")
            self.row = []
        elif tag in ("td", "th") and self.table_depth:
            if self.row is None or self.cell is not None:
                self.errors.append("invalid_cell_nesting")
            attrs = dict(attrs)
            spans = []
            for name in ("rowspan", "colspan"):
                value = attrs.get(name, "1")
                if not isinstance(value, str) or not value.isdigit() or not 1 <= int(value) <= 200:
                    self.errors.append("invalid_span")
                    value = "1"
                spans.append(int(value))
            self.cell = {"text": "", "row_span": spans[0], "col_span": spans[1], "is_header": tag == "th"}
        elif tag == "br" and self.cell is not None:
            self.cell["text"] += "\n"

    def handle_data(self, data):
        if self.cell is not None:
            self.cell["text"] += data

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            if self.cell is None or self.row is None:
                self.errors.append("unmatched_cell_end")
            else:
                self.cell["text"] = self.cell["text"].strip()
                self.row.append(self.cell)
                self.cell = None
        elif tag == "tr":
            if self.cell is not None or self.row is None:
                self.errors.append("invalid_row_end")
            else:
                self.rows.append(self.row)
                self.row = None
        elif tag == "table":
            self.table_depth -= 1


def inspect_table(content, reference_text="", reference_reliable=False, source_redaction_text=""):
    parser = TableHTML()
    try:
        parser.feed(content or "")
        parser.close()
    except Exception:
        parser.errors.append("invalid_html")
    issues = list(parser.errors)
    if parser.tables != 1:
        issues.append("expected_one_html_table")
    if parser.table_depth or parser.row is not None or parser.cell is not None:
        issues.append("truncated_html")
    if len(parser.rows) < 2:
        issues.append("table_has_no_body")
    if len(parser.rows) > 2500 or sum(len(r) for r in parser.rows) > 100000:
        issues.append("table_exceeds_limits")
        parser.rows = []
    cells, occupied = [], {}
    for row_no, row in enumerate(parser.rows):
        column = 0
        for cell in row:
            while (row_no, column) in occupied:
                column += 1
            if column + cell["col_span"] > 200 or row_no + cell["row_span"] > len(parser.rows):
                issues.append("span_outside_table")
                continue
            index = len(cells)
            cells.append({**cell, "row": row_no, "column": column, "bbox_pdf": None})
            for r in range(row_no, row_no + cell["row_span"]):
                for c in range(column, column + cell["col_span"]):
                    if (r, c) in occupied:
                        issues.append("overlapping_spans")
                    occupied[r, c] = index
            column += cell["col_span"]
    width = max((c + 1 for _, c in occupied), default=0)
    if width and any((r, c) not in occupied for r in range(len(parser.rows)) for c in range(width)):
        issues.append("inconsistent_row_width")
    if len(parser.rows) >= 2 and any(c["row"] == 0 and c["row_span"] == len(parser.rows)
                                    and c["text"].strip() for c in cells):
        # Often a page-top continuation: the model spans a header over absent
        # leading row labels. It can also be legitimate, so route for review.
        issues.append("first_row_spans_entire_table_needs_context")
    text = "\n".join(c["text"] for c in cells)
    written = numbers(text)
    missing, unsupported = [], []
    if reference_reliable:
        reference = numbers(reference_text)
        missing = sorted((reference - written).elements())
        unsupported = sorted((written - reference).elements())
        if missing:
            issues.append("source_numbers_missing")
        if unsupported:
            issues.append("numbers_not_in_native_reference")
        # Number bags do not catch a dropped treatment/group header. This is
        # only a coverage alarm: it neither assigns cells nor approves meaning.
        def label_words(value):
            value = re.sub(r'\\[A-Za-z]+', ' ', comparable_text(value))
            return set(re.findall(r'[a-z]{5,}', value.casefold()))
        model_words = label_words(text)
        missing_labels = sorted(w for w in label_words(reference_text)
            if not any(w == v or w == v + suffix for v in model_words for suffix in ('', 'a', 'b', 'c', 'd', 'e')))
        if missing_labels:
            issues.append('native_label_tokens_missing')
    else:
        missing_labels = []
    if "[unreadable]" in (content or "").lower():
        issues.append("unreadable_cells")
    if re.search(r"\(b\)\s*\([46]\)|\bredact(?:ed|ion)\b|\[redacted\]", text, re.I):
        issues.append("redacted_cells_require_review")
        if any(c["row_span"] > 1 and re.search(r"\(b\)\s*\([46]\)", c["text"], re.I)
               and re.search(r"[A-Za-z]{3,}", c["text"]) for c in cells):
            issues.append("visible_text_spans_redacted_rows")
    # A second model cannot erase source redactions and thereby make a table eligible.
    if REDACTION.search(reference_text + '\n' + source_redaction_text):
        issues.append('source_redactions_require_review')
        if not REDACTION.search(text):
            issues.append('source_redaction_marker_not_preserved')
        if any(c['row_span'] > 1 and not c['is_header'] and re.search(r'[A-Za-z]{3,}', c['text'])
               and not REDACTION.fullmatch(c['text'].strip()) for c in cells):
            issues.append('rowspan_in_redacted_table_requires_review')
    if reference_reliable:
        reference_labels = re.sub(r"\s", "", comparable_text(reference_text))
        changed_labels = [c["text"] for c in cells if c["column"] == 0
            and re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", c["text"])
            and re.sub(r"\s", "", comparable_text(c["text"])) not in reference_labels]
        if changed_labels:
            issues.append("native_row_labels_not_preserved")
    if re.search(r"\bcontinued\b|続き|续表", content or "", re.I):
        issues.append("continuation_needs_context")
    return {"version": QUALITY_VERSION, "issues": sorted(set(issues)), "rows": len(parser.rows),
            "columns": width, "cells": cells, "text": text, "numbers": dict(written),
            "missing_numbers": missing[:100], "unsupported_numbers": unsupported[:100],
            "missing_label_tokens": missing_labels[:100],
            "reference_reliable": reference_reliable, "semantic_status": "unverified",
            "numeric_evidence_eligible": False}


def route_asset(kind, quality, content):
    if kind == "figure":
        return {"task": "describe_figure", "reasons": ["figure_requires_visual_description"]}
    if kind == "table" and quality["issues"]:
        return {"task": "repair_table", "reasons": quality["issues"]}
    return {"task": None, "reasons": []}
