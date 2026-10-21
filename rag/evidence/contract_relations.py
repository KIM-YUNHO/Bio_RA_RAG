"""Explicit local comparison/range/scope candidates, with original anchors.

Registry use is limited to identity and parent relationships; registry outcomes
are never evidence. Same page, nearby text and shared programme membership do not
certify an assay or a part-specific outcome. No case-specific exceptions.
"""
from copy import deepcopy
import re
from rag.evidence.contract_terms import atoms, key
from rag.evidence.grounded_table_graph import build, selected_fact

VERSION = "contract-relations-9-printed-prose-antecedents"
GAP = "[UNSUPPLIED SOURCE GAP]"


def _ratio_header(node):
    return bool(re.search(r"\bratio\b", node["value"], re.I) or
                re.search(r"\bratio\d+\b", node["value"], re.I) and re.search(r"<sup\b", node["quote"], re.I))


def _markers(node):
    symbols={a or b for a,b in re.findall(r'<sup[^>]*>\s*([\dA-Za-z]+)\s*</sup>|\^\s*\{\s*([\dA-Za-z]+)\s*\}',node['quote'],re.I)}
    # Some source OCR preserves a superscript as a flat trailing integer.
    # Only an actual ratio/CI header can carry this numeric marker.
    if _ratio_header(node) or any(a['value']=='CI90' for a in atoms(node['value'])):
        m=re.search(r'\s+(\d+)\s*$',node['value'])
        if m:symbols.add(m[1])
    return symbols


def _own_row_group(fact,table,text,product_id):
    cell=fact['cell']
    if cell['rowspan']<2:return None
    from rag.retrieval.study_registry import products
    known={*products(),'EU-Stelara','US-Stelara'}
    group=[n for n in table['cells'] if cell['row']<=n['row']<cell['row']+cell['rowspan'] and n['column']<cell['column']]
    roles={}
    for row in range(cell['row'],cell['row']+cell['rowspan']):
        own=[n for n in group if n['row']==row and n['rowspan']==1]
        labels=[n for n in own if re.fullmatch(r'Test|Reference',n['value'],re.I)]
        treatments=[n for n in own if arm(n['value'],product_id) in known]
        if len(labels)!=1 or len(treatments)!=1:return None
        role=labels[0]['value'].casefold()
        if role in roles:return None
        roles[role]={'treatment':arm(treatments[0]['value'],product_id),'node_ids':[treatments[0]['id'],labels[0]['id']]}
    if set(roles)!={'test','reference'} or roles['test']['treatment']==roles['reference']['treatment']:return None
    selected=[n for n in group if n['id'] in {i for r in roles.values() for i in r['node_ids']}]
    a=min(n['source_range'][0] for n in selected);b=max(n['source_range'][1] for n in selected)
    if GAP in text[a:b]:return None
    return {'arms':{r['treatment'] for r in roles.values()},'node_ids':[n['id'] for n in selected]}


def _statistic_scope(fact,nodes):
    """A closed printed block overrides the prior column statistic only.

    An estimate with bounds is a two-field bundle only when its own complete
    header explicitly prints GMR and 90% CI. Never split or compute the cell.
    """
    block={r['node_id'] for r in fact['relations'] if r['kind']=='row_block_context'}
    columns={r['node_id'] for r in fact['relations'] if r['kind']=='column_context'}
    if any(a['dimension']=='statistic' and a.get('node_id') in block for a in fact['observations']):
        fact['observations']=[a for a in fact['observations'] if a['dimension']!='statistic' or a.get('node_id') not in columns-block]
    if fact['relationship_status']!='structural_candidate' or fact['value_binding_status']!='whole_cell':return
    number=r'[+−–-]?\d+(?:\.\d+)?'
    if not re.fullmatch(r'\s*'+number+r'\s*\(\s*'+number+r'\s*,\s*'+number+r'\s*\)\s*',fact['cell']['value']):return
    active={a.get('node_id') for a in fact['observations'] if a['dimension']=='statistic'}
    headers=[nodes[n] for n in active if n in nodes and {a['value'] for a in atoms(nodes[n]['value']) if a['dimension']=='statistic'}=={'GMR','CI90'}]
    if len(headers)!=1:return
    header=headers[0]
    fact['compound_statistic_binding']={'roles':['GMR','CI90'],'header_node_id':header['id'],
        'header_quote':header['quote'],'header_source_range':header['source_range'],
        'whole_cell_source_range':fact['cell']['source_range'],'basis':'whole_estimate_bounds_cell_and_own_explicit_header',
        'semantic_approval':False}


