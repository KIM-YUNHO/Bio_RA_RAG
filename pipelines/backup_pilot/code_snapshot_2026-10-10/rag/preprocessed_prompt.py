"""One prompt builder shared by token preflight, generation and offline replay."""
import json
REPAIR_RESERVED_TOKENS=640


def build(question,u,unit,evidence,extra_rules,condition_rules=''):
    from rag.answer import messages_for
    blocks=list(evidence.values());texts=[];sources={}
    for block in blocks:
        identity=(block['document_id'],block['version_id'])
        sources.setdefault(identity,f'D{len(sources)+1}')
        metadata={key:block.get(key) for key in ('pdf_page','product_id','jurisdiction','retrieval_role')}
        # Full identities/ranges remain in response provenance. Stable source
        # aliases distinguish versions in the prompt without repeating hashes.
        metadata['source_version']=sources[identity]
        metadata['representation']={key:block['representation'].get(key) for key in ('retrieval_eligible','description_review_required','numeric_evidence_eligible','semantic_status')}
        metadata['representation']['origins']=sorted(set(block['representation'].get('origins',{}).values()))
        metadata['visual_parent']={key:block['structure'].get(key) for key in ('kind','caption','association_status','continuation_status')}
        metadata['source_section']={'path':block['structure'].get('section_path',[]),
            'heading_studies':block['structure'].get('heading_studies',[]),
            'status':'structural_candidate_unverified'}
        if block.get('evidence_view'):metadata['evidence_view']={'selection':block['evidence_view']['selection'],'semantic_approval':False}
        texts.append('Metadata (not source text): '+json.dumps(metadata,ensure_ascii=False,separators=(',',':'))+'\nVerbatim chunk text:\n'+block['text'])
    messages=messages_for(question,u,unit,blocks,texts)
    messages[0]['content']+='\n'+extra_rules+'\n'+condition_rules
    messages[0]['content']+='\n[UNSUPPLIED SOURCE GAP] is a gap marker, never source evidence; never quote across it.'
    messages[1]['content']+='\n\nQuestion to answer: '+question+'\nAllowed evidence IDs: '+', '.join(evidence)+'. Use only these IDs in items[].evidence.'
    return messages


def configure_budget(adapter,client,question,u,unit,pages,extra_rules,condition_rules='',builder=None):
    counter=client.tokenizer
    make=lambda blocks:builder(question,u,unit,blocks) if builder else build(question,u,unit,blocks,extra_rules,condition_rules)
    def cost(blocks):
        total=counter.messages(make(blocks))
        if builder:
            from rag.source_selection import schema,catalog
            total+=counter.one(json.dumps(schema(catalog(blocks,question)),ensure_ascii=False,separators=(',',':')))
        return total
    base=cost({})
    n=max(1,len({(p['document_id'],p['version_id'],p['pdf_page']) for p in pages}))
    reserved=0 if builder else REPAIR_RESERVED_TOKENS
    limit=client.input_limit-reserved
    adapter.budget={'counter':counter,'fits':lambda blocks:cost(blocks)<=limit,
        'query':u['search_terms'],'topic':unit.get('topic'),'studies':unit.get('studies',[]),
        'core_tokens':max(96,(limit-base-200*n)//n),
        'context_tokens':client.config.num_ctx,'output_reserved_tokens':client.output_tokens,
        'input_limit':limit,'repair_reserved_tokens':reserved,
        'provider_input_limit':client.input_limit,'base_prompt_tokens':base,'token_count_basis':counter.basis,
        'source_selection_schema_included':bool(builder)}
    if builder:
        from rag.clinical_retrieval import cues
        def core_fits(blocks):
            block=next(iter(blocks.values()))
            qualified=cues(
                u['search_terms'],block,unit.get('studies',()),unit.get('topic'))
            # A qualified table can need multiple complete endpoint groups.
            # Give it a larger share; the final prompt still obeys one hard cap.
            share=max(160,(limit-base)//n-100)
            if qualified:share=max(share,limit-base-400)
            return cost(blocks)-base<=share
        adapter.budget['core_fits']=core_fits
    return make
