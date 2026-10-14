"""Bounded citation completion BEFORE the selected-source semantic review."""
from copy import deepcopy
import re

from rag import ra_quantities, ra_source_constraints, ra_spans
from rag.ra_requirements import verified_study_key

VERSION = 'ra-supplied-source-citation-completion-2-table-context'
REPAIRABLE = {'claim_population_not_in_selected_sources', 'single_dose_qualifier_not_in_selected_sources',
    'comparator_identity_not_in_selected_sources', 'comparator_approval_not_in_selected_sources',
    'claim_route_not_in_selected_sources', 'design_cardinality_not_selected',
    'claim_pk_study_not_in_selected_sources'}


def study_identity_spans(claim,evidence,spans):
    """Literal source headings for requested registered identities, no clinical facts."""
    from rag import study_registry
    normalize=lambda s:re.sub(r'\s+',' ',s).strip().casefold()
    result=[]
    for product in claim['products']:
        identity_text=re.sub(r'(?<=[A-Za-z0-9])(?=[가-힣])',' ',claim['text'])
        requested={study_registry.top(k) for _,_,k in study_registry.finditer(identity_text,product)}
        seeds={verified_study_key(evidence[s['evidence']]) for s in claim['sources']
               if evidence[s['evidence']].get('product_id')==product}
        seeds.discard(None)
        for alias,span in spans.items():
            block=evidence[span['evidence']];key=verified_study_key(block)
            if (block.get('product_id')!=product or key not in seeds or
                    study_registry.top(key[-1]) not in requested):continue
            titles=[a.get('title','') for a in block.get('structure',{}).get('heading_anchors',[])
                    if a.get('kind')=='study' and a.get('heading_location',{}).get('block_id')]
            if normalize(span['quote']) in {normalize(t) for t in titles}:
                result.append(alias)
    return result