def arm(value,product_id=None):
    from rag.evidence.treatment_identity import printed_arm
    printed=printed_arm(value,product_id)
    if printed:return printed
    from rag.retrieval.study_registry import products
    normalized = key(value).replace("-", "")
    if re.fullmatch(r"(?:eu|유럽)(?:(?:approved|licensed|허가|승인))?(?:stelara|스텔라라|대조약)", normalized):
        return "EU-Stelara"
    if re.fullmatch(r"(?:us|미국)(?:(?:approved|licensed|허가|승인))?(?:stelara|스텔라라|대조약)", normalized):
        return "US-Stelara"
    matches = {pid for pid, p in products().items() if normalized in {
        key(n).replace("-", "") for n in [pid, *p["names"], *p.get("ko_names", [])]}}
    return next(iter(matches)) if len(matches) == 1 else value.strip()


def grounded_term(dimension, value, quote):
    if dimension in ("comparison", "arm"):
        return arm(value) == arm(quote)
    if dimension == "study":
        from rag.retrieval.study_registry import load, identifier_key, products
        study = load().get(value)
        if not study:
            return False
        if identifier_key(quote, study["product_id"]) == value:
            return True
        # A model may identify a registered trial from a descriptive Korean query.
        # Validate the typed registry identity and a printed product anchor only;
        # semantic trial selection remains a model candidate, not certification.
        names = [study["product_id"], *products()[study["product_id"]]["names"], *products()[study["product_id"]].get("ko_names", [])]
        return any(re.search(r"(?<![A-Za-z0-9])" + re.escape(n) + r"(?![A-Za-z0-9])", quote, re.I) for n in names)
    if dimension == "weight":
        return key(value) == key(quote)
    return False


def ranges(text):
    pattern = r"\b(?:body\s+)?weight\s*(?:between\s*)?(\d+(?:\.\d+)?)\s*(?:to|and|[~–−-])\s*(\d+(?:\.\d+)?)\s*kg\b|체중\s*(\d+(?:\.\d+)?)\s*(?:[~–−-]|부터)\s*(\d+(?:\.\d+)?)\s*kg"
    return [{"dimension": "weight", "values": [m[1] or m[3], m[2] or m[4]], "unit": "kg",
             "quote": m.group(), "start": m.start(), "end": m.end()} for m in re.finditer(pattern, text, re.I)]


def pairs(text,product_id=None):
    """Only literal slash, ratio-to or vs/between templates over known arm names."""
    from rag.evidence.treatment_identity import mention_pattern
    pattern = mention_pattern(product_id)
    mentions = list(re.finditer(pattern, text, re.I)); result = []
    for first, second in zip(mentions, mentions[1:]):
        middle = text[first.end():second.start()]
        if re.fullmatch(r"\s*/\s*", middle):
            relation = "ordered_pair"
        elif re.fullmatch(r"\s+to\s+", middle, re.I) and re.search(r"\bratio(?:\s+of)?\s*$", text[max(0, first.start()-45):first.start()], re.I):
            relation = "ordered_pair"
        elif re.fullmatch(r"\s+(?:vs\.?|versus|and)\s+", middle, re.I):
            relation = "unordered_pair"
        else:
            continue
        result.append({"dimension": "comparison", "operator": relation, "values": [arm(first.group(),product_id), arm(second.group(),product_id)],
                       "quote": text[first.start():second.end()], "start": first.start(), "end": second.end()})
    return result


