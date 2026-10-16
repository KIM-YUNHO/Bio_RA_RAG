"""Keep a table value's exact row/column proof inside its own review boundary."""
from copy import deepcopy
from functools import lru_cache
from decimal import Decimal
import re

from rag import ra_spans, ra_quantities
from rag.table_evidence import cells

VERSION = 'ra-table-citation-bindings-1'
STATISTIC = re.compile(r'산술평균|기하\s*평균|표준편차|중앙값|평균|범위|\bgeometric\s+mean\b|\bmean\b|\bmedian\b|\brange\b|\bSD\b', re.I)


def group_label(text):
    return bool(re.search(r'[A-Za-z가-힣]{2}', text) and len(text) <= 80
        and not ra_quantities.quantities(text)
        and not re.search(r'parameter|statistics|mean|median|range|\bSD\b|\bGM\b|Geo|Cmax|Tmax|AUC|t1/2|'
            r'treatment|dosage|subjects|study|\bPK\b|objective|design|duration|number|route|healthy|weight|BMI', text, re.I))


def group_key(text):
    return re.sub(r'[\s-]', '', text).casefold()


def statistic_role(text):
    text = text.casefold()
    if '표준편차' in text or text == 'sd': return 'sd'
    if '중앙값' in text or 'median' in text: return 'median'
    if '기하' in text or 'geometric' in text: return 'geometric_mean'
    if '범위' in text or 'range' in text: return 'range'
    return 'mean'


ENDPOINT = re.compile(r'Cmax|Tmax|t1/2|AUC(?:inf|last)?',re.I)


def group_mentions(text, groups):
    found=[]
    for name in groups:
        pattern=r'(?<![A-Za-z0-9])'+r'[\s-]*'.join(re.escape(p) for p in name.split())+r'(?![A-Za-z0-9]|-\d)'
        found.extend((m.start(),m.end(),name) for m in re.finditer(pattern,text,re.I))
        regional=re.fullmatch(r'(.+?)\s*\((EU|US)\)',name,re.I)
        if regional:
            alternate=r'(?<![A-Za-z0-9])'+regional[2]+r'\s+'+re.escape(regional[1])+r'(?![A-Za-z0-9])'
            found.extend((m.start(),m.end(),name) for m in re.finditer(alternate,text,re.I))
    return sorted(set(found))


def assertions(text, groups):
    """Literal group/role/value links; explicit respectively lists retain order."""
    text=ra_quantities.normalized(text)
    mentions=group_mentions(text,groups)
    markers=list(STATISTIC.finditer(text));endpoints=list(ENDPOINT.finditer(text))
    records=[]
    explicit=list(ra_quantities.QUANTITY.finditer(text))
    for m in explicit:
        canonical=ra_quantities.numeric_token(m[1])
        if re.fullmatch(r'[+-]?\d+(?:\.\d+)?',canonical):
            records.append({'start':m.start(),'end':m.end(),'value':Decimal(canonical),'unit':ra_quantities.unit_alias(m[2])})
    shared=ra_quantities.units(text)
    if len(shared)==1 and markers:
        for m in ra_quantities.NUMBER.finditer(text):
            if (any(a<=m.start()<b for a,b,_ in mentions) or
                    any(e.start()<=m.start()<e.end() for e in endpoints) or
                    any(v['start']<=m.start()<v['end'] for v in records) or
                    not any(b<=m.start() for _,b,_ in mentions)):
                continue
            canonical=ra_quantities.numeric_token(m[0])
            if re.fullmatch(r'[+-]?\d+(?:\.\d+)?',canonical):
                records.append({'start':m.start(),'end':m.end(),'value':Decimal(canonical),'unit':shared[0]})
    records.sort(key=lambda v:v['start'])
    assigned={}
    for index,marker in enumerate(markers):
        stop=markers[index+1].start() if index+1<len(markers) else len(text)
        values=[v for v in records if marker.end()<=v['start']<stop]
        preceding=list(dict.fromkeys(name for a,_,name in mentions if a<marker.start()))
        if (values and re.search(r'각각|respectively',text[marker.end():values[0]['start']],re.I)
                and len(values)==len(preceding) and
                not any(marker.end()<=a<stop for a,_,_ in mentions)):
            assigned.update({v['start']:name for v,name in zip(values,preceding)})
    result=[]
    for value in records:
        prior=[name for _,end,name in mentions if end<=value['start']]
        group=assigned.get(value['start']) or (prior[-1] if prior else None)
        role=next((statistic_role(m[0]) for m in reversed(markers) if m.end()<=value['start']),None)
        compound=list(re.finditer(r'(중앙값|평균|Median|Mean)\s*\((범위|SD|표준편차|range)\)',text[:value['start']],re.I))
        if compound and not any(compound[-1].end()<m.start()<value['start'] for m in markers):
            previous=[v for v in records if compound[-1].end()<=v['start']<value['start'] and
                      not any(v['end']<a<value['start'] for a,_,_ in mentions)]
            role=statistic_role(compound[-1][2] if previous else compound[-1][1])
        endpoint=next((m[0] for m in reversed(endpoints) if m.end()<=value['start']),None)
        if group:result.append({**value,'group':group,'role':role,'endpoint':endpoint})
    return result


