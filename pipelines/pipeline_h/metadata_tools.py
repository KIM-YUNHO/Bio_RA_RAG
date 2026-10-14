"""Normalize tool inputs while retaining catalog vocabulary and original raw blocks."""
import importlib.util
import re
from pathlib import Path

def doc_type(meta):
    source=meta['source'];family=meta.get('family');name=meta['document_type']
    if source=='Health Canada':
        return {'Regulatory Decision Summary':'rds','Summary Basis of Decision':'sbd','Product Monograph':'product_monograph'}.get(name,family or name)
    if source=='FDA':
        return {'assessment_report':'review','quality_review':'review','other_review':'review','decision_letter':'approval_letter',
                'product_information':'label','correspondence':'review'}.get(family,family or name)
    return {'risk_management_plan':'risk_management','post_authorisation_history':'procedural_steps','presentation_list':'procedural_steps',
            'plain_summary':'epar_overview'}.get(family,family or name)

def normalize_block_metadata(blocks,meta):
    path=Path(__file__).resolve().parent/'vendor/compare_study_standalone/compare_study/bench/vendor/ra_prep/structure.py'
    original=[b.get('speaker') for b in blocks]
    if meta['source']=='ClinicalTrials.gov':
        # A posted protocol/SAP is the sponsor's own plan: every block speaks for the applicant,
        # never for a regulator, and states planned rather than actual conduct.
        agency,status=None,'sponsor_document_rule'
        for b in blocks:b['speaker']='applicant'
    else:
        spec=importlib.util.spec_from_file_location('_h_source_structure_rules',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        agency,status={'Health Canada':'hc','EMA':'ema','FDA':'fda'}[meta['source']],'heuristic_unverified'
        module.assign_speaker(blocks,agency,doc_type(meta))
    for block,old in zip(blocks,original):
        block['source_structure_metadata']={'original_speaker':old,'normalized_agency_input':agency,
            'normalized_document_type_input':doc_type(meta),'speaker_status':status,'catalog_vocabulary_preserved':True}
    return {'version':'metadata-tool-input-1','changed_speaker_blocks':sum(b.get('speaker')!=old for b,old in zip(blocks,original)),
            'rule_source':str(path),'normalized_agency':agency,'normalized_doc_type':doc_type(meta)}

def printed_page_candidates(blocks,height,width):
    candidates=[]
    for b in blocks:
        if b.get('type')!='text' or not b.get('bbox'):continue
        l,bot,r,top=b['bbox']
        if top>height*.13 or (l+r)/2<width*.35 or not (b.get('furniture') or b.get('label')=='page_footer'):continue
        text=b.get('text','').strip()
        match=re.fullmatch(r'(?:Page\s+)?(\d{1,4}|[ivxlcdm]{1,6})(?:\s+(?:of|/)\s+\d+)?',text,re.I)
        if match:candidates.append({'value':match[1],'literal':text,'block_ref':b['ref'],'bbox_original_bottom_left':b['bbox'],
                                    'status':'source_footer_candidate; not independently verified'})
    return candidates