def _part(study):
    match = re.search(r"\bpart\s+(\d+)\b", study.get("internal_id", ""), re.I)
    return match[1] if match else None


def scope(text, product, requested, facet, headings=None):
    from rag.retrieval.study_registry import load, finditer
    registry = load(); identifiers = list(finditer(text, product)); present = {k for _, _, k in identifiers}
    records = []
    for target in requested:
        study = registry[target]
        if target in present:
            a, b, _ = next(x for x in identifiers if x[2] == target)
            records.append({"study": target, "status": "explicit_candidate", "basis": "printed_study_identifier", "quote": text[a:b]})
            continue
        parent = study.get("parent"); number = _part(study)
        parents = {k for k in present if registry[k]["kind"] != "study_part"}
        part_mention = re.search(r"\bpart\s+" + re.escape(number or "__missing__") + r"\b", text, re.I)
        if parent in parents and len(parents) == 1 and part_mention and GAP not in text and facet == "design":
            records.append({"study": target, "status": "explicit_candidate", "basis": "parent_design_with_explicit_component", "quote": text})
        elif facet == "assay" and re.search(r"clinical\s+development|all\s+(?:clinical\s+)?studies|both\s+parts", text, re.I):
            records.append({"study": target, "status": "related", "basis": "programme_method_scope_not_trial_assignment", "quote": text})
        else:
            related = any(k == parent for k in present)
            status = "mismatch" if present and not related and not set(requested) & present else "unknown"
            records.append({"study": target, "status": status, "basis": "no_explicit_local_assignment", "quote": None})
    # Heading metadata is retained as a review candidate, not proof of methods or outcomes.
    return {"version": VERSION, "requested": records, "heading_candidates": deepcopy(headings or []), "semantic_approval": False}


