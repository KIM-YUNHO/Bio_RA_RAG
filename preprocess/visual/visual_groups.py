"""Caption-anchored logical figures/tables, keeping every physical region intact."""
from collections import defaultdict
import re
import unicodedata

from preprocess.ingest import stable_hash
from preprocess.visual.text_checks import figure_label_reference

GROUPING_VERSION = "visual-groups-8"
CAPTION = re.compile(r"^\s*(Figure|Fig\.?|図|Table|表)\s*"
    r"(\d+(?:[.-]\d+)*(?:[A-Z])?|[A-Z]\d+(?:[.-]\d+)*|[IVXLCDM]+|[A-Z])"
    r"(?=$|\W)", re.I)


def caption_identity(asset):
    value = unicodedata.normalize("NFKC", asset["base"]["content"]).strip()
    if asset["block_label"] not in {"figure_title", "table_title", "caption", "paragraph_title"} and not (
            asset['asset_type']=='text' and asset['block_label'] in {'text','header'}
            and (CAPTION.fullmatch(value) or asset.get('caption_recovery', {}).get('basis') ==
                 'native_title_repeated_in_adjacent_table_header')
            and (asset['block_label']!='header' or asset.get('native_reference',{}).get('reliable'))):
        return None
    match = CAPTION.match(value)
    if not match:
        return None
    # A carried-over prose reference such as "Figure C)" is not a printed caption.
    if value[match.end():].lstrip().startswith(')'):
        return None
    identifier = match[2]
    # Title-case prose such as "Figure Mix" is not a Roman-number caption.
    if identifier.isalpha() and len(identifier) > 1 and not identifier.isupper():
        return None
    kind = "figure" if match[1].casefold().startswith(("fig", "図")) else "table"
    return kind, identifier.upper()


def display_box(asset):
    w, h = asset["input_image_size"]
    return [v * 1000 / (w if i % 2 == 0 else h) for i, v in enumerate(asset["bbox_input_px"])]


def horizontal_overlap(a, b):
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) / max(1, min(a[2]-a[0], b[2]-b[0]))


def between(a, b, item):
    lo, hi = min(a[3], b[3]), max(a[1], b[1])
    return lo <= item[1] and item[3] <= hi


def margin_caption_above(cap, figure, rows):
    """Short left-margin caption above a centered/full-width figure, with no column guess."""
    cb, box = display_box(cap), display_box(figure)
    identity = caption_identity(cap)
    literal = unicodedata.normalize('NFKC', cap['base']['content']).strip()
    match = CAPTION.fullmatch(literal)
    if (not match or identity[0] != 'figure' or cb[0] > 180 or cb[2]-cb[0] > 180
            or box[0] < cb[0]-20 or not 0 <= box[1]-cb[3] <= 120
            or not (cb[2] <= box[0] or box[2]-box[0] >= 500)):
        return False
    # Another diagram in the left column makes this a column ownership ambiguity.
    return not any(r is not figure and r['asset_type'] in {'figure','table'}
        and display_box(r)[2] <= box[0] and display_box(r)[0] >= cb[0]-20
        and min(display_box(r)[3],box[3]) > max(display_box(r)[1],box[1]) for r in rows)


def row_reference_identity(figure, rows):
    """A literal reference beside a diagram can disambiguate adjacent caption tiles."""
    from preprocess.visual.visual_context import EXTERNAL, references
    box = display_box(figure)
    identities = set()
    for row in rows:
        if row['asset_type'] != 'text' or row['block_label'] not in {'text','paragraph_title'} or caption_identity(row):
            continue
        rb = display_box(row)
        overlap = max(0,min(box[3],rb[3])-max(box[1],rb[1])) / max(1,min(box[3]-box[1],rb[3]-rb[1]))
        gap = max(0,box[0]-rb[2],rb[0]-box[2])
        if overlap < .5 or horizontal_overlap(box,rb) > 0 or gap > 120:
            continue
        native = row.get('native_reference') or {}
        text = native['text'] if native.get('reliable') else row['base']['content']
        if EXTERNAL.match(text.lstrip()):
            continue
        identities.update((ref['kind'],ref['identifier']) for ref in references(text))
    return next(iter(identities)) if len(identities)==1 else None


