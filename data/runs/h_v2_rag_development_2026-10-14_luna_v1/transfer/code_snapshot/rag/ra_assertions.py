"""Conservatively separate explicit positive statements from absence prose."""
from copy import deepcopy
import re

VERSION = 'ra-positive-absence-separation-1'
ABSENCE = re.compile(r'보고되지\s*않|제공되지\s*않|포함되지\s*않|포함되어\s*있지\s*않|데이터.{0,15}없|결과.{0,15}없|자료.{0,15}없|\bnot\s+(?:reported|provided)|\bno\s+(?:data|results)', re.I)
CONNECTOR = re.compile(r'(하였|했|되었|받았|였|있었)(?:으며|고),?\s+')


def preserve_sourced_date(claim):
    """Weaken an explicit date-and-scope clause when citations prove only date."""
    if claim['products']:
        return claim, []
    own='\n'.join(s['quote'] for s in claim['sources'])
    if not re.search(r'Date for coming into effect|Start of public consultation',own,re.I):
        return claim, []
    if re.search(r'\bscope\b|\baddresses\b|lays down|\brequirements\b|\bsimilarity\b|outlines the general principles',own,re.I):
        return claim, []
    for pattern,ending in [(r'^(.+?(?:효력이\s*발생|효력을\s*발휘))하며,?\s+(.+)$','한다.'),
                           (r'^(.+?(?:공공\s*상담이\s*시작|시행|발효))되며,?\s+(.+)$','된다.')]:
        match=re.match(pattern,claim['text'])
        if match and re.search(r'다룬|설명|적용\s*범위|원칙',match[2]) and re.search(r'\d',match[1]):
            item=deepcopy(claim);item.update(text=match[1]+ending,
                original_review_clause=claim['text'],assertion_separation=VERSION)
            withheld={**deepcopy(claim),'id':claim['id']+'.scope','text':match[2],
                'reason':'document_scope_tail_not_in_selected_citations'}
            return item,[withheld]
    return claim, []


def preserve_representativeness(claim):
    """Keep an explicit representative-comparator clause, not broader similarity."""
    own='\n'.join(s['quote'] for s in claim['sources'])
    if not re.search(r'representative',own,re.I) or re.search(r'\bsimilarity\b',own,re.I):
        return claim, []
    match=re.search(r'유사(?:해야|하여야)\s*하며,?\s+',claim['text'])
    if not match or not re.search(r'대표.{0,30}입증',claim['text'][match.end():]):
        return claim, []
    item=deepcopy(claim);item.update(text=claim['text'][match.end():],
        original_review_clause=claim['text'],assertion_separation=VERSION)
    withheld={**deepcopy(claim),'id':claim['id']+'.similarity',
        'text':claim['text'][:match.end()].strip(),
        'reason':'representativeness_not_biological_similarity'}
    return item,[withheld]