def complete(claims, evidence, client=None):
    """Add at most two supplied spans per claim; no new search or document scope."""
    from rag.ra_table_citations import complete as complete_tables
    claims, table_changes = complete_tables(claims, evidence, client)
    spans, _ = ra_spans.catalog(evidence)
    ready, changes = [], []
    added_tokens = 0
    def cost(alias):
        quote = spans[alias]['quote']
        return (client.tokenizer.one(quote) if client and hasattr(client, 'tokenizer') else (len(quote) + 3) // 4) + 80
    for original in claims:
        item = deepcopy(original)
        selected = list(dict.fromkeys(s['citation_alias'] for s in item['sources']))
        seed_blocks = [evidence[s['evidence']] for s in item['sources']]
        own='\n'.join(s['quote'] for s in item['sources'])
        for alias in study_identity_spans(item,evidence,spans):
            if alias in selected or added_tokens+cost(alias)>900 or len(selected)-len(original['sources'])>=2:continue
            identities=ra_quantities.numbers(spans[alias]['quote']) & ra_quantities.numbers(item['text'])
            if not identities-ra_quantities.numbers(own):continue
            item=ra_spans.resolve({'claims':[{**item,'citation_span_ids':selected+[alias]}],'gaps':[]},evidence,spans)['claims'][0]
            selected.append(alias);added_tokens+=cost(alias);own+='\n'+spans[alias]['quote']
            changes.append({'claim_id':item['id'],'added_span_id':alias,'reason':'literal_study_heading_not_selected',
                'boundary':'same_supplied_document_version_verified_study','reviewed_after_completion':True})
        # The Part number identifies the objective; it is not an invented
        # clinical value. Select its literal title from the SAME body block,
        # then review title and body together. Do not infer it from metadata.
        parts=set(re.findall(r'(?<![A-Za-z0-9])Part\s*(\d+)(?![A-Za-z0-9])',item['text'],re.I))
        own='\n'.join(s['quote'] for s in item['sources'])
        if len(parts)==1 and not re.search(r'(?<![A-Za-z0-9])Part\s*'+next(iter(parts))+r'(?![A-Za-z0-9])',own,re.I):
            part=next(iter(parts))
            for alias,span in spans.items():
                if alias in selected or span['evidence'] not in {s['evidence'] for s in item['sources']}:
                    continue
                if not re.fullmatch(r'\s*(?:Primary|Secondary)\s+objectives?\s*\(Part\s*'+part+r'\)\s*:\s*',span['quote'],re.I):
                    continue
                starts=[s['source_range'][0] for s in item['sources'] if s['evidence']==span['evidence']]
                preceding=list(re.finditer(r'(?:Primary|Secondary)\s+objectives?\s*\(Part\s*(\d+)\)\s*:',
                    evidence[span['evidence']]['text'][:min(starts)],re.I))
                if (span['end']>min(starts) or not preceding or preceding[-1][1]!=part
                        or added_tokens+cost(alias)>900):
                    continue
                bound=ra_spans.resolve({'claims':[{**item,'citation_span_ids':selected+[alias]}],'gaps':[]},evidence,spans)['claims'][0]
                if ra_quantities.check(bound,evidence):
                    continue
                item=bound;selected.append(alias);added_tokens+=cost(alias)
                changes.append({'claim_id':item['id'],'added_span_id':alias,
                    'reason':'literal_part_objective_title_not_selected','boundary':'same_supplied_body_block',
                    'reviewed_after_completion':True})
                break
        if len(item['products']) == 1:
            seeds = {verified_study_key(b) for b in seed_blocks if b.get('product_id') == item['products'][0]}
            seeds.discard(None)
            reason = ra_source_constraints.check(item, evidence)
            # A design's explicit arm count can need its actual design sentence,
            # not just a selected population sentence. Never repair an arbitrary
            # clinical quantity, sample count, date or result from another span.
            own = '\n'.join(s['quote'] for s in item['sources'])
            missing = ra_quantities.numbers(ra_quantities.substantive_text(item, evidence)) - ra_quantities.numbers(own)
            arm_counts = set(re.findall(r'(\d+)\s*(?:군|개\s*군|arms?|groups?)', item['text'], re.I))
            if (not reason and missing and missing <= arm_counts
                    and ra_quantities.quantities(item['text']) <= ra_quantities.quantities(own)):
                reason = 'design_cardinality_not_selected'
            if len(seeds) == 1 and reason in REPAIRABLE:
                candidates = [alias for alias, span in spans.items() if alias not in selected
                    and evidence[span['evidence']].get('product_id') == item['products'][0]
                    and verified_study_key(evidence[span['evidence']]) in seeds
                    and len(span['quote']) <= 6000]
                for _ in range(2-len(selected)+len(original['sources'])):
                    matched = None
                    for alias in sorted(candidates, key=lambda a: len(spans[a]['quote'])):
                        if added_tokens + cost(alias) > 900:
                            continue
                        if reason == 'design_cardinality_not_selected' and not re.search(
                                r'\b(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+)[ -]*(?:treatment\s+)?(?:arms?|groups?)\b',
                                spans[alias]['quote'], re.I):
                            continue
                        proposal = {**item, 'citation_span_ids': selected + [alias]}
                        bound = ra_spans.resolve({'claims': [proposal], 'gaps': []}, evidence, spans)['claims'][0]
                        # Never repair a number from a new paragraph or another regimen.
                        if ra_quantities.check(bound, evidence):
                            continue
                        after = ra_source_constraints.check(bound, evidence)
                        if after != reason:
                            matched = (alias, bound, after)
                            break
                    if matched is None:
                        break
                    alias, item, after = matched
                    selected.append(alias); candidates.remove(alias)
                    added_tokens += cost(alias)
                    changes.append({'claim_id': item['id'], 'added_span_id': alias,
                        'reason': reason, 'boundary': 'same_supplied_document_version_verified_study',
                        'reviewed_after_completion': True})
                    reason = after
                    if reason not in REPAIRABLE:
                        break
        elif len(item['products']) > 1:
            # Repair an OCR-damaged comparator name from this product's SAME
            # supplied verified trial before review, never from the other side.
            reason = ra_source_constraints.check(item, evidence)
            if reason == 'comparator_identity_not_in_selected_sources':
                for product in item['products']:
                    own = '\n'.join(s['quote'] for s in item['sources'] if evidence[s['evidence']].get('product_id') == product)
                    if re.search(r'ustekinumab|Stelara|스텔라라', own, re.I):
                        continue
                    seeds = {verified_study_key(b) for b in seed_blocks if b.get('product_id') == product}
                    seeds.discard(None)
                    if len(seeds) != 1:
                        continue
                    for alias, span in sorted(spans.items(), key=lambda pair:len(pair[1]['quote'])):
                        block = evidence[span['evidence']]
                        if (alias in selected or block.get('product_id') != product or verified_study_key(block) not in seeds
                                or not re.search(r'ustekinumab|Stelara', span['quote'], re.I)
                                or len(span['quote']) > 6000 or added_tokens + cost(alias) > 900):
                            continue
                        bound = ra_spans.resolve({'claims':[{**item,'citation_span_ids':selected+[alias]}],'gaps':[]},evidence,spans)['claims'][0]
                        if ra_quantities.check(bound,evidence) or ra_source_constraints.check(bound,evidence) == reason:
                            continue
                        item=bound;selected.append(alias);added_tokens+=cost(alias)
                        changes.append({'claim_id':item['id'],'added_span_id':alias,'reason':reason,
                            'boundary':'same_product_supplied_document_version_verified_study','reviewed_after_completion':True})
                        break
                    if len(selected)-len(original['sources']) >= 2:
                        break
        elif not item['products'] and re.search(r'적용|범위|원칙|설명|다룬|요구사항', item['text']):
            # A named guideline's date does not prove scope. Supply at most one
            # explicit scope paragraph from that SAME supplied document/version.
            identities = {(b['document_id'], b['version_id']) for b in seed_blocks}
            normalize = lambda text: re.sub(r'\s+', '', text).casefold()
            named = {(b['document_id'], b['version_id']) for b in evidence.values()
                     if b.get('title') and normalize(b['title']) in normalize(item['text'])}
            if len(identities) == 1 and named == identities:
                cues = [(r'품질', r'quality'), (r'일반\s*원칙', r'general principles'),
                        (r'맞춤형\s*임상', r'tailored clinical')]
                needed = [source for phrase, source in cues if re.search(phrase, item['text'], re.I)]
                own = '\n'.join(s['quote'] for s in item['sources'])
                if needed and not all(re.search(pattern, own, re.I) for pattern in needed):
                    for alias, span in spans.items():
                        block = evidence[span['evidence']]
                        if alias in selected or (block['document_id'], block['version_id']) not in identities:
                            continue
                        if (re.search(r'\bscope\b|\baddresses\b|\boutlines\b|\bdescribes\b|lays down', span['quote'], re.I)
                                and all(re.search(pattern, span['quote'], re.I) for pattern in needed)
                                and len(span['quote']) <= 6000 and added_tokens + cost(alias) <= 900):
                            item = ra_spans.resolve({'claims': [{**item, 'citation_span_ids': selected + [alias]}],
                                'gaps': []}, evidence, spans)['claims'][0]
                            changes.append({'claim_id': item['id'], 'added_span_id': alias,
                                'reason': 'named_guideline_scope_not_in_selected_date',
                                'boundary': 'same_supplied_document_version', 'reviewed_after_completion': True})
                            added_tokens += cost(alias)
                            break
        ready.append(item)
    return ready, table_changes + changes