def header_signature(asset):
    quality = asset["base"].get("quality") or {}
    def clean(text):
        return re.sub(r"\W", "", unicodedata.normalize("NFKC", text).casefold())
    return {clean(c["text"]) for c in quality.get("cells", []) if c["row"] < 3 and clean(c["text"])}


def header_fragment_pair(previous, current):
    """Page-bottom blank-stub header plus page-top body; association only."""
    p, c = previous['base']['quality'], current['base']['quality']
    pb, cb = display_box(previous), display_box(current)
    return (pb[3] > 850 and cb[1] < 200 and horizontal_overlap(pb, cb) >= .8
        and p['columns'] == c['columns'] and p['columns'] >= 2
        and 1 <= p['rows'] <= 4 and c['rows'] > p['rows']
        and any(cell['column'] == 0 for cell in p['cells'])
        and all(not cell['text'].strip() for cell in p['cells'] if cell['column'] == 0)
        and all(not cell['text'].strip() for cell in c['cells'] if cell['row'] == 0 and cell['column'] == 0))


def normalized_text(value):
    return ' '.join(unicodedata.normalize('NFKC', value).split()).casefold()


def page_furniture(asset):
    return asset['block_label'] in {'header', 'footer', 'page_number'} or (
        asset['block_label'] == 'number' and bool(re.fullmatch(
            r'(?:page\s+)?\d+(?:\s*(?:of|/)\s*\d+)?',
            normalized_text(asset['base']['content']))))


def caption_starts_next_page(cap, table, previous_rows, current_rows):
    """A printed orphan table title can be the last content before a page break.

    Whitespace below it varies with pagination; require source order and a clear
    next-page owner instead of assuming the caption sits in the bottom 15%.
    """
    cb, tb = display_box(cap), display_box(table)
    if cb[3] < 500 or tb[1] > 200 or horizontal_overlap(cb, tb) < .4:
        return False
    if any(r is not cap and (not page_furniture(r) or caption_identity(r)) and display_box(r)[1] >= cb[3]-2
           for r in previous_rows):
        return False
    for row in current_rows:
        if row is table or page_furniture(row) and not caption_identity(row) or display_box(row)[3] > tb[1]+2:
            continue
        # A printed caption continuation is assembled separately with its own
        # source coordinates. Other prose/captions prevent ownership guessing.
        if (row['asset_type'] == 'text' and not caption_identity(row)
                and re.match(r'\s*(or\b|and\b|continued\b|続き|续表)', row['base']['content'], re.I)):
            continue
        return False
    return True


def recover_table_captions(rows):
    """Promote a mislabeled title only with native, table-header and geometry support."""
    proposals = defaultdict(list)
    for cap in rows:
        if cap['asset_type'] != 'text' or cap['block_label'] != 'text' or caption_identity(cap):
            continue
        native = cap.get('native_reference') or {}
        value = unicodedata.normalize('NFKC', cap['base']['content']).strip()
        match = CAPTION.match(value)
        if (not native.get('reliable') or not match or match[1].casefold() not in {'table', '表'}
                or normalized_text(value) != normalized_text(native['text'])):
            continue
        tail = value[match.end():]
        if not re.match(r'^\s*[:.：]\s*\S', tail):
            continue
        title = re.sub(r'^\s*[:.：]\s*', '', tail)
        cb = display_box(cap)
        if len(title) < 12 or len(value) > 600 or cb[3]-cb[1] > 100:
            continue
        candidates = []
        for table in rows:
            if table['asset_type'] != 'table':
                continue
            quality = table['base'].get('quality') or {}
            first = [c for c in quality.get('cells', []) if c['row'] == 0]
            tb = display_box(table)
            if (len(first) != 1 or first[0]['column'] != 0
                    or first[0]['col_span'] != quality.get('columns')
                    or normalized_text(first[0]['text']) != normalized_text(title)
                    or not 0 <= tb[1]-cb[3] <= 60 or horizontal_overlap(cb, tb) < .8
                    or abs(cb[0]-tb[0]) > 40):
                continue
            if any(r is not cap and r is not table and between(cb, tb, display_box(r))
                   and horizontal_overlap(tb, display_box(r)) >= .15 for r in rows):
                continue
            candidates.append(table)
        if len(candidates) == 1:
            proposals[candidates[0]['asset_id']].append(cap)
    for table_id, caps in proposals.items():
        if len(caps) == 1:
            caps[0]['caption_recovery'] = {
                'basis': 'native_title_repeated_in_adjacent_table_header',
                'table_asset_id': table_id, 'grouping_version': GROUPING_VERSION,
                'source_refs': caps[0]['source_refs']}