def prepare(claims, evidence=None):
    ready, withheld, changes = [], [], []
    for claim in claims:
        text = claim['text']
        if not claim['products'] and re.search(r'현재.{0,12}(?:버전|유효|효력)|최신.{0,12}(?:지침|문서)', text):
            # Preserve a separately written historical date; source checking
            # still decides whether that date is evidenced by the final IDs.
            parts = re.split(r'이며,?\s+', text, maxsplit=1)
            if len(parts)==2 and re.search(r'현재|최신',parts[0]) and re.search(r'채택|시행|발효|효력',parts[1]) and re.search(r'\d',parts[1]):
                item=deepcopy(claim);item.update(text=parts[1],original_claim_text=text,assertion_separation=VERSION)
                ready.append(item)
                changes.append({'claim_id':claim['id'],'original':text,'positive':parts[1],
                                'withheld_text':parts[0],'method':VERSION})
                withheld.append({**deepcopy(claim),'id':claim['id']+'.current_validity',
                                 'text':parts[0],'reason':'stored_guideline_does_not_certify_current_validity'})
                continue
        # Comparator identity and approval/licensing status are independent.
        # Keep a weaker identity proposal for the SAME selected-source review.
        # The omitted modifier is recorded, not treated as an established fact.
        if claim['products'] and re.search(r'대조약|comparator', text, re.I):
            quoted = '\n'.join(s['quote'] for s in claim['sources'])
            missing_status = not re.search(r'authori[sz]ed|approved|licensed|승인|허가', quoted, re.I)
            if evidence:
                missing_status = any(not re.search(r'authori[sz]ed|approved|licensed|승인|허가',
                    '\n'.join(s['quote'] for s in claim['sources'] if evidence[s['evidence']].get('product_id')==product), re.I)
                    for product in claim['products'])
            if missing_status:
                weaker = re.sub(r'유럽에서\s*(?:승인된|허가된)', 'EU 버전의', text)
                weaker = re.sub(r'미국에서\s*(?:승인된|허가된)', 'US 버전의', weaker)
                weaker = re.sub(r'(?<=EU)\s*(?:승인된|승인|허가된|허가)', '', weaker)
                weaker = re.sub(r'(?<=US)\s*(?:승인된|승인|허가된|허가|라이선스|라이센스)', '', weaker)
                if weaker != text:
                    item = deepcopy(claim);item.update(text=weaker, original_claim_text=text,
                        assertion_separation=VERSION)
                    ready.append(item)
                    changes.append({'claim_id':claim['id'],'original':text,'positive':weaker,
                        'withheld_text':'approval/licensing modifier','method':VERSION})
                    withheld.append({**deepcopy(claim),'id':claim['id']+'.approval',
                        'reason':'comparator_approval_not_in_selected_sources','coverage_affecting':False})
                    continue
        positive = None
        # Only split an explicit trailing absence. Conditional/causal premises
        # and statements requiring an implicit new subject remain unchanged.
        if claim['claim_basis'] == 'absence_of_evidence':
            for match in CONNECTOR.finditer(text):
                before, after = text[:match.start()], text[match.end():]
                if (ABSENCE.search(after) and not ABSENCE.search(before)
                        and not re.search(r'경우|조건|때만|한해서|않으면|없으면|only if|unless', text, re.I)):
                    positive = before + match[1] + '다.'
                    break
            if positive:
                item = deepcopy(claim)
                item.update(text=positive, claim_basis='source_statement', original_claim_text=text,
                            assertion_separation=VERSION)
                ready.append(item)
                changes.append({'claim_id': claim['id'], 'original': text, 'positive': positive,
                                'withheld_text': text[match.end():], 'method': VERSION})
                withheld.append({**deepcopy(claim), 'id': claim['id'] + '.absence',
                    'text': text[match.end():], 'reason': 'partial_retrieval_cannot_establish_document_absence'})
                continue
            # Leave unsplittable wording for the selected-source clause review;
            # its final clauses still undergo the strict absence guard.
            ready.append(claim)
            continue
        ready.append(claim)
    return ready, withheld, changes


def deduplicate(claims, evidence=None):
    kept, omitted, seen = [], [], {}
    for claim in claims:
        text = re.sub(r'(mg|mL|SC|IV)의', r'\1', claim['text'])
        text = re.sub(r'\s+', '', text).rstrip('.,').casefold()
        proof = tuple(sorted(s.get('citation_span_id', s['quote']) for s in claim['sources']))
        if evidence:
            from rag.ra_requirements import verified_study_key
            proof = tuple(sorted({(b.get('document_id') or '', b.get('version_id') or '',
                str(verified_study_key(b) or b.get('chunk_id') or s['evidence']),
                repr(b.get('analysis_context', {})))
                for s in claim['sources'] for b in [evidence[s['evidence']]]}))
        key = (text, claim['kind'], claim['claim_basis'], tuple(sorted(claim['products'])), proof)
        if key in seen:
            prior = seen[key]
            prior['task_ids'] = list(dict.fromkeys(prior['task_ids'] + claim['task_ids']))
            if prior.get('requirement_ids') or claim.get('requirement_ids'):
                prior['requirement_ids']=list(dict.fromkeys(prior.get('requirement_ids',[])+claim.get('requirement_ids',[])))
            omitted.append({'id':claim['id'],'same_as':prior['id'],
                'reason':'same_assertion_same_source_scope', 'sources':deepcopy(claim['sources'])})
        else:
            seen[key]=claim;kept.append(claim)
    return kept, omitted
