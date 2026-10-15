"""Recover source headings, without deciding a passage's clinical meaning.

Numbered headings, exact standalone registry identifiers and conventional source
subheadings are structural candidates. HTML heading tags are read from the saved
source without changing the existing section/page locators. Every node records
the original heading position; no inferred study name is invented as a heading.
"""
import hashlib
import re
from html.parser import HTMLParser

try:
    from . import study_registry as sr
except ImportError:  # CLI modules retain their historical import path.
    import study_registry as sr

VERSION = "sections-8"
# Opt-in: one-level numbered layout titles. Evaluated on release 05 (2026-10-15) and not
# adopted as the default: no retrieval gain, silver recall fell (step titles over-split).
SECTIONS_9 = "sections-9"
COMPAT_VERSION = "sections-4"
NUMBERED = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){1,4})\.?\s+([A-Z].{2,110})$")
# "7. MARKETING AUTHORISATION HOLDER": one level only. In running text the same
# shape is a list item ("9. Remove the cap"), so it needs the parser's layout
# title label as independent evidence (sections-9).
TOP_NUMBERED = re.compile(r"^(\d{1,2})\.\s+([A-Z].{2,110})$")
TOC = re.compile(r"\.{2,}|\u2026")
SUBHEADINGS = {
    "methods", "results", "discussion", "conclusion", "conclusions",
    "study design", "study population", "study objectives", "statistical analysis",
    "statistical methods", "analysis populations", "analysis sets", "endpoints",
    "primary endpoints", "secondary endpoints", "efficacy results", "safety results",
    "pharmacokinetic results", "immunogenicity results",
    "ada assay", "nab assay", "anti-drug antibody assay", "neutralising antibody assay",
    "neutralizing antibody assay", "patient flow", "recruitment", "conduct of the study",
    "protocol deviations", "pharmacokinetic assay", "bioanalytical methods",
    "sampling time points",
    "sensitivity analysis", "post-hoc analysis", "post hoc analysis",
    "primary analysis", "primary analyses", "primary pk analysis",
    "statistical analyses", "statistical methods and analyses",
    "sensitivity analyses", "post-hoc analyses", "post hoc analyses",
    "ad hoc analysis", "ad-hoc analysis", "ad hoc analyses", "ad-hoc analyses",
    "body weight adjusted analysis", "weight-adjusted analysis",
}
DETAILS = {"primary objective", "primary objectives", "secondary objective", "secondary objectives"}
CLINICAL_SUBHEADINGS = {
    "treatments", "treatment", "objectives", "inclusion criteria", "exclusion criteria",
    "numbers analysed", "numbers analyzed", "outcomes/endpoints", "outcomes and endpoints",
    "interventions", "randomisation", "randomization", "blinding", "dose and administration",
    "traitements", "objectifs", "critères d'inclusion", "critères d'exclusion",
    "population d'analyse", "analyse principale", "analyses statistiques",
}
TOPICS = {
    "bioequivalence", "pharmacokinetics", "pharmacodynamics", "clinical efficacy",
    "clinical safety", "immunogenicity", "comparative pharmacokinetics",
    "comparative clinical efficacy", "comparative clinical safety",
    "impact of immunogenicity on pk", "indications", "extrapolation of indications",
}


def key(text):
    return re.sub(r"\s+", " ", text).strip().rstrip(":.").casefold()


