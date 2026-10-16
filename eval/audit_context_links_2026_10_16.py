"""Read-only audit of served-release context links; no provider/model calls.

Structural consistency checks are exhaustive for this snapshot. Study/scope
conflicts are review candidates, not automatic semantic error judgements.
"""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rag import study_registry as sr
from rag.portable_paths import resolve

OUT = ROOT / 'data/runs/context_link_audit_2026-10-16'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def rows(path):
    with Path(path).open(encoding='utf-8') as source:
        return [json.loads(s) for s in source if s.strip()]


def save(name, value):
    (OUT/name).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def studies(c):
    anchors = [h for h in c['structure'].get('heading_anchors', []) if h['kind']=='study']
    return sorted({sr.top(k) for k in (anchors[-1].get('study_keys', []) if anchors else [])})


def short(c):
    return {'chunk_id': c['chunk_id'], 'document_id': c['document_id'],
            'version_id': c['version_id'], 'page': c['pdf_page'],
            'kind': c['structure']['kind'], 'study_keys': studies(c),
            'section_path': c['structure'].get('section_path', []), 'text': c['text'][:700]}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    registry = read(ROOT/'data/versions/versions.json')
    version = next(v for v in registry['versions'] if v['id']==registry['roles']['served_by_ra_service'])
    release = ROOT/version['embedded_path']
    folder = release/'candidate'
    chunks = rows(folder/'chunks.jsonl')
    units = {u['unit_id']:u for u in rows(folder/'source_units.jsonl')}
    links = rows(folder/'visual_context_links.jsonl')
    contexts = {u['context_unit_id']:u for u in rows(folder/'visual_context_units.jsonl')}
    targets = {t['visual_id']:t for t in rows(folder/'visual_context_targets.jsonl')}
    groups = rows(folder/'visual_groups.jsonl')
    by_unit, by_visual = defaultdict(list), defaultdict(list)
    counts, violations, flags, caption_flags = Counter(), [], [], []
    checked_anchors, indexed = set(), set()
    for c in chunks:
        rebuilt = []
        for span in c['provenance']['source_spans']:
            by_unit[span['unit_id']].append(c)
            u = units.get(span['unit_id'])
            a,b = span['representation_start'],span['representation_end']
            if not u or not 0<=a<b<=len(u['text']):
                violations.append({'kind':'chunk_source_range','chunk_id':c['chunk_id'],'span':span})
                continue
            if (u['document_id'],u['version_id']) != (c['document_id'],c['version_id']):
                violations.append({'kind':'chunk_source_identity','chunk_id':c['chunk_id'],'span':span})
            rebuilt.append(span['separator_before']+u['text'][a:b])
        if ''.join(rebuilt)!=c['text']:
            violations.append({'kind':'chunk_body_reconstruction','chunk_id':c['chunk_id']})
        s = c['structure']
        if s['kind']=='table_candidate':
            caption = s.get('caption') or c['text'].split('<table')[0]
            literal = {sr.top(k) for _,_,k in sr.finditer(caption,c['product_id'])}
            inherited = set(studies(c))
            if literal and inherited and literal.isdisjoint(inherited):
                caption_flags.append({'chunk_id':c['chunk_id'],'document_id':c['document_id'],
                    'page':c['pdf_page'],'inherited':sorted(inherited),'caption_studies':sorted(literal),
                    'caption':caption,'heading_anchors':s.get('heading_anchors',[])})
        visual = s.get('table_id') or s.get('figure_id')
        if visual:
            by_visual[visual].append(c)
        indexed.update(s.get('visual_context', {}).get('indexed_link_ids', []))
        for a in s.get('heading_anchors', []):
            if a['section_id'] in checked_anchors:
                continue
            checked_anchors.add(a['section_id'])
            loc = a['heading_location']; u = units.get(loc['block_id'])
            if not u:
                violations.append({'kind':'missing_heading_unit', 'heading':a})
                continue
            start, end = loc.get('representation_start',0), loc.get('representation_end',len(u['text']))
            if not (0<=start<end<=len(u['text'])):
                violations.append({'kind':'heading_range', 'heading':a})
            elif re.sub(r'\s+',' ',u['text'][start:end]).strip()!=re.sub(r'\s+',' ',a['title']).strip():
                violations.append({'kind':'heading_source_text','heading':a,'source_text':u['text'][start:end]})
            if (u['document_id'],u['version_id']) != (c['document_id'],c['version_id']):
                violations.append({'kind':'heading_identity', 'heading':a, 'chunk':short(c)})
            if not u.get('retrieval_eligible') or u.get('review_required'):
                counts['unique_heading_anchors_not_supplied_by_packet']+=1
    for l in links:
        counts['links_'+l['link_status']]+=1
        if l['link_status']!='linked_candidate':
            continue
        target, ctx = targets.get(l['visual_id']), contexts.get(l['context_unit_id'])
        if not target or not ctx:
            violations.append({'kind':'missing_link_endpoint', 'link':l})
            continue
        if len({(x['document_id'],x['version_id']) for x in (l,target,ctx)})!=1:
            violations.append({'kind':'link_identity', 'link':l})
        a,b = l['context_range']
        if not 0<=a<b<=len(ctx['text']):
            violations.append({'kind':'link_range', 'link':l})
        if l['context_role']!='referring_sentence':
            continue
        counts['referring_sentence_links']+=1
        cross = ctx['pdf_page'] not in target.get('pdf_pages',[target['pdf_page']])
        if cross: counts['cross_page_referring_sentence_links']+=1
        source_chunks = list({c['chunk_id']:c for c in by_unit.get(ctx['source_unit_id'],[])}.values())
        target_chunks = by_visual.get(l['visual_id'],[])
        source_keys = {k for c in source_chunks for k in studies(c)}
        target_keys = {k for c in target_chunks for k in studies(c)}
        literal = {sr.top(k) for _,_,k in sr.finditer(ctx['text'][a:b], source_chunks[0]['product_id'])} if source_chunks else set()
        reasons = []
        if source_keys and target_keys and source_keys.isdisjoint(target_keys): reasons.append('different_carried_study_keys')
        if literal and target_keys and literal.isdisjoint(target_keys): reasons.append('explicit_reference_study_conflict')
        if ctx.get('scope_id') and target.get('scope_id') and ctx['scope_id']!=target['scope_id']:
            reasons.append('different_visual_scope')
        if reasons:
            flags.append({'link_id':l['link_id'], 'reasons':reasons,'indexed_in_header':l['link_id'] in indexed,
                          'source_page':ctx['pdf_page'],'target_pages':target.get('pdf_pages'),
                          'source_text':ctx['text'][a:b], 'source_studies':sorted(source_keys),
                          'target_studies':sorted(target_keys), 'literal_studies':sorted(literal),
                          'source_chunks':[short(c) for c in source_chunks],
                          'target_chunks':[short(c) for c in target_chunks]})
    counts['unique_heading_anchors_checked'] = len(checked_anchors)
    counts['referring_flags_review_candidates'] = len(flags)
    counts['referring_flags_indexed_in_header'] = sum(f['indexed_in_header'] for f in flags)
    counts['table_caption_vs_heading_conflicts'] = len(caption_flags)
    counts['table_caption_vs_heading_conflicts_by_document'] = dict(Counter(f['document_id'] for f in caption_flags))
    counts['visual_group_continuation_status'] = dict(Counter(g.get('continuation_status','missing') for g in groups))
    counts['integrity_violations'] = len(violations)
    counts['all_chunks'] = len(chunks)
    counts['all_links'] = len(links)
    # Retain a source-linked packet for the example discussed in chat.
    example = [c for c in chunks if c['document_id']=='EMA:64196' and 42<=c['pdf_page']<=46]
    example_visuals = {c['structure'].get('table_id') for c in example if c['structure'].get('table_id')}
    example_links = []
    for l in links:
        if l.get('visual_id') in example_visuals:
            ctx = contexts[l['context_unit_id']];a,b=l['context_range']
            example_links.append({**l,'source_page':ctx['pdf_page'],'source_text':ctx['text'][a:b]})
    manifest = read(release/'release_manifest.json')
    docs = {}
    for component in manifest['components']:
        run = resolve(component['run'])
        for doc in read(run/'extraction_manifest.json')['documents']:
            if doc['document_id']=='EMA:64196': docs[doc['document_id']]={**doc,'resolved_raw_path':str(resolve(doc['raw_path']))}
    save('summary.json', {'release_id':version['id'],'chunks_sha256':hashlib.sha256((folder/'chunks.jsonl').read_bytes()).hexdigest(),
                         'counts':dict(counts),'scope':'Exhaustive structural checks; semantic flags require source review. No model calls.'})
    save('structural_violations.json', violations)
    save('study_scope_review_candidates.json', flags)
    save('caption_study_review_candidates.json',caption_flags)
    save('abp654_source_packet.json', {'chunks':example,'links':example_links,'documents':docs})
    print(json.dumps({'out':str(OUT),'counts':dict(counts),'flag_examples':[{'source_page':f['source_page'],
        'target_pages':f['target_pages'],'source_studies':f['source_studies'],'target_studies':f['target_studies'],
        'text':f['source_text'][:160]} for f in flags[:12]]},ensure_ascii=False))


if __name__=='__main__':main()