def binding_quantities(alias, relation, spans, *, role=None, endpoint=None):
    header=ra_quantities.normalized('\n'.join(spans[a]['quote'] for a in
        relation['column_header_span_ids']+relation['row_label_span_ids']))
    compact=lambda s:re.sub(r'[^a-z0-9/]', '',s.casefold())
    if endpoint and compact(endpoint) not in compact(header):return set()
    printed_units=ra_quantities.units(header)
    raw=ra_quantities.normalized(spans[alias]['quote']).strip()
    # A cell may print its own percentage with the counts behind it: "16.2% (18/111)".
    printed=re.fullmatch(r'([+−-]?\d+(?:\.\d+)?)\s*%\s*(?:\(\s*\d+\s*/\s*\d+\s*\))?',raw)
    if printed:
        return set() if role is not None else {(Decimal(ra_quantities.numeric_token(printed[1])),'%')}
    if not re.fullmatch(r'[\d\s.,+−\-–()±]+',raw):return set()
    roles=[]
    if re.search(r'\bMedian\b',header,re.I):roles=['median','range']
    elif re.search(r'\bGM\b|geometric\s+mean',header,re.I):roles=['geometric_mean','geo_cv']
    elif re.search(r'\bMean\b',header,re.I):roles=['mean','sd'] if re.search(r'\bSD\b',header,re.I) else ['mean']
    elif re.search(r'\bSD\b',header,re.I):roles=['sd']
    values=ra_quantities.NUMBER.findall(raw)
    if len(printed_units)==1:units=printed_units*len(values)
    elif len(printed_units)==2 and re.fullmatch(r'[+−-]?\d[\d.,]*\s*\([+−-]?\d[\d.,]*\)',raw):units=printed_units
    else:return set()
    result=set()
    for n,(value,unit) in enumerate(zip(values,units)):
        canonical=ra_quantities.numeric_token(value)
        assigned=roles[min(n,len(roles)-1)] if roles else None
        if re.fullmatch(r'[+-]?\d+(?:\.\d+)?',canonical) and (role is None or role==assigned):
            result.add((Decimal(canonical),unit))
    return result


@lru_cache(maxsize=64)
def layouts(text):
    result = []
    for match in re.finditer(r'<table\b[^>]*>.*?</table\s*>', text, re.I | re.S):
        raw = match[0]
        if len(re.findall(r'<table\b', raw, re.I)) != 1:
            continue
        parsed = cells(raw)
        if not parsed:
            continue
        occupied, placed = {}, []
        valid = True
        for cell in parsed:
            try:
                height, width = int(cell['rowspan']), int(cell['colspan'])
            except ValueError:
                valid = False
                break
            if not (1 <= height <= 100 and 1 <= width <= 40):
                valid = False
                break
            row, column = cell['row'], 0
            while (row, column) in occupied:
                column += 1
            slots = [(r, c) for r in range(row, row + height)
                     for c in range(column, column + width)]
            if any(slot in occupied for slot in slots):
                valid = False
                break
            value = {**cell, 'column': column, 'width': width, 'height': height,
                     'start': match.start() + cell['start'], 'end': match.start() + cell['end']}
            placed.append(value)
            occupied.update({slot: value for slot in slots})
        if not valid:
            continue
        rows = sorted({c['row'] for c in placed})
        widths = [{col for row, col in occupied if row == r} for r in rows]
        # Ragged cells are not a verified grid. Do not guess missing columns.
        if not widths or any(w != set(range(len(widths[0]))) for w in widths):
            continue
        data_rows = [c['row'] for c in placed if c['tag'] == 'td'
                     and re.match(r'[\s(±+−-]*\d', c['value'])]
        first_data = min(data_rows) if data_rows else 1
        headers = [c for c in placed if c['row'] < first_data
                   and (c['tag'] == 'th' or c['row'] == rows[0])]
        # Clinical summary tables sometimes use td for their header band.
        if not headers or not any(re.search(r'[A-Za-z가-힣]', c['value']) for c in headers):
            continue
        result.append({'start': match.start(), 'end': match.end(), 'cells': placed,
                       'grid': occupied, 'headers': headers, 'first_data': first_data})
    return tuple(result)


