"""Score frozen source-first agent visual spotchecks; never releases expert Gold."""
import argparse
import json
from pathlib import Path
import sys
import unicodedata
import re
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from preprocess.visual_trial import verify_trial
from preprocess.hybrid_validate import validate_extraction
from preprocess.hybrid import read_jsonl
from preprocess.ingest import sha256
from preprocess.quality_eval import table_checks, overlap


def literal(value):
    return re.sub(r'\s+',' ',unicodedata.normalize('NFKC',value or '')).strip().casefold()


def evaluate(specification,run,out):
    folder,run,out=map(lambda p:Path(p).resolve(),(specification,run,out))
    spec=verify_trial(folder);validate_extraction(run)
    gold=folder/'source_annotations.json'
    if sha256(gold)!=json.loads((folder/'annotation_seal.json').read_text(encoding='utf-8'))['sha256']:
        raise ValueError('Source annotations changed')
    execution=json.loads((run/'trial_execution.json').read_text(encoding='utf-8'))
    if execution['source_annotations_sha256']!=sha256(gold) or execution['trial_sha256']!=sha256(folder/'trial.json'):
        raise ValueError('Run did not use the frozen source/code trial')
    annotations=json.loads(gold.read_text(encoding='utf-8'))
    assets=read_jsonl(run/'extracted_assets.jsonl');groups=read_jsonl(run/'visual_groups.jsonl')
    units={u['unit_id']:u for u in read_jsonl(run/'source_units.jsonl')}
    by_id={a['asset_id']:a for a in assets};results=[]
    for case in annotations['cases']:
        result={k:case[k] for k in ('case_id','document_id','version_id','pdf_page','kind')}
        group=next((g for g in groups if g['document_id']==case['document_id'] and g['version_id']==case['version_id']
                    and case['pdf_page'] in g['pdf_pages'] and g['label']==case['group_label']),None)
        result['caption_group_found']=group is not None
        matches=[a for a in assets if a['document_id']==case['document_id'] and a['version_id']==case['version_id']
                 and a['pdf_page']==case['pdf_page'] and a['asset_type']==case['kind']]
        matches.sort(key=lambda a:overlap(a['bbox_pdf'],case['bbox_pdf']),reverse=True)
        asset=matches[0] if matches and overlap(matches[0]['bbox_pdf'],case['bbox_pdf'])>=.45 else None
        result['physical_region_found']=asset is not None
        if case['kind']=='table':
            result['literal_checks_by_stage']={}
            for stage,content in [('base',asset['base']['content'] if asset else ''),('selected',asset['selected']['content'] if asset else '')]:
                result['literal_checks_by_stage'][stage]=table_checks(content,case['spotchecks'])
            result['selected_retrieval_eligible']=units[asset['asset_id']]['retrieval_eligible'] if asset else False
            result['review_required']=units[asset['asset_id']]['review_required'] if asset else None
            result['validation_issues']=(asset['base']['quality']['issues'] if asset else [])
        else:
            parent=by_id[group['group_id']] if group else asset
            fields=(parent or {}).get('figure_labels',{}).get('fields',[])
            checked=[]
            for f in case['literal_fields']:
                matches=[g for g in fields if g['role']==f['role'] and literal(g['text'])==literal(f['literal'])]
                checked.append({**f,'candidate_exact_match':bool(matches),
                    'accepted_literal_exact_match':any(g['status']=='text_supported' for g in matches)})
            result['literal_field_checks']=checked
            result['layout_regions_in_parent']=len(group['member_asset_ids']) if group else None
            result['selected_retrieval_eligible']=units[parent['asset_id']]['retrieval_eligible'] if parent else False
            result['role_or_clinical_accuracy_measured']=False
        results.append(result)
    stages={}
    for stage in ['base','selected']:
        checks=[c for r in results for c in r.get('literal_checks_by_stage',{}).get(stage,[])]
        stages[stage]={'passed':sum(c['passed'] for c in checks),'total':len(checks)}
    figchecks=[c for r in results for c in r.get('literal_field_checks',[])]
    result={'trial_version':spec['version'],'source_annotations_sha256':sha256(gold),
        'scorer_sha256':sha256(__file__),'scope':annotations['scope'],'annotation_status':annotations['annotation_status'],
        'domain_expert_reviewed':False,'strict_holdout':False,'clinical_semantics_reviewed':False,
        'source_pages':14,'annotated_pages':4,'cases':len(results),'table_cell_spotchecks':stages,
        'figure_field_spotchecks':{'total':len(figchecks),'candidate_exact_match':sum(c['candidate_exact_match'] for c in figchecks),
            'accepted_literal_exact_match':sum(c['accepted_literal_exact_match'] for c in figchecks)},
        'caption_groups_found':sum(r['caption_group_found'] for r in results),
        'results':results,'deployment_gate':'Not passed: human visual evidence and exhaustive field/ownership coverage pending.'}
    out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return {k:v for k,v in result.items() if k!='results'}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--specification',type=Path,required=True)
    p.add_argument('--run',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();print(json.dumps(evaluate(a.specification,a.run,a.out),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