def enrich(fact, graph, evidence, requested_studies=None, facet=None):
    if fact is None:
        return None
    fact = deepcopy(fact); table = next(t for t in graph["tables"] if t["id"] == fact["table_id"])
    nodes = {n["id"]: n for n in table["cells"]}; text = evidence["text"]
    _statistic_scope(fact,nodes)
    contexts = [nodes[r["node_id"]] for r in fact["relations"]]
    fact["explicit_relations"] = []; fact["relation_anchors"] = []
    for node in contexts:
        fact["explicit_relations"].extend({**p, "node_id": node["id"], "basis": "own_cell_context", "semantic_approval": False} for p in pairs(node["value"],evidence.get('product_id')) + ranges(node["value"]))
    # Adjacent notes cannot cross a source gap or another table. A marked footnote
    # is tied to its symbol; an unnumbered note must explicitly name the ratio.
    end = table["source_range"][1]; tail = text[end:]
    boundaries = [m.start() for m in re.finditer(r"<table\b|\[UNSUPPLIED SOURCE GAP\]", tail, re.I)]
    tail = tail[:min(boundaries, default=len(tail))]
    markers=set().union(*(_markers(n) for n in contexts))
    ratio_header = any(_ratio_header(n) for n in contexts)
    ci_header = any(a["value"] == "CI90" for n in contexts for a in atoms(n["value"]))
    # A CI with the same printed marker as a ratio is an explicit statistical
    # group only when the local note itself says the CIs are for those ratios.
    ratio_markers=set().union(*(_markers(n) for n in table['cells'] if table['body_start_row'] is not None
        and n['row']<table['body_start_row'] and _ratio_header(n)))
    row_group=_own_row_group(fact,table,text,evidence.get('product_id'))
    for m in re.finditer(r"[^\n]+(?:\n(?!\s*\n)[^\n]+)*", tail):
        note = m.group(); numbered = re.match(r"\s*(\d+|[A-Za-z])\s*[.):]\s*|\s*(\d+)\s+(?=[A-Za-z])", note)
        marker=(numbered[1] or numbered[2]) if numbered else None
        marked = bool(numbered and marker in markers)
        ci_for_ratio = ci_header and bool(markers & ratio_markers) and bool(re.search(r"(?:CIs?|confidence\s+intervals?)\s+for\s+(?:the\s+)?ratios", note, re.I))
        named_ratio = not numbered and (ratio_header or ci_for_ratio) and bool(re.search(r"\bratios?\b", note, re.I)) and bool(re.search(r"\bonly\b|ratio\s+of", note, re.I))
        if not (marked or named_ratio) or ci_header and not ratio_header and not ci_for_ratio:
            continue
        note_pairs = pairs(note,evidence.get('product_id'))
        # A note listing every table comparison does not assign all pairs to
        # every cell. Match the cell's complete physical Test/Reference group.
        if row_group:
            note_pairs=[p for p in note_pairs if set(p['values'])==row_group['arms']]
        elif len({tuple(p['values']) for p in note_pairs})>1:
            note_pairs=[]
        if len({tuple(p['values']) for p in note_pairs})!=1:continue
        if not note_pairs:
            continue
        anchor_id = f"{table['id']}:note:{m.start()}"
        fact["relation_anchors"].append({"id": anchor_id, "source_range": [end+m.start(), end+m.end()], "quote": note,
            "basis": "explicit_footnote_marker" if marked else "adjacent_note_names_ratio", "semantic_approval": False})
        fact["explicit_relations"].extend({**p, "anchor_id": anchor_id, "basis": "local_ratio_note", "semantic_approval": False,
            **({'row_group_node_ids':row_group['node_ids']} if row_group else {})} for p in note_pairs)
        if ratio_header and not ci_header and re.search(r"geometric", note, re.I):
            fact["observations"].append({"dimension": "statistic", "value": "GMR", "basis": "local_note_names_geometric_ratio", "anchor_id": anchor_id, "semantic_status": "unverified"})
    # Printed A/B legends identify the ratio direction, independently of where
    # the data are printed. Only known treatment identities may bind symbols.
    from rag.retrieval.study_registry import products
    known_arms = {*products(), "EU-Stelara", "US-Stelara"}
    definitions = {}
    for definition in re.finditer(r"\b([A-Z])\s*=\s*([^;\n]+)", tail):
        value = arm(definition[2].strip(),evidence.get('product_id'))
        if value in known_arms:
            definitions.setdefault(definition[1], []).append((value, definition))
    formula_nodes = list(contexts)
    if ci_header and any(re.search(r"\bCI\s+of\s+(?:the\s+)?Ratio\b", n["value"], re.I) for n in contexts):
        named = [n for n in table["cells"] if table["body_start_row"] is not None and n["row"] < table["body_start_row"]
                 and re.search(r"\bratio\s+[A-Z]\s*/\s*[A-Z]\b", n["value"], re.I)]
        if len(named) == 1:
            formula_nodes.extend(named)
    for node in formula_nodes:
        formula = re.search(r"\bratio\s+([A-Z])\s*/\s*([A-Z])\b", node["value"], re.I)
        if not formula:
            continue
        symbols = [formula[i].upper() for i in [1, 2]]
        if not all(len({v for v, _ in definitions.get(s, [])}) == 1 for s in symbols):
            continue
        values = [definitions[s][0][0] for s in symbols]; anchor_ids = []
        for symbol in symbols:
            _, definition = definitions[symbol][0]; anchor_id = f"{table['id']}:legend:{definition.start()}"
            fact["relation_anchors"].append({"id": anchor_id, "source_range": [end+definition.start(), end+definition.end()],
                "quote": definition.group(), "basis": "printed_ratio_symbol_definition", "semantic_approval": False})
            anchor_ids.append(anchor_id)
        fact["explicit_relations"].append({"dimension": "comparison", "operator": "ordered_pair", "values": values,
            "node_id": node["id"], "anchor_ids": anchor_ids, "basis": "printed_ratio_formula_and_legend", "semantic_approval": False})
    # A generic reference denominator in a printed ratio header may bind to the
    # uniquely named reference on this value's row, never another row/page.
    references = {arm(n["value"]) for n in contexts if arm(n["value"]) in ("EU-Stelara", "US-Stelara")}
    if len(references) == 1:
        for node in contexts:
            generic = re.fullmatch(r"\s*ratio\s+(?:of\s+)?(.+?)\s*/\s*Stelara\s*", node["value"], re.I)
            if generic and arm(generic[1]) in products():
                fact["explicit_relations"].append({"dimension": "comparison", "operator": "ordered_pair",
                    "values": [arm(generic[1]), next(iter(references))], "node_id": node["id"],
                    "reference_node_ids": [n["id"] for n in contexts if arm(n["value"]) in references],
                    "basis": "printed_generic_ratio_with_own_row_reference", "semantic_approval": False})
    # Ratio estimates are ratios of geometric means only for an explicit table
    # template with geometric mean headers. Do not treat a CI as another GMR.
    geometric_headers = [n for n in table["cells"] if table["body_start_row"] is not None and n["row"] < table["body_start_row"]
                         and re.search(r"geometric\s+(?:LS\s*)?mean|geo[ -]?LS\s*mean", n["value"], re.I)]
    if ratio_header and not ci_header and geometric_headers:
        fact["observations"].append({"dimension": "statistic", "value": "GMR", "basis": "printed_ratio_and_geometric_mean_table_template",
            "node_ids": [n["id"] for n in geometric_headers], "semantic_status": "unverified"})
    fact["ratio_statistic"] = bool(ratio_header or any(a["value"] == "GMR" for a in fact["observations"])
                                   or ci_header and any(a["basis"] == "adjacent_note_names_ratio" for a in fact["relation_anchors"]))
    # A table caption is actual local source text, unlike inherited page metadata.
    start = table["source_range"][0]
    previous = max((t["source_range"][1] for t in graph["tables"] if t["source_range"][1] <= start), default=0)
    prefix = text[previous:start]
    prefix_base = previous
    if GAP in prefix:
        prefix_base += prefix.rfind(GAP) + len(GAP)
        prefix = prefix.split(GAP)[-1]
    captions = list(re.finditer(r"(?im)^Table\s+\d+[^\n]*(?:\n[^\n]+)*", prefix))
    if captions:
        caption = captions[-1]; anchor_id = f"{table['id']}:caption"
        fact["relation_anchors"].append({"id": anchor_id, "source_range": [prefix_base+caption.start(), prefix_base+caption.end()],
            "quote": caption.group(), "basis": "printed_local_table_caption", "semantic_approval": False})
        fact["observations"].extend({**a, "basis": "printed_local_table_caption", "anchor_id": anchor_id, "semantic_status": "unverified"}
            for a in atoms(caption.group()) if a["dimension"] in ("analysis_model", "analysis_timing", "analysis_role", "timepoint", "endpoint", "population", "route", "covariate"))
    local = "\n".join(n["value"] for n in contexts)
    scope_text = prefix + "\n" + local
    if requested_studies:
        fact["scope_audit"] = scope(scope_text, evidence.get("product_id"), requested_studies, facet,
                                    evidence.get("structure", {}).get("heading_anchors", []))
        for record in fact["scope_audit"]["requested"]:
            if record["status"] == "explicit_candidate":
                fact["observations"].append({"dimension": "study", "value": record["study"], "basis": record["basis"], "semantic_status": "unverified"})
    return fact


