"""Select exact source ranges; preserve table header/rows and visible gaps."""
from copy import deepcopy
import re

GAP = '\n[UNSUPPLIED SOURCE GAP]\n'


def term_keys(text):
    def stem(word):
        return word[:-1] if len(word)>3 and word.endswith('s') and not word.endswith(('is','ss','us')) else word
    plain = re.sub(r'<[^>]*>', ' ', re.sub(r'<br\s*/?>','',text,flags=re.I)).casefold()
    words={stem(w) for w in re.findall(r'[A-Za-z][A-Za-z0-9_-]+',plain)}
    words-={'the','and','for','what','study','which','with','from','were','was','are','of','in','to','by','is','on','or','at','as','an','be'}
    return words|{'n:'+n for n in re.findall(r'(?<![\w.])\d+(?:\.\d+)?(?![\d.])',plain)}


def score(text, query, weights=None):
    matched=term_keys(text)&term_keys(query)
    # Coverage beats repeated generic words in a long paragraph. An endpoint
    # appearing once in a result table must not lose to repeated dose prose.
    value=sum((weights or {}).get(term,1) for term in matched)
    from rag.regulatory_scope import requests_marketing_notification,MARKETING
    if requests_marketing_notification(query) and re.search(MARKETING,text,re.I) and re.search(r'\bsubmit\b|notif|inform',text,re.I):
        value+=12
    if re.search(r'infus|주입|정맥',query,re.I) and re.search(r'minimum|at least|duration|time|최소|시간',query,re.I):
        if re.search(r'administer|infus',text,re.I) and re.search(r'(?:at least|over|minimum)[\s\S]{0,50}\b(?:hours?|minutes?)\b',text,re.I):value+=12
    if re.search(r'approval|허가|승인',query,re.I) and re.search(r'date|날짜|일자',query,re.I) and re.search(r'Approval Date\s*:',text,re.I):value+=12
    return value