def native_cell_boundary_pair(previous, current, rows, previous_rows):
    """An unfinished parenthesis carried into a blank-stub row, never cell filling."""
    p, c = previous['base']['quality'], current['base']['quality']
    pb, cb = display_box(previous), display_box(current)
    if not (pb[3] > 850 and cb[1] < 200 and horizontal_overlap(pb, cb) >= .8
            and p['columns'] == c['columns'] == 2):
        return False
    last = {cell['column']: cell for cell in p['cells'] if cell['row'] == p['rows']-1}
    first = {cell['column']: cell for cell in c['cells'] if cell['row'] == 0}
    if (set(last) != {0, 1} or set(first) != {0, 1} or not last[0]['text'].strip()
            or first[0]['text'].strip() or any(cell['row_span'] != 1 or cell['col_span'] != 1
                for cell in [*last.values(), *first.values()])):
        return False
    tail, head = normalized_text(last[1]['text']), normalized_text(first[1]['text'])
    # The first carried fragment must close the delimiter opened on the prior page.
    if (not tail or not head or tail.count('(')-tail.count(')') != 1
            or not first[1]['text'].lstrip()[:1].islower() or ')' not in head[:160]
            or '(' in head[:head.index(')')]):
        return False
    # Compare only the sentence/delimiter boundary; unrelated math OCR is preserved.
    tail_sentence = re.split(r'(?<=[.!?])\s+', tail)[-1]
    fragments = (tail_sentence[-160:], head[:head.index(')')+1])
    if any(len(fragment) < 12 for fragment in fragments):
        return False
    for asset, fragment in zip((previous, current), fragments):
        native = asset.get('native_reference') or {}
        if not native.get('reliable') or fragment not in normalized_text(native['text']):
            return False
    # A new section or body text between the page edge and table breaks this rule.
    return not any(r is not table and r['asset_type'] == 'text'
        and not page_furniture(r)
        and (display_box(r)[3] <= box[1] if before else display_box(r)[1] >= box[3])
        and horizontal_overlap(box, display_box(r)) >= .4
        for table, box, local, before in ((current, cb, rows, True),
            (previous, pb, previous_rows, False)) for r in local)


def expand_lateral_members(groups, unassigned, lookup, by_page):
    """Recover same-row neighbors of a narrow centered caption, not another figure."""
    changed = True
    while changed:
        changed = False
        for a in list(unassigned):
            if a['asset_type'] != 'figure' or a.get('grouping_issue'):
                continue
            ab = display_box(a)
            candidates = []
            for g in groups:
                if g['kind'] != 'figure' or (g['document_id'],g['version_id']) != (a['document_id'],a['version_id']):
                    continue
                members = [lookup[uid] for uid in g['member_asset_ids'] if lookup[uid]['pdf_page'] == a['pdf_page']]
                for m in members:
                    mb = display_box(m)
                    vertical = max(0,min(ab[3],mb[3])-max(ab[1],mb[1])) / max(1,min(ab[3]-ab[1],mb[3]-mb[1]))
                    gap = max(0,ab[0]-mb[2],mb[0]-ab[2])
                    if vertical < .7 or gap > 180:
                        continue
                    strip = [min(ab[0],mb[0]),max(ab[1],mb[1]),max(ab[2],mb[2]),min(ab[3],mb[3])]
                    rows = by_page[(a['document_id'],a['version_id'],a['pdf_page'])]
                    blocked = any((caption_identity(r) or r['block_label']=='paragraph_title')
                        and display_box(r)[1] < strip[3] and display_box(r)[3] > strip[1]
                        and horizontal_overlap(strip,display_box(r)) > .15 for r in rows)
                    blocked = blocked or any(r['block_label']=='paragraph_title'
                        and 0 <= ab[1]-display_box(r)[3] <= 80
                        and horizontal_overlap(ab,display_box(r)) >= .4 for r in rows)
                    if not blocked:
                        candidates.append(g)
                        break
            unique = {g['group_id']:g for g in candidates}
            if len(unique) == 1:
                g = next(iter(unique.values()))
                g['member_asset_ids'].append(a['asset_id'])
                g['relations'].append({'asset_id':a['asset_id'],'relation':'lateral_region_geometry_candidate'})
                unassigned.remove(a)
                changed = True