def bindings(evidence, spans=None):
    """Relationships come from one closed HTML table, never another packet."""
    spans = spans or ra_spans.catalog(evidence)[0]
    by_range = {(s['evidence'], s['start'], s['end']): alias for alias, s in spans.items()}
    result = {}
    for key, block in evidence.items():
        for table in layouts(block['text']):
            caption = [alias for alias, s in spans.items() if s['evidence'] == key
                       and s['kind'] == 'table_context' and s['end'] <= table['start']
                       and table['start'] - s['end'] < 300 and len(s['quote']) <= 1200
                       and re.search(r'\bTable\s+\d|표\s*\d', s['quote'], re.I)]
            caption = sorted(caption, key=lambda a: spans[a]['end'], reverse=True)[:1]
            for cell in table['cells']:
                if (cell['row'] < table['first_data'] or cell['tag'] != 'td' or not (
                        re.match(r'[\s(±+−-]*\d',cell['value']) or ra_quantities.quantities(cell['value']))):
                    continue
                alias = by_range.get((key, cell['start'], cell['end']))
                if not alias:
                    continue
                columns = [c for c in table['headers'] if
                           c['column'] <= cell['column'] < c['column'] + c['width']]
                row_labels = []
                for column in range(cell['column']):
                    label = table['grid'].get((cell['row'], column))
                    if label and (column == 0 or not ra_quantities.numbers(label['value'])):
                        row_labels.append(label)
                # A literal study-ID column is row identity, not a neighboring result.
                for header in table['headers']:
                    if re.search(r'\b(?:study|trial)\s*(?:identifier|ID)\b', header['value'], re.I):
                        label = table['grid'].get((cell['row'], header['column']))
                        if label and label != cell:
                            row_labels.extend([header, label])
                ids = lambda rows: list(dict.fromkeys(by_range[(key, c['start'], c['end'])]
                    for c in rows if (key, c['start'], c['end']) in by_range))
                column_ids, row_ids = ids(columns), ids(row_labels)
                required = list(dict.fromkeys([*caption, *column_ids, *row_ids]))
                if column_ids and required:
                    groups = [c['value'] for c in columns + row_labels if group_label(c['value'])]
                    result[alias] = {'evidence': key, 'table_range': [table['start'], table['end']],
                        'row': cell['row'], 'column': cell['column'],
                        'column_header_span_ids': column_ids, 'row_label_span_ids': row_ids,
                        'caption_span_ids': caption, 'required_span_ids': required,
                        'group_labels': list(dict.fromkeys(groups))}
    return result


