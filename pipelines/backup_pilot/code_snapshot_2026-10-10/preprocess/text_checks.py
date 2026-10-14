"""Text support checks deliberately do not certify clinical or visual meaning."""
import re
import unicodedata
from html.parser import HTMLParser

from .table_quality import numbers


def words(text):
    return re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", text or "").casefold())


def plain_text(text):
    class Text(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.parts = []
        def handle_data(self, data):
            self.parts.append(data)
    parser = Text()
    parser.feed(text or "")
    return " ".join(parser.parts)


def figure_label_reference(text):
    # A document VLM can digitize curves even when asked for OCR. Such tables
    # and standalone numeric lines cannot support numbers in a figure summary.
    text = re.sub(r"<table\b.*?(?:</table>|$)", "", text or "", flags=re.I | re.S)
    lines = [plain_text(line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if "|" not in line and any(c.isalpha() for c in line))


def grounded_figure(answer, native_text, parsed_text):
    references = native_text + "\n" + plain_text(parsed_text)
    vocabulary = set(words(references))
    labels = [answer["x_axis"], answer["y_axis"], *answer["legend"], *answer["printed_labels"]]
    for panel in answer['panels']:
        labels += [panel['x_axis'],panel['y_axis'],panel['scale_label']]
    marked = []
    for label in labels:
        if label:
            tokens = words(label)
            supported = bool(tokens) and all(t in vocabulary for t in tokens)
            marked.append({"text": label, "textually_supported": supported})
    prose = "\n".join([answer["what_is_compared"], answer["summary"],
                        *[label for label in labels if label]])
    unsupported = sorted(set(numbers(prose)) - numbers(references).keys())
    issues = []
    if not references.strip():
        issues.append("no_textual_grounding_reference")
    if any(not x["textually_supported"] for x in marked):
        issues.append("labels_without_textual_support")
    if unsupported:
        issues.append("numbers_without_textual_support")
    return {"labels": marked, "unsupported_numbers": unsupported, "grounding_issues": issues,
            "grounding_source": "native_and_paddle_transcription",
            "semantic_status": "unverified"}


def figure_role_issues(answer):
    issues = []
    if answer['figure_type'] == 'diagram' and any(p['x_scale'] or p['y_scale'] for p in answer['panels']):
        issues.append('diagram_has_plot_scale')
    if any((p['x_scale']=='logarithmic' or p['y_scale']=='logarithmic') and not p['scale_label']
           for p in answer['panels']):
        issues.append('logarithmic_scale_without_printed_label')
    if answer['figure_type']=='chart' and re.search(
        r'\b(?:peak\w*|trend\w*|similar\w*|equivalen\w*|overlap\w*|declin\w*|increase\w*|decrease\w*|higher|lower)\b'
        r'|同等|類似|増加|減少|上昇|低下|유사|동등|증가|감소',
        answer['summary']+'\n'+answer['what_is_compared'],re.I):
        issues.append('chart_interpretation_outside_description_task')
    return issues