def table_ranges(text):
    """Keep the source prefix and complete groups connected by row spans.

    Colspans in leading headings need not make every independent body row
    indivisible. Undeclared/ambiguous prefixes conservatively stay whole.
    """
    from rag.table_evidence import cells
    rows=list(re.finditer(r'<tr\b[^>]*>.*?</tr\s*>',text,re.I|re.S))
    parsed=cells(text)
    if not rows or not parsed:return [(0,len(text),True)]
    body=None
    for r in range(len(rows)):
        own=[c for c in parsed if c['row']==r]
        try:
            spans=[int(c['colspan']) for c in own]
        except ValueError:return [(0,len(text),True)]
        # Numeric data shapes select a conservative *source prefix*, not
        # semantic header roles. Explicit th rows are always prefix context.
        numeric=sum(bool(re.match(r'\s*[($±+−–-]*\s*\d',c['value'])) for c in own[1:])
        if own and not any(c['tag']=='th' for c in own) and all(s==1 for s in spans) and numeric>=max(1,(len(own)-1)//2):
            body=r;break
    if body is None or body==0:return [(0,len(text),True)]
    parent=list(range(len(rows)))
    def root(i):
        while parent[i]!=i:i=parent[i]
        return i
    for cell in parsed:
        try:span=int(cell['rowspan'])
        except ValueError:return [(0,len(text),True)]
        if span<1 or cell['row']+span>len(rows):return [(0,len(text),True)]
        for row in range(cell['row']+1,cell['row']+span):parent[root(row)]=root(cell['row'])
    from preprocess.table_quality import inspect_table
    from rag.table_blocks import blocks
    quality=inspect_table(text)
    if not quality['issues']:
        for block in blocks(quality['cells']):
            start=max(body,block['start_row'])
            for row in range(start+1,block['end_row']):parent[root(row)]=root(start)
    groups={}
    for r in range(len(rows)):groups.setdefault(root(r),[]).append(r)
    result=[(0,rows[0].start(),True)] if rows[0].start() else []
    for own in sorted(groups.values(),key=min):
        if own!=list(range(min(own),max(own)+1)):return [(0,len(text),True)]
        result.append((rows[min(own)].start(),rows[max(own)].end(),min(own)<body))
    if rows[-1].end()<len(text):result.append((rows[-1].end(),len(text),True))
    return result


def slices(chunk, units):
    parts = []
    for span in chunk['provenance']['source_spans']:
        unit = units[span['unit_id']]
        start, end = span['representation_start'],span['representation_end']
        text = unit['text'][start:end]
        if unit['kind'] == 'table':
            pieces = table_ranges(text)
        elif unit['kind'] in ('figure','figure_group'):
            pieces = [(0,len(text),True)]
        else:
            # Sentence/line boundaries only; character offsets remain source exact.
            cuts = [0]+[m.end() for m in re.finditer(r'\n\s*\n|(?<=[.!?。])\s+(?=[A-Z])',text)]+[len(text)]
            pieces = [(a,b,span.get('role')=='repeated_context') for a,b in zip(cuts,cuts[1:]) if b>a and text[a:b].strip()]
        for a,b,required in pieces:
            parts.append({'span':{**span,'representation_start':start+a,'representation_end':start+b},
                'text':text[a:b], 'required':required or span.get('role')=='repeated_context',
                'original_separator':span['separator_before'] if a == 0 else ''})
    return parts


def view(chunk, units, query, limit, counter):
    if counter.one(chunk['text']) <= limit: return chunk
    parts = slices(chunk, units)
    # A bullet's numeric values depend on its printed colon lead-in. Retain
    # the contiguous list prefix, so a missing comparison/population sentence
    # cannot turn its otherwise correct numbers into an unbound result.
    dependencies={};lead=None
    for i,p in enumerate(parts):
        if GAP.strip() in p['original_separator']:lead=None
        bullet=p['text'].lstrip().startswith(('•','●','▪'))
        if bullet:
            if lead is not None:dependencies[i]=set(range(lead,i+1))
        else:lead=i if p['text'].rstrip().endswith(':') and '<table' not in p['text'].lower() else None
    required = {i for i,p in enumerate(parts) if p['required']}
    order = sorted((i for i in range(len(parts)) if i not in required),
        key=lambda i:(-score(parts[i]['text'],query),i))
    chosen = set(required)
    for i in required:chosen.update(dependencies.get(i,set()))

    def render(indices):
        text, spans, previous = '', [], None
        for i in sorted(indices):
            p = parts[i]
            separator = '' if previous is None else p['original_separator'] if i == previous+1 else GAP
            text += separator+p['text']; spans.append({**p['span'],'separator_before':separator})
            previous = i
        return text, spans

    if counter.one(render(chosen)[0]) > limit: return None
    for i in order:
        proposed = chosen | {i} | dependencies.get(i,set())
        if counter.one(render(proposed)[0]) <= limit: chosen = proposed
    # Wrappers/captions without a data row are not a useful table excerpt.
    if not chosen or not any(i not in required for i in chosen): return None
    text, spans = render(chosen)
    result = deepcopy(chunk)
    result['text'] = text
    result['provenance']['source_spans'] = spans
    ids = list(dict.fromkeys(s['unit_id'] for s in spans))
    result['representation']['unit_ids'] = ids
    result['representation']['origins'] = {uid:chunk['representation']['origins'][uid] for uid in ids}
    refs = []
    for uid in ids:
        for ref in units[uid]['source_refs']:
            if ref not in refs: refs.append(ref)
    result['provenance']['source_refs'] = refs
    result['evidence_view'] = {'original_chunk_id':chunk['chunk_id'],'selection':'query_ranked_exact_source_ranges',
        'original_tokens':counter.one(chunk['text']), 'selected_tokens':counter.one(text),
        'source_gap_marker':GAP.strip(), 'semantic_approval':False}
    result['evidence_view']['literal_list_dependencies']=[{'selected_part':i,'required_parts':sorted(dependencies[i])}
        for i in sorted(chosen) if i in dependencies]
    return result