def complete(claims, evidence, client=None):
    """Complete exact asserted cells in an already cited table, before review."""
    spans, _ = ra_spans.catalog(evidence)
    relations = bindings(evidence, spans)
    ready, changes, tokens = [], [], 0
    for claim in claims:
        selected = list(dict.fromkeys(s['citation_alias'] for s in claim['sources']))
        tables={(relations[a]['evidence'],tuple(relations[a]['table_range'])) for a in selected if a in relations}
        candidates={a:r for a,r in relations.items() if (r['evidence'],tuple(r['table_range'])) in tables}
        groups={name for r in candidates.values() for name in r['group_labels']}
        added_values=[]
        for asserted in assertions(claim['text'],groups):
            if not asserted['role'] or not asserted['endpoint']:continue
            matches=[a for a,r in candidates.items() if group_key(asserted['group']) in
                {group_key(name) for name in r['group_labels']} and
                (asserted['value'],asserted['unit']) in binding_quantities(a,r,spans,
                    role=asserted['role'],endpoint=asserted['endpoint'])]
            if len(matches)!=1 or matches[0] in selected:continue
            relation=candidates[matches[0]]
            if not any(a in selected for a in relation['column_header_span_ids']+relation['row_label_span_ids']):continue
            if len(added_values)>=4:break
            selected.append(matches[0]);added_values.append(matches[0])
        required = list(dict.fromkeys(a for seed in selected for a in
            relations.get(seed, {}).get('required_span_ids', [])))
        added = [a for a in required if a not in selected]
        additions=[*added_values,*added]
        cost = sum((client.tokenizer.one(spans[a]['quote']) if client and hasattr(client, 'tokenizer')
                    else (len(spans[a]['quote']) + 3) // 4) + 40 for a in additions)
        if additions and len(selected) + len(added) <= 40 and tokens + cost <= 1800:
            claim = ra_spans.resolve({'claims': [{**claim, 'citation_span_ids': selected + added}],
                                     'gaps': []}, evidence, spans)['claims'][0]
            tokens += cost
            changes.append({'claim_id': claim['id'], 'added_span_ids': additions,'added_value_span_ids':added_values,
                'reason': 'exact_requested_table_cell_and_structural_proof' if added_values else
                          'selected_table_value_requires_row_column_identity',
                'boundary': 'same_supplied_table_exact_source_spans', 'reviewed_after_completion': True})
        ready.append(claim)
    return ready, changes


def retain(final, original, relations):
    """Retain structural spans already supplied to THIS proposal's review."""
    retained = [a for seed in final for a in relations.get(seed, {}).get('required_span_ids', [])
                if a in original and a not in final]
    retained = list(dict.fromkeys(retained))
    return list(dict.fromkeys([*final, *retained])), retained


def check(claim, evidence, relations=None):
    relations = relations if relations is not None else bindings(evidence)
    own = {s['citation_alias'] for s in claim['sources']}
    for alias in own:
        if not set(relations.get(alias, {}).get('required_span_ids', [])) <= own:
            return 'table_value_row_column_context_not_in_own_citations'
    tables = {(relations[a]['evidence'],tuple(relations[a]['table_range'])) for a in own if a in relations}
    groups = {name for binding in relations.values()
              if (binding['evidence'],tuple(binding['table_range'])) in tables for name in binding['group_labels']}
    for asserted in assertions(claim['text'],groups):
        pair={(asserted['value'],asserted['unit'])}
        if not pair <= own_quantities(claim,evidence,group=asserted['group'],endpoint=asserted['endpoint']):
            return 'table_quantity_not_in_named_group_citations'
        if asserted['role'] and not pair <= own_quantities(claim,evidence,group=asserted['group'],
                role=asserted['role'],endpoint=asserted['endpoint']):
            return 'table_quantity_not_in_asserted_statistic_citations'
    return None


def selected_metadata(alias, own, relations):
    relation = relations.get(alias)
    if relation and set(relation['required_span_ids']) <= set(own):
        return {'table_binding': {k: deepcopy(v) for k, v in relation.items() if k != 'required_span_ids'}}
    return {}


def own_quantities(claim, evidence, product=None, *, group=None, role=None, endpoint=None):
    """A value gets units only from its selected, structurally bound headers."""
    if (not all(s.get('citation_alias') for s in claim['sources'])
            or not any('<table' in b.get('text', '').lower() for b in evidence.values())):
        return set()
    spans, _ = ra_spans.catalog(evidence)
    relations = bindings(evidence, spans)
    own = {s['citation_alias'] for s in claim['sources'] if product is None
           or evidence[s['evidence']].get('product_id') == product}
    result = set()
    for alias in own:
        relation = relations.get(alias)
        if not relation or not set(relation['required_span_ids']) <= own:
            continue
        if group and group_key(group) not in {group_key(name) for name in relation['group_labels']}:
            continue
        result.update(binding_quantities(alias,relation,spans,role=role,endpoint=endpoint))
    return result