def identify(text, product=None, *, version=COMPAT_VERSION, layout_title=False):
    """Only standalone heading candidates. Prose naming a study is not a heading.

    layout_title: the source parser laid this line out as a title (sections-9).
    """
    text = text.strip()
    if not text or "\n" in text or len(text) > 125 or TOC.search(text):
        return None
    # Registry matching is identifier recognition, not subject/comparison judgment.
    for pid in ([product] if product else sr.products()):
        matches = sr.finditer(text, pid)
        if len(matches) == 1:
            start, end, study = matches[0]
            before = key(text[:start])
            # Closing a wrapped table caption or a sentence is not a heading.
            after = text[end:].strip()
            phase_title = re.fullmatch(r"(?:clinical )?(?:phase (?:[1-3]|i{1,3}) )?study\s*\(?", before)
            topic_title = re.fullmatch(r"(?:bioequivalence|pharmacokinetics)\s*\(\s*study", before)
            clinical_title = version in (VERSION, SECTIONS_9) and re.fullmatch(
                r'(?:(?:single|multiple)[ -]dose )?(?:(?:pk|pharmacokinetic|clinical|similarity|comparative|pivotal|supportive|efficacy|safety|bioequivalence) )*'
                r'study(?: in [a-z -]{3,55})?\s*\(?', before) and not re.search(
                    r'\b(?:was|were|is|are|showed|reported|received|included)\b', before)
            wrapped = before.endswith("(") or topic_title
            if after not in ((")", "):" ) if wrapped else ("", ":")):
                continue
            if before == "" or phase_title or topic_title or clinical_title:
                return {"title": text, "kind": "study", "study_keys": [study], "basis": "standalone_identifier"}
    match = NUMBERED.fullmatch(text)
    if match:
        return {"title": text, "kind": "numbered", "code": match[1], "study_keys": [], "basis": "numbered_heading"}
    match = TOP_NUMBERED.fullmatch(text) if version == SECTIONS_9 and layout_title else None
    if match:
        return {"title": text, "kind": "numbered", "code": match[1], "study_keys": [],
                "basis": "top_level_numbered_layout_title"}
    normalized = key(text)
    if re.fullmatch(r"(?:post[ -]?hoc )?pooled analys[ie]s", normalized):
        return {"title": text, "kind": "cross_analysis", "study_keys": [], "basis": "standalone_heading_text"}
    # A source's explicit part qualifier belongs to the structural heading;
    # it supplies evidence, never an automatic semantic assignment.
    bare = re.sub(r"\s*\(part\s+\d+\)$", "", normalized)
    if version not in (COMPAT_VERSION, 'sections-5', 'sections-6', 'sections-7', VERSION, SECTIONS_9):
        raise ValueError('Unknown section rules version')
    if version in ('sections-7', VERSION, SECTIONS_9) and re.fullmatch(
            r'(?:(?:clinical|pivotal|supportive) )?phase (?:[1-4](?:/[1-4])?|[ivx]+(?:/[ivx]+)?) '
            r'(?:clinical )?(?:study|trial)(?:\s*\([^()]{1,60}\))?', normalized):
        return {'title':text,'kind':'study_boundary','study_keys':[],
                'basis':'standalone_unidentified_study_heading'}
    headings = SUBHEADINGS | CLINICAL_SUBHEADINGS if version != COMPAT_VERSION else SUBHEADINGS
    if version in ('sections-6', 'sections-7', VERSION, SECTIONS_9) and re.fullmatch(
            r'(?:(?:subcutaneous|intravenous|oral) )?(?:adult|pediatric|paediatric)(?: patients?)? '
            r'(?:dosage|dose)(?: regimen| and administration)?', bare):
        return {'title': text, 'kind': 'subsection', 'study_keys': [], 'basis': 'standalone_population_dosage_heading'}
    kind = "subsection" if bare in headings else "detail" if bare in DETAILS else "topic" if normalized in TOPICS else None
    return {"title": text, "kind": kind, "study_keys": [], "basis": "standalone_heading_text"} if kind else None


def source_segments(text, product=None):
    """Split standalone source headings, retaining every original character.

    This is a view over a source unit, not a new transcription. Inline prose,
    table cells and partial line matches never become structural headings.
    """
    boundaries = {0, len(text)}
    offset = 0
    for line in text.splitlines(keepends=True):
        if identify(line.strip(), product, version=VERSION):
            boundaries.update((offset, offset + len(line)))
        offset += len(line)
    positions = sorted(boundaries)
    return [(start, end) for start, end in zip(positions, positions[1:]) if end > start]


class HtmlHeadings(HTMLParser):
    """Extract h1..h6 inside main, retaining source order and tag level."""
    def __init__(self):
        super().__init__()
        self.main = 0
        self.skip = 0
        self.active = None
        self.items = []

    def handle_starttag(self, tag, attrs):
        if tag == "main":
            self.main += 1
        if not self.main:
            return
        if tag in ("script", "style", "nav", "form"):
            self.skip += 1
        elif not self.skip and re.fullmatch(r"h[1-6]", tag):
            self.active = {"tag": tag, "level": int(tag[1]), "text": ""}

    def handle_data(self, data):
        if self.active is not None and not self.skip:
            self.active["text"] += data

    def handle_endtag(self, tag):
        if self.active is not None and tag == self.active["tag"]:
            title = " ".join(self.active["text"].split())
            if title:
                self.items.append({"title": title, "level": self.active["level"]})
            self.active = None
        if tag in ("script", "style", "nav", "form") and self.skip:
            self.skip -= 1
        if tag == "main":
            self.main -= 1


def html_heading_map(raw, pages, clean):
    """Map saved heading tags to exact lines in existing parsed HTML sections.

    Existing locators stay unchanged. Matching proceeds in source order, so
    identical headings in different sections are not collapsed.
    """
    parser = HtmlHeadings()
    parser.feed(raw)
    positions = [(p["pdf_page"], i, clean(line)) for p in pages for i, line in enumerate(p["text"].split("\n"))]
    cursor, out = 0, {}
    for heading in parser.items:
        for n in range(cursor, len(positions)):
            page, line, text = positions[n]
            if text == clean(heading["title"]):
                recognized = identify(text)
                out[(page, line)] = {**heading, "kind": "html",
                                    "study_keys": recognized["study_keys"] if recognized else [], "basis": "source_html_tag"}
                cursor = n + 1
                break
    return out