def attach_comparison(item,candidate,evidence,graph=None):
    """Attach literal own-pair proof before checking missing conditions."""
    block=evidence[candidate['evidence']];graph=graph or build(block)
    fact=table_fact(candidate,evidence,graph=graph)
    if not fact or fact['relationship_status']!='structural_candidate' or fact['value_binding_status']!='whole_cell' or not fact.get('ratio_statistic'):return
    pairs=[p for p in fact['explicit_relations'] if p['dimension']=='comparison']
    if len({tuple(p['values']) for p in pairs})!=1:return
    table=next(t for t in graph['tables'] if t['id']==fact['table_id']);nodes={n['id']:n for n in table['cells']}
    anchors={a['id']:a for a in fact['relation_anchors']}
    quotes=[];proof_nodes=[]
    for p in pairs:
        for node in [p.get('node_id'),*p.get('row_group_node_ids',[]),*p.get('reference_node_ids',[])]:
            if node in nodes:
                proof_nodes.append({'node_id':node,'quote':nodes[node]['quote'],'source_range':nodes[node]['source_range'],'evidence':candidate['evidence']})
        if p.get('node_id') in nodes:quotes.append(nodes[p['node_id']]['quote'])
        if p.get('anchor_id') in anchors and p.get('quote'):
            # Retain the full note in the audit, but display only this cell's
            # literal pair. Other comparisons in the same note are not its
            # conditions. Offsets below remain relative to the source note.
            anchor=anchors[p['anchor_id']]
            a,b=p.get('start'),p.get('end')
            if isinstance(a,int) and isinstance(b,int) and anchor['quote'][a:b]==p['quote']:
                quotes.append(p['quote'])
        for anchor in p.get('anchor_ids',[]):
            if anchor in anchors:quotes.append(anchors[anchor]['quote'])
    if not quotes:return
    from rag.evidence.table_evidence import cells
    from rag.evidence.evidence import normalize
    removed=[deepcopy(c) for c in item['conditions'] if c['dimension']=='comparison']
    item['conditions']=[c for c in item['conditions'] if c['dimension']!='comparison']
    for quote in dict.fromkeys(quotes):
        parsed=cells(quote);value=parsed[0]['value'] if len(parsed)==1 else normalize(quote)
        condition={'dimension':'comparison','value':value,'quote':quote,'evidence':candidate['evidence']}
        if condition not in item['conditions']:item['conditions'].append(condition)
    # Endpoint labels are copied only from this value's linked source nodes.
    # A model label is audit history, never proof that Cmax is AUCinf.
    endpoints={a['value'] for a in fact['observations'] if a['dimension']=='endpoint'}
    if len(endpoints)==1:
        endpoint_nodes={a['node_id'] for a in fact['observations'] if a['dimension']=='endpoint' and a.get('node_id') in nodes}
        removed.extend(deepcopy(c) for c in item['conditions'] if c['dimension']=='endpoint')
        item['conditions']=[c for c in item['conditions'] if c['dimension']!='endpoint']
        for node in sorted(endpoint_nodes):
            n=nodes[node];item['conditions'].append({'dimension':'endpoint','value':n['value'],'quote':n['quote'],'evidence':candidate['evidence']})
        stats={a['value'] for a in fact['observations'] if a['dimension']=='statistic'} & {'GMR','CI90'}
        if stats:
            statistic_nodes={r['node_id'] for r in fact['relations'] if _ratio_header(nodes[r['node_id']]) or
                any(a['dimension']=='statistic' and a['value'] in stats for a in atoms(nodes[r['node_id']]['value']))}
            if statistic_nodes:
                removed.extend(deepcopy(c) for c in item['conditions'] if c['dimension']=='statistic')
                item['conditions']=[c for c in item['conditions'] if c['dimension']!='statistic']
                for node in sorted(statistic_nodes):
                    n=nodes[node];item['conditions'].append({'dimension':'statistic','value':n['value'],'quote':n['quote'],'evidence':candidate['evidence']})
            item.setdefault('model_source_label',item['source_label'])
            pair_quote=next((p['quote'] for p in pairs if p.get('quote')),None)
            item['source_label']=' · '.join([next(iter(endpoints)),' / '.join(sorted(stats)),*([pair_quote] if pair_quote else [])])
            if 'item' in item:
                item.setdefault('model_item',item['item'])
                item['item']=item['source_label']
    item['removed_unbound_ratio_conditions']=removed
    captions=[a for a in fact['relation_anchors'] if a['basis']=='printed_local_table_caption' and
        re.search(r'statistical\s+(?:assessment|analysis|comparison)|\bANCOVA\b|\bANOVA\b',a['quote'],re.I)]
    if len(captions)==1:
        caption=captions[0]
        item['removed_unbound_ratio_conditions'].extend(deepcopy(c) for c in item['conditions'] if c['dimension']=='analysis')
        item['conditions']=[c for c in item['conditions'] if c['dimension']!='analysis']
        item['conditions'].append({'dimension':'analysis','value':caption['quote'],'quote':caption['quote'],'evidence':candidate['evidence']})
        item['own_analysis_binding']={'quote':caption['quote'],'source_range':caption['source_range'],'evidence':candidate['evidence'],
            'basis':'literal_own_table_analysis_caption','primary_status':'not_inferred','semantic_approval':False}
    item['own_comparison_binding']={'version':VERSION,'pairs':[list(p) for p in sorted({tuple(p['values']) for p in pairs})],
        'source_node_proof':list({p['node_id']:p for p in proof_nodes}.values()),
        'basis':'own_table_context_and_uniquely_applicable_note','semantic_approval':False}


