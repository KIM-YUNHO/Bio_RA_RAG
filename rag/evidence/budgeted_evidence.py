"""Token-budgeted adapter retaining source ranges and physical hit coverage."""
from rag.evidence.preprocessed_evidence import PreprocessedEvidence
from rag.evidence.evidence_budget import select
from rag.evidence.evidence_slices import view
from rag.evidence.evidence_slices import score
from rag.evidence.evidence_slices import term_keys
from copy import deepcopy
from collections import defaultdict
import re
import math


def relevance(chunk, query, weights=None):
    """Rank source passages, including their printed structural labels."""
    structure = chunk.get('structure', {})
    labels = ' '.join([*(structure.get('section_path') or []), structure.get('caption') or ''])
    extra_labels=' '.join(label for label in structure.get('section_path',[]) if label not in chunk['text'])
    value = score(chunk['text'], query,weights) + score(extra_labels, query,weights)
    for kind, number in re.findall(r'\b(table|figure)\s+(\d+)\b', query, re.I):
        if re.search(r'\b' + kind + r'\s+' + number + r'\b', labels + '\n' + chunk['text'], re.I):
            value += 12
    return value


class BudgetedEvidence(PreprocessedEvidence):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.budget=None;self.last_evidence={};self.last_budget={}
        # Composed releases retain each component's representation row indices.
        # A global enumeration would falsify a virtual heading's source index.
        self.unit_index={}
        for chunk in self.chunks:
            for span in chunk['provenance']['source_spans']:
                uid,index=span['unit_id'],span['unit_index']
                if uid in self.unit_index and self.unit_index[uid]!=index:raise ValueError('Conflicting source unit row index')
                self.unit_index[uid]=index
        self.by_page=defaultdict(list)
        for chunk in self.chunks:
            self.by_page[(chunk['document_id'],chunk['version_id'],chunk['pdf_page'])].append(chunk)
        self.by_visual=defaultdict(list)
        for chunk in self.chunks:
            identity=chunk['structure'].get('figure_id') or chunk['structure'].get('table_id')
            if identity:self.by_visual[(chunk['document_id'],chunk['version_id'],identity)].append(chunk)
        self.visual_bundle=None
        from rag.index.bundle import read
        summary=read(self.bundle.candidate/'chunks_summary.json')
        if summary.get('visual_context_contract'):
            from preprocess.visual.visual_context import load_bundle
            self.visual_bundle=load_bundle(self.bundle.candidate,summary['visual_context_contract'])
        self.visual_links={l['link_id']:l for l in (self.visual_bundle or {}).get('links',[])}

    def linked_candidates(self,chunk,allowed_ids):
        if not self.visual_bundle:return []
        from preprocess.visual.visual_context import verify_chunk_links
        verify_chunk_links(chunk,self.visual_bundle)
        result=[]
        from rag.evidence.source_study_scope import link_conflict
        from preprocess.visual.visual_context import link_text
        for lid in chunk['structure'].get('related_visual_link_ids',[]):
            link=self.visual_links[lid]
            for target in self.by_visual[(chunk['document_id'],chunk['version_id'],link['visual_id'])]:
                if (target['chunk_id'] in allowed_ids and target['structure']['kind'] in ('table_candidate','figure_candidate')
                        and not link_conflict(chunk,target,link_text(link,self.visual_bundle))):
                    result.append(target)
        return result

    def page_candidates(self, selected, allowed_ids, query):
        """The strongest passage on a hit page may be a table outside dense top-8."""
        pages=list(dict.fromkeys((c['document_id'],c['version_id'],c['pdf_page']) for c in selected))
        result=[]; seen=set()
        for page in pages:
            originals=[c for c in selected if (c['document_id'],c['version_id'],c['pdf_page'])==page]
            own=[c for c in self.by_page[page] if c['chunk_id'] in allowed_ids]
            keys=[term_keys(c['text']+' '+' '.join(c['structure'].get('section_path',[]))) for c in own]
            weights={term:math.log(1+(len(own)-sum(term in k for k in keys)+.5)/(sum(term in k for k in keys)+.5))
                for term in term_keys(query)}
            role_score=getattr(self,'context_role_score',None)
            roles={c['chunk_id']:role_score(c) if role_score else 0 for c in own}
            candidates=[c for c in own if c in originals or roles[c['chunk_id']]>0 or relevance(c,query,weights)>0]
            from rag.retrieval.clinical_retrieval import cues
            cues=getattr(self,'cue_fn',cues)
            preferences=getattr(self,'budget',None) or {}
            candidates.sort(key=lambda c:(-roles[c['chunk_id']],-(relevance(c,query,weights)+4*len(cues(query,c,
                preferences.get('studies',()),preferences.get('topic')))), c not in originals))
            for chunk in candidates:
                if chunk['chunk_id'] not in seen:
                    result.append(chunk);seen.add(chunk['chunk_id'])
        return result

    def blocks(self, selected):
        result={}
        for chunk,role in selected:
            block={**{key:chunk.get(key) for key in ('chunk_id','document_id','version_id','pdf_page',
                'title','source_url','product_id','jurisdiction','document_type','document_metadata',
                'studies','study_candidates','analysis_context','locator_type','pdf_page_label')},
                'text':chunk['text'],'representation':chunk['representation'],
                'source_refs':chunk['provenance']['source_refs'],'source_spans':chunk['provenance']['source_spans'],
                'structure':chunk['structure'],'retrieval_role':role,'also_in':[],
                'evidence_status':'unreviewed_retrieval','review_links':self.links(chunk)}
            if chunk.get('evidence_view'):block['evidence_view']=chunk['evidence_view']
            result[f'E{len(result)+1}']=block
        return result

    def evidence(self,selected,allowed,include_nondefault=False):
        if not self.budget:
            self.last_evidence=super().evidence(selected,allowed,include_nondefault)
            return self.last_evidence
        allowed_ids={self.chunks[int(i)]['chunk_id'] for i in allowed}
        selected=self.page_candidates(selected,allowed_ids,self.budget['query'])
        if re.search(r'footnote|각주',self.budget['query'],re.I):
            # Figure label projections do not include printed footnote paragraphs.
            # Explicit footnote requests prioritize source prose on the hit page.
            preferred=[]
            for hit in selected:
                if hit['structure']['kind']!='figure_candidate':continue
                own=[c for c in self.chunks if c['chunk_id'] in allowed_ids and c['structure']['kind']=='text'
                    and (c['document_id'],c['version_id'],c['pdf_page'])==(hit['document_id'],hit['version_id'],hit['pdf_page'])]
                own.sort(key=lambda c:-score(c['text'],self.budget['query']))
                if own and score(own[0]['text'],self.budget['query'])>0:preferred.append(own[0])
            selected=[*preferred,*selected]
        queue=[];seen=set()
        # A visual linked from an earlier hit can itself be a later page's
        # strongest hit. Register all hits before adding graph neighbours so
        # duplicate suppression never demotes a hit to optional linked context.
        for chunk in selected:
            key=chunk['chunk_id']
            if key in allowed_ids and key not in seen:
                queue.append((chunk,'retrieved'));seen.add(key)
        for chunk in selected:
            if chunk['chunk_id'] not in allowed_ids:continue
            # Source headings are supplied as their own literal blocks. A heading
            # candidate in metadata alone is never usable as a quotation.
            anchors=chunk['structure'].get('heading_anchors',[])
            specific=[h for h in anchors if h.get('kind')=='study']
            if anchors and anchors[-1] not in specific:specific.append(anchors[-1])
            for heading in specific:
                uid=heading['heading_location'].get('block_id')
                source=self.bundle.units.get(uid)
                if not source or uid not in self.unit_index or not source.get('retrieval_eligible') or source.get('review_required'):continue
                if (source['document_id'],source['version_id'])!=(chunk['document_id'],chunk['version_id']):
                    raise ValueError('Heading context crosses a source version')
                location=heading['heading_location']
                start=location.get('representation_start',0)
                end=location.get('representation_end',len(source['text']))
                if not 0<=start<end<=len(source['text']):raise ValueError('Invalid source heading range')
                key=f'heading:{uid}:{start}:{end}'
                if key in seen:continue
                context=deepcopy(chunk)
                context.update(chunk_id=key,text=source['text'][start:end],pdf_page=location['pdf_page'])
                context['provenance']['source_refs']=source['source_refs']
                context['provenance']['source_spans']=[{'unit_index':self.unit_index[uid],'unit_id':uid,
                    'representation_start':start,'representation_end':end,
                    'separator_before':'','role':'content'}]
                context['representation'].update(unit_ids=[uid],origins={uid:source['content_origin']},description_review_required=False)
                context['structure'].update(kind='text',caption=None,table_id=None,figure_id=None)
                queue.append((context,'source_heading'));seen.add(key)
            for context in self.linked_candidates(chunk,allowed_ids):
                key=context['chunk_id']
                if key in allowed_ids and key not in seen:
                    queue.append((self.by_id[key],'linked_visual_candidate'));seen.add(key)
        if queue:
            from rag.retrieval.clinical_retrieval import cues
            cues=getattr(self,'cue_fn',cues)
            # Complete qualified result tables precede broad discussion pages.
            # Other hits remain candidates within the same final input cap.
            role_score=getattr(self,'context_role_score',None)
            queue.sort(key=lambda row:(-1,-(role_score(row[0]) if role_score else 0),
                -len(cues(self.budget['query'],row[0],self.budget.get('studies',()),self.budget.get('topic'))))
                if row[1]=='retrieved' else (0,0,0))
        core=set(); prepared=[]; excerpts=[]
        for chunk,role in queue:
            page=(chunk['document_id'],chunk['version_id'],chunk['pdf_page'])
            candidate=chunk
            if role=='retrieved':
                core.add(page)
                limit=self.budget['core_tokens']
                if chunk['structure']['kind']=='table_candidate':limit=max(limit,1024 if self.budget.get('core_fits') else 512)
                candidate=view(chunk,self.bundle.units,self.budget['query'],limit,self.budget['counter'])
                if candidate is None:candidate=chunk
                if self.budget.get('core_fits'):
                    if self.budget['core_fits'](self.blocks([(chunk,role)])):candidate=chunk
                    while limit>=120 and not self.budget['core_fits'](self.blocks([(candidate,role)])):
                        limit=int(limit*.7)
                        smaller=view(chunk,self.bundle.units,self.budget['query'],limit,self.budget['counter'])
                        if smaller is not None:candidate=smaller
            prepared.append((candidate,role))
        dependencies={};requested=set(self.budget.get('studies',()))
        if requested:
            from rag.retrieval import study_registry as sr
            headings=[c for c,r in prepared if r=='source_heading']
            for chunk,role in prepared:
                if role!='retrieved' or requested.intersection(k for _,_,k in sr.finditer(chunk['text'],chunk.get('product_id'))):continue
                anchors={h['heading_location'].get('block_id') for h in chunk['structure'].get('heading_anchors',[])
                    if requested.intersection(h.get('study_keys',[]))}
                linked=[h['chunk_id'] for h in headings if (h['document_id'],h['version_id'])==(chunk['document_id'],chunk['version_id'])
                    and anchors.intersection(s['unit_id'] for s in h['provenance']['source_spans'])
                    and requested.intersection(k for _,_,k in sr.finditer(h['text'],h.get('product_id')))]
                if linked:dependencies[chunk['chunk_id']]=linked
        selected,omitted=select(prepared,lambda proposed:self.budget['fits'](self.blocks(proposed)),dependencies)
        # Expand core excerpts when the other hit pages already have their share.
        for index,(chunk,role) in enumerate(selected):
            if not chunk.get('evidence_view'):continue
            if chunk['chunk_id'] not in self.by_id:continue
            proposed=[*selected];proposed[index]=(self.by_id[chunk['chunk_id']],role)
            if self.budget['fits'](self.blocks(proposed)):selected=proposed
        result=self.blocks(selected)
        excerpts=[{'chunk_id':b['chunk_id'],'pdf_page':b['pdf_page'],
                   'reason':'exact_source_excerpt_with_visible_gaps'} for b in result.values() if b.get('evidence_view')]
        self.last_omitted=omitted
        self.last_evidence=result
        self.last_budget={**{k:v for k,v in self.budget.items() if k not in ('counter','fits','core_fits','query')},
            'pages_supplied':len({(b['document_id'],b['version_id'],b['pdf_page']) for b in result.values()}),
            'excerpts':excerpts,'source_clipping':'exact_sentence_or_complete_table_row_ranges'}
        self.last_budget['required_source_heading_dependencies']=dependencies
        return result