def build_visual_groups(assets):
    """Heuristic associations, never semantic approval or inferred missing cells."""
    physical = [a for a in assets if not a.get("is_logical_group")]
    by_page = defaultdict(list)
    for a in physical:
        a.pop("logical_group_id", None)
        a.pop("panel_index", None)
        a.pop("grouping_issue", None)
        a.pop("continuation_status", None)
        a.pop('table_context_role', None)
        a.pop('context_association_issues', None)
        a.pop('caption_association_basis', None)
        a.pop('caption_recovery', None)
        by_page[(a["document_id"], a["version_id"], a["pdf_page"])].append(a)
    groups, unassigned = [], []
    for location, rows in by_page.items():
        recover_table_captions(rows)
        captions = [a for a in rows if caption_identity(a)]
        assigned = defaultdict(list)
        for a in [r for r in rows if r["asset_type"] in {"figure", "table"}]:
            candidates = []
            box = display_box(a)
            for cap in captions:
                kind, _ = caption_identity(cap)
                if kind != a["asset_type"]:
                    continue
                cb = display_box(cap)
                margin = margin_caption_above(cap, a, rows) if kind == 'figure' else False
                if horizontal_overlap(box, cb) < .15 and not margin:
                    continue
                gap = max(0, cb[1]-box[3], box[1]-cb[3])
                if gap > 500:
                    continue
                blockers = [r for r in rows if r is not a and r is not cap
                    and between(cb, box, display_box(r)) and horizontal_overlap(box, display_box(r)) >= .15
                    and (caption_identity(r) or r["block_label"] == "paragraph_title"
                         or r["block_label"] == "text" and len(r["base"]["content"]) >= 160)]
                if not blockers:
                    candidates.append((gap, cap, margin))
            row_identity = row_reference_identity(a, rows) if a['asset_type']=='figure' else None
            row_matches = [item for item in candidates if caption_identity(item[1]) == row_identity]
            margin_matches = [item for item in candidates if item[2]]
            basis = None
            if len(row_matches)==1:
                candidates, basis = row_matches, 'same_row_explicit_reference_candidate'
            elif len(margin_matches)==1:
                candidates, basis = margin_matches, 'left_margin_caption_above_candidate'
            candidates.sort(key=lambda x:x[0])
            if candidates and (len(candidates)==1 or candidates[1][0]-candidates[0][0] >= 15):
                assigned[candidates[0][1]["asset_id"]].append(a)
                if basis:
                    a['caption_association_basis'] = basis
            else:
                unassigned.append(a)
                if candidates:
                    a["grouping_issue"] = "ambiguous_caption_association"
        for cap in captions:
            kind, number = caption_identity(cap)
            members = assigned.get(cap["asset_id"], [])
            # An orphan table caption at a page bottom can introduce the next page's table.
            if not members and kind != "table":
                continue
            groups.append({"group_id":"g"+stable_hash([*location, kind, number, cap["bbox_pdf"]])[:26],
                "document_id":location[0], "version_id":location[1], "kind":kind,
                "label":f"{'Figure' if kind=='figure' else 'Table'} {number}",
                "caption_asset_id":cap["asset_id"], "member_asset_ids":[a["asset_id"] for a in members],
                "association_status":"geometry_candidate", "continuation_status":"not_detected",
                "relations":[{'asset_id':a['asset_id'],'relation':a['caption_association_basis']}
                    for a in members if a.get('caption_association_basis')], "grouping_version":GROUPING_VERSION})
    lookup = {a["asset_id"]:a for a in physical}
    expand_lateral_members(groups, unassigned, lookup, by_page)
    for a in sorted(unassigned, key=lambda x:(x["document_id"],x["pdf_page"],display_box(x)[1])):
        if a["asset_type"] != "table" or display_box(a)[1] > 200 or a.get("grouping_issue"):
            continue
        previous = []
        for group in groups:
            if group["kind"] != "table" or (group["document_id"],group["version_id"]) != (a["document_id"],a["version_id"]):
                continue
            cap = lookup[group["caption_asset_id"]]
            if not group["member_asset_ids"]:
                if (cap["pdf_page"] == a["pdf_page"]-1 and caption_starts_next_page(cap, a,
                        by_page[(cap['document_id'], cap['version_id'], cap['pdf_page'])],
                        by_page[(a['document_id'], a['version_id'], a['pdf_page'])])):
                    previous.append((group,"caption_on_previous_page"))
            else:
                last = lookup[group["member_asset_ids"][-1]]
                shared = header_signature(last) & header_signature(a)
                boundary = native_cell_boundary_pair(last, a,
                    by_page[(a['document_id'], a['version_id'], a['pdf_page'])],
                    by_page[(last['document_id'], last['version_id'], last['pdf_page'])])
                if (last["pdf_page"] == a["pdf_page"]-1 and display_box(last)[3] > 650
                    and last["base"]["quality"]["columns"] == a["base"]["quality"]["columns"]
                    and (len(shared) >= 4 or header_fragment_pair(last, a) or boundary)):
                    previous.append((group,"data_fragment_continuation_candidate",
                        'native_open_delimiter_cell_boundary' if boundary else 'header_structure'))
        if len(previous) == 1:
            group, relation, *basis = previous[0]
            group["member_asset_ids"].append(a["asset_id"])
            group["continuation_status"] = relation
            group["relations"].append({"asset_id":a["asset_id"],"relation":relation,
                **({'basis':basis[0]} if basis else {})})
    groups = [g for g in groups if g["member_asset_ids"]]
    jobs = []
    for group in groups:
        cap = lookup[group["caption_asset_id"]]
        members = sorted([lookup[x] for x in group["member_asset_ids"]],key=lambda a:(a["pdf_page"],display_box(a)[1],display_box(a)[0]))
        group["member_asset_ids"] = [a["asset_id"] for a in members]
        group["source_refs"] = [ref for a in [cap,*members] for ref in a["source_refs"]]
        group["pdf_pages"] = sorted({a["pdf_page"] for a in [cap,*members]})
        caption = cap["native_reference"]
        group["caption_text"] = caption["text"].strip() if caption["reliable"] else cap["base"]["content"]
        group["caption_origin"] = "native" if caption["reliable"] else "paddle"
        group["caption_part_asset_ids"] = [cap["asset_id"]]
        if group["continuation_status"] == "caption_on_previous_page":
            first = members[0]
            rows = by_page[(first["document_id"],first["version_id"],first["pdf_page"])]
            tails = []
            for tail in rows:
                gap = display_box(first)[1] - display_box(tail)[3]
                if (0 <= gap <= 60 and re.match(r"\s*(or\b|and\b|continued\b|続き|续表)", tail["base"]["content"], re.I)
                    and tail['asset_type'] == 'text' and not caption_identity(tail)
                    and tail["block_label"] in {"paragraph_title", "table_title", "figure_title", "caption", "text"}
                    and horizontal_overlap(display_box(first), display_box(tail)) >= .4
                    and not any(r not in (first, tail) and between(display_box(tail), display_box(first), display_box(r))
                        and horizontal_overlap(display_box(first), display_box(r)) >= .15 for r in rows)):
                    tails.append(tail)
            if len(tails) == 1:
                for tail in tails:
                    tail["logical_group_id"] = group["group_id"]
                    group["caption_part_asset_ids"].append(tail["asset_id"])
                    group["caption_text"] += "\n" + (tail["native_reference"]["text"].strip()
                        if tail["native_reference"]["reliable"] else tail["base"]["content"])
                    group["source_refs"].extend(tail["source_refs"])
        cap["logical_group_id"] = group["group_id"]
        for index, a in enumerate(members):
            a["logical_group_id"] = group["group_id"]
            a["panel_index"] = index if group["kind"]=="figure" else None
            if group["continuation_status"] == "data_fragment_continuation_candidate":
                a["continuation_status"] = group["continuation_status"]
        if group["kind"] != "figure":
            continue
        # Figure groups remain within a page. Cross-page figure association is not guessed.
        extras = [a for a in by_page[(cap['document_id'],cap['version_id'],cap['pdf_page'])]
                  if a.get('native_figure_owner_id') in group['member_asset_ids']]
        group['figure_context_asset_ids'] = [a['asset_id'] for a in extras]
        for a in extras:
            a['logical_group_id'] = group['group_id']
        related = [cap,*members,*extras]
        bounds = [min(a["bbox_input_px"][0] for a in related), min(a["bbox_input_px"][1] for a in related),
                  max(a["bbox_input_px"][2] for a in related), max(a["bbox_input_px"][3] for a in related)]
        titles = [a for a in by_page[(cap["document_id"],cap["version_id"],cap["pdf_page"])]
            if a["block_label"]=="figure_title" and not caption_identity(a)
            and bounds[0] <= a["bbox_input_px"][0] and a["bbox_input_px"][2] <= bounds[2]
            and bounds[1] <= a["bbox_input_px"][1] and a["bbox_input_px"][3] <= bounds[3]]
        for title in titles:
            title["logical_group_id"] = group["group_id"]
        group["panel_title_asset_ids"] = [a["asset_id"] for a in titles]
        refs = [ref for a in [*related,*titles] for ref in a["source_refs"]]
        group["source_refs"] = refs
        box = [min(a["bbox_pdf"][0] for a in related), min(a["bbox_pdf"][1] for a in related),
               max(a["bbox_pdf"][2] for a in related), max(a["bbox_pdf"][3] for a in related)]
        text_reference = "\n".join([group["caption_text"],*[a["base"].get("printed_text_reference","") for a in members],
                                     *[a["base"]["content"] for a in [*titles,*extras]]])
        jobs.append({**members[0], "asset_id":group["group_id"],"asset_type":"figure_group",
            "is_logical_group":True,"logical_group_id":group["group_id"],"block_label":"figure_group",
            "panel_index":None,
            "bbox_pdf":box,"bbox_input_px":bounds,"source_refs":refs,
            "group_label":group["label"],"member_asset_ids":group["member_asset_ids"],
            "base":{"engine":"PaddleOCR-VL-1.6","origin":"caption_and_region_assembly",
                "content":group["caption_text"],"quality":None,"printed_text_reference":figure_label_reference(text_reference)},
            "native_reference":{"text":caption["text"] if caption["reliable"] else "","reliable":caption["reliable"]},
            "route":{"task":"describe_figure","reasons":["describe_complete_caption_anchored_figure"]},
            "enrichment":None,"selected":{"origin":"native_figure_caption" if caption["reliable"] else "paddle_figure_caption",
                                          "content":group["caption_text"]},
            "validation_status":"pending_enrichment","numeric_evidence_eligible":False})
        from preprocess.extraction.visual_router import figure_group_route
        cascade_route=figure_group_route(members,jobs[-1].get('visual_structure_review'))
        if cascade_route is not None:jobs[-1]['route']=cascade_route
        for a in members:
            a["route"]={"task":None,"reasons":["described_via_logical_figure_group"],"parent_asset_id":group["group_id"]}
    from preprocess.tables.table_context import assemble
    assemble(physical, groups, GROUPING_VERSION)
    return groups, jobs