def table_fact(candidate, evidence, requested_studies=None, facet=None, graph=None):
    block = evidence[candidate["evidence"]]; graph = graph or build(block)
    fact = selected_fact(graph, [candidate["start"], candidate["end"]])
    return enrich(fact, graph, block, requested_studies, facet)


def prose_fact(candidate, evidence, requested_studies=None, facet=None):
    block = evidence[candidate["evidence"]]; text = candidate["quote"]
    if candidate["kind"] not in ("source_paragraph", "source_line") or GAP in text:
        return None
    local_scope = scope(text, block.get("product_id"), requested_studies or [], facet, block.get("structure", {}).get("heading_anchors", []))
    observations = [{**a, "basis": "contiguous_source_text", "semantic_status": "unverified"} for a in atoms(text)]
    from rag.evidence.prose_relation_scope import linked,measure_bundle
    antecedent=linked(candidate,block,requested_studies or [])
    if antecedent:
        for record in local_scope['requested']:
            if record['status']=='unknown' and record['study']==antecedent['proof']['study']:
                record.update(status='explicit_candidate',basis=antecedent['proof']['basis'],
                              quote=antecedent['proof']['quote'],source_range=antecedent['proof']['source_range'])
        observations.extend(antecedent['observations'])
    observations.extend({"dimension": "study", "value": r["study"], "basis": r["basis"], "semantic_status": "unverified"}
                        for r in local_scope["requested"] if r["status"] == "explicit_candidate")
    return {"relationship_status": "structural_candidate", "observations": observations,
            "explicit_relations": [{**p, "basis": "contiguous_source_text", "semantic_approval": False} for p in pairs(text,block.get('product_id')) + ranges(text)],
            "scope_audit": local_scope, "value_binding_status": "whole_source_text", "source_quote":text,
            "printed_antecedent":antecedent['proof'] if antecedent else None,
            "compound_statistic_binding":measure_bundle(text),
            "semantic_approval": False}


def relation_condition(fact, dimension, operator, values):
    if fact is None or fact["relationship_status"] != "structural_candidate":
        return {"dimension": dimension, "status": "unknown", "reason": "unresolved_source_relationship"}
    relevant = [p for p in fact.get("explicit_relations", []) if p["dimension"] == dimension]
    expected = [arm(v) for v in values] if dimension == "comparison" else values
    matches = []; mismatches = []
    for relation in relevant:
        actual = relation["values"]
        if operator == "range":
            same = [key(x) for x in actual] == [key(x) for x in expected]
        elif operator == "ordered_pair":
            if relation.get("operator") != "ordered_pair":
                continue
            same = actual == expected
        elif operator == "unordered_pair":
            same = set(actual) == set(expected)
        else:
            continue
        (matches if same else mismatches).append(relation)
    status = "match" if matches and not mismatches else "unknown" if matches or not mismatches else "mismatch"
    return {"dimension": dimension, "operator": operator, "expected": expected, "observed": relevant, "status": status,
            "reason": "explicit_local_relationship" if status == "match" else "different_local_relationship" if status == "mismatch" else "relationship_not_uniquely_resolved"}
