"""Source-bound visual review is a fallback, never an ID-based routing rule."""
import json
from pathlib import Path
from preprocess.ingest import sha256, stable_hash


def bind(asset,source,review_path):
    path=Path(review_path).resolve();record=json.loads(path.read_text(encoding='utf-8'))
    decision=next((d for d in record['decisions'] if d['asset_id']==asset['asset_id']
        and Path(d['extraction']).resolve()==Path(source).resolve()),None)
    if not decision:return None
    if decision['input_state_sha256']!=stable_hash([asset['base'],asset['native_reference'],asset.get('light_region_ocr')]):
        raise ValueError('Reviewed source state changed')
    if any(sha256(p)!=digest for p,digest in decision.get('crop_hashes',{}).items()):
        raise ValueError('Reviewed source image changed')
    return {'record_path':str(path),'record_sha256':sha256(path),'asset_id':asset['asset_id'],
        'source_extraction':str(Path(source).resolve()),'source_input_sha256':decision['input_state_sha256'],
        'visual_category':decision.get('visual_category'),'review_method':decision['review_method'],
        'crop_hashes':decision.get('crop_hashes',{}),'expert_approval':False,'numeric_evidence_eligible':False}


def validate(proof):
    if sha256(proof['record_path'])!=proof['record_sha256']:raise ValueError('Visual review record changed')
    record=json.loads(Path(proof['record_path']).read_text(encoding='utf-8'))
    match=next((d for d in record['decisions'] if d['asset_id']==proof['asset_id']
        and Path(d['extraction']).resolve()==Path(proof['source_extraction']).resolve()),None)
    if (not match or match['input_state_sha256']!=proof['source_input_sha256']
            or match.get('visual_category')!=proof['visual_category'] or match.get('crop_hashes',{})!=proof['crop_hashes']
            or any(sha256(p)!=digest for p,digest in proof['crop_hashes'].items())
            or proof['expert_approval'] or proof['numeric_evidence_eligible']):
        raise ValueError('Visual review binding differs from its source')