class Tracker:
    def __init__(self, document_id, version_id, product, *, version=COMPAT_VERSION):
        self.document_id, self.version_id, self.product = document_id, version_id, product
        self.version = version
        self.stack = []
        self.nodes = {}

    def scan(self, texts, refs, page, source_kind="native", region=None, explicit=None, layout_titles=()):
        explicit = explicit or {}
        candidates = {}
        for i, text in enumerate(texts):
            found = explicit.get(refs[i]["line_start"]) or identify(' '.join(text.split()), self.product, version=self.version,
                                                                    layout_title=i in layout_titles)
            if found:
                # A short label next to other cells on the same row is not a
                # section heading. Native table columns often say "Results".
                box = refs[i].get("bbox_pdf")
                if not explicit and box and any(j != i and (other := refs[j].get("bbox_pdf"))
                        and abs(other[1] - box[1]) <= max(2, min(other[3] - other[1], box[3] - box[1]) * .4)
                        and (other[0] >= box[2] + 2 or other[2] <= box[0] - 2) for j in range(len(refs))):
                    continue
                # A wrapped identifier must not become a numbered section.
                if found["kind"] == "numbered" and i:
                    previous = texts[i - 1].split("\n")[-1]
                    joined = previous + "\n" + text
                    if any(s < len(previous) < e for s, e, _ in sr.finditer(joined, self.product)):
                        continue
                candidates[i] = found
        numbered_count = sum(h["kind"] == "numbered" for h in candidates.values())
        toc_label = any(key(text) in ("contents", "table of contents") for text in texts)
        narrative = any(i not in candidates and len(text.split()) >= 8 for i, text in enumerate(texts))
        # A dense page of real subsections is not a TOC merely because it has
        # six headings. Preserve headings when they have accompanying prose.
        if numbered_count >= 6 and (toc_label or not narrative):
            candidates = {i: h for i, h in candidates.items() if h["kind"] != "numbered"}
        breaks, labels, paths, states = set(), [], [], []
        for i, text in enumerate(texts):
            if i in candidates:
                self.push(candidates[i], page, refs[i], source_kind, region)
                breaks.add(i)
            if re.match(r"^(?:Table|Figure)\s+\d+", text, re.I):
                breaks.add(i)
            state = self.state()
            paths.append(state["path"])
            labels.append(" > ".join(state["path"]) or None)
            states.append(state)
        return breaks, labels, paths, states

    def push(self, heading, page, ref, source_kind, region):
        kind = heading["kind"]
        if self.stack and self.stack[-1]["title"] == heading["title"]:
            return  # repeated running heading; keep its original source anchor
        if kind == "numbered":
            code = heading["code"]
            self.stack = [n for n in self.stack if n.get("code") and code.startswith(n["code"] + ".")]
        elif kind == "html":
            # Remove implicit text subheadings with their enclosing HTML branch.
            while self.stack and ("html_level" not in self.stack[-1] or self.stack[-1]["html_level"] >= heading["level"]):
                self.stack.pop()
        elif kind in ("study", "study_boundary"):
            previous = next((i for i, n in enumerate(self.stack) if n["kind"] in ("study", "study_boundary")), None)
            self.stack = self.stack[:previous] if previous is not None else [n for n in self.stack if n["kind"] not in ("subsection", "detail")]
        elif kind == "topic":
            self.stack = [n for n in self.stack if n["kind"] in ("numbered", "html", "study", "study_boundary")]
        elif kind == "cross_analysis":
            self.stack = [n for n in self.stack if n["kind"] in ("numbered", "html", "topic")]
        elif kind == "subsection":
            self.stack = [n for n in self.stack if n["kind"] not in ("subsection", "detail")]
        else:
            self.stack = [n for n in self.stack if n["kind"] != "detail"]
        location = {"pdf_page": page, "source_kind": source_kind, "region": region, **ref}
        identity = repr((self.document_id, self.version_id, heading["title"], location))
        node_id = f"{self.document_id}:{self.version_id}:s{hashlib.sha256(identity.encode()).hexdigest()[:16]}"
        node = {**heading, "section_id": node_id, "parent_section_id": self.stack[-1]["section_id"] if self.stack else None,
                "heading_location": location, "status": "structural_candidate", "depth": len(self.stack) + 1}
        if kind == "html":
            node["html_level"] = heading["level"]
        self.nodes[node_id] = node
        self.stack.append(node)

    def state(self):
        return {"path": [n["title"] for n in self.stack], "section_id": self.stack[-1]["section_id"] if self.stack else None,
                "ancestor_section_ids": [n["section_id"] for n in self.stack],
                "heading_studies": sorted({k for n in self.stack for k in n["study_keys"]}),
                "heading_anchors": [{k: n[k] for k in ("section_id", "parent_section_id", "title", "kind", "basis",
                                                        "study_keys", "depth", "heading_location")} for n in self.stack]}
