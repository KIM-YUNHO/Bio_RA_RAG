"""Source-bound literal spotchecks, never an automatic clinical gold generator."""
import argparse
import json
from pathlib import Path
import re
import unicodedata

from .hybrid import read_jsonl
from .inventory import write_json
from .table_quality import inspect_table, comparable_text, NUMBER
from .ingest import sha256


def norm(text):
    text = comparable_text(text or '').replace('\\mu','μ').replace('\\cdot','·')
    return re.sub(r'[\s{}$]', '', unicodedata.normalize('NFKC',text)).casefold()


def numeric_sequence(text):
    return [re.sub(r',(?=\d{3}(?:\D|$))','',n) for n in NUMBER.findall(comparable_text(text))]


def grid(html):
    quality=inspect_table(html)
    result=[['']*quality['columns'] for _ in range(quality['rows'])]
    for cell in quality['cells']:
        for r in range(cell['row'],cell['row']+cell['row_span']):
            for c in range(cell['column'],cell['column']+cell['col_span']):
                result[r][c]=cell['text']
    return result


def table_checks(html, checks):
    expanded=grid(html)
    outcomes=[]
    for check in checks:
        candidates=[row for row in expanded if len(row)>=len(check['row_prefix']) and all(
            wanted is None or (norm(row[i])==norm(wanted) if norm(wanted).isdigit() or check.get('exact_row_labels')
                              else norm(row[i]).startswith(norm(wanted)))
            for i,wanted in enumerate(check['row_prefix']))]
        def same(row, col, expected):
            if col>=len(row):
                return False
            if expected=='':
                return not row[col].strip()
            numbers=numeric_sequence(expected)
            return numeric_sequence(row[col])==numbers if numbers else norm(row[col])==norm(expected)
        def redacted(row, col):
            if col>=len(row):
                return False
            label=re.sub(r'\(b\)\s*\([46]\)|\[?(?:redacted|unreadable)\]?', '',row[col],flags=re.I).strip()
            return not label
        # Ambiguous prefixes cannot pass by assembling cells from different rows.
        consistent = [row for row in candidates if all(same(row,int(c),value) for c,value in check['cells'].items())
                      and all(redacted(row,c) for c in check.get('redacted_columns',[]))]
        eligible = candidates if len(candidates)==1 else consistent
        for c,expected in check['cells'].items():
            col=int(c)
            outcomes.append({'row_prefix':check['row_prefix'],'column':col,'expected':expected,
                'passed':any(same(row,col,expected) for row in eligible),
                'observed':[row[col] if col<len(row) else None for row in candidates]})
        for col in check.get('redacted_columns',[]):
            outcomes.append({'row_prefix':check['row_prefix'],'column':col,'expected':'no visible label propagated into redacted rows',
                'passed':any(redacted(row,col) for row in eligible),
                'observed':[row[col] if col<len(row) else None for row in candidates]})
    return outcomes


def overlap(a,b):
    area=lambda r:max(0,r[2]-r[0])*max(0,r[3]-r[1])
    intersection=[max(a[0],b[0]),max(a[1],b[1]),min(a[2],b[2]),min(a[3],b[3])]
    return area(intersection)/max(1,area(a)+area(b)-area(intersection))


def evaluate(run, gold, output):
    run=Path(run).resolve()
    contract=json.loads(Path(gold).read_text(encoding='utf-8'))
    manifest=json.loads((run/'extraction_manifest.json').read_text(encoding='utf-8'))
    versions={d['document_id']:d['version_id'] for d in manifest['documents']}
    for case in contract['cases']:
        if versions.get(case['document_id']) != contract.get('document_versions',{}).get(case['document_id']):
            raise ValueError('Gold annotations do not match the evaluated source version')
    sample=json.loads((run/'sample_manifest.json').read_text(encoding='utf-8'))
    for d in sample['documents']:
        if versions[d['document_id']]!=d['version_id'] or sha256(d['raw_path'])!=d['version_id']:
            raise ValueError('Quality pilot source hash mismatch')
    assets=read_jsonl(run/'extracted_assets.jsonl')
    lookup={a['asset_id']:a for a in assets}
    groups=read_jsonl(run/'visual_groups.jsonl') if (run/'visual_groups.jsonl').exists() else []
    units={u['unit_id']:u for u in read_jsonl(run/'source_units.jsonl')}
    results=[]
    for case in contract['cases']:
        result={'case_id':case['case_id'],'document_id':case['document_id'],'version_id':versions[case['document_id']],
                'kind':case['kind'],'pdf_page':case.get('pdf_page')}
        source=[a for a in assets if a['document_id']==case['document_id'] and not a.get('is_logical_group')]
        group=next((g for g in groups if g['document_id']==case['document_id'] and g['label']==case.get('group_label')
                    and (not case.get('pdf_page') or case['pdf_page'] in g['pdf_pages'])),None)
        if case['kind']=='negative':
            actual=[a['asset_id'] for a in source if a['pdf_page']==case['pdf_page'] and a['asset_type'] in {'table','figure'}]
            result.update(passed=len(actual)==case['expected_visual_regions'],detected_regions=actual)
        elif case['kind']=='group_relation':
            passed=bool(group and group['pdf_pages']==case['pdf_pages'] and len(group['member_asset_ids'])==case['member_count']
                        and group['continuation_status']==case['relation'])
            result.update(passed=passed,group=group)
        else:
            matches=[a for a in source if a['pdf_page']==case['pdf_page'] and a['asset_type']==case['kind']]
            matches.sort(key=lambda a:overlap(a['bbox_pdf'],case['bbox_pdf']),reverse=True)
            found=matches[0] if matches and (overlap(matches[0]['bbox_pdf'],case['bbox_pdf'])>=.45 or
                case['kind']=='figure' and len(matches)>1 and group is not None) else None
            result['detected']=bool(found)
            result['source_ref']={'pdf_page':case['pdf_page'],'bbox_pdf':case['bbox_pdf']}
            if case['kind']=='table':
                result['checks_by_stage']={}
                for stage,html in [('base',found['base']['content'] if found else ''),('selected',found['selected']['content'] if found else '')]:
                    result['checks_by_stage'][stage]=table_checks(html,case['spotchecks'])
                answer=(found.get('enrichment') or {}).get('validation',{}).get('answer') if found else None
                if answer:
                    result['checks_by_stage']['qwen_candidate']=table_checks(answer['table_html'],case['spotchecks'])
                selected=result['checks_by_stage']['selected']
                result['passed']=bool(found) and all(c['passed'] for c in selected)
                if case.get('must_review'):
                    result['unsafe_retrieval_blocked']=bool(found and not units[found['asset_id']]['retrieval_eligible'])
            else:
                result['group_preserved']=bool(group and len(group['member_asset_ids'])==case['member_count'])
                parent=lookup.get(group['group_id']) if group else found
                answer=((parent or {}).get('enrichment') or {}).get('validation',{}).get('answer')
                checks=[]
                for key,wanted in case['figure_assertions'].items():
                    value=answer.get(key) if answer else '__missing__'
                    passed=value is None if wanted is None else value is not None and all(
                        norm(w) in norm(json.dumps(value,ensure_ascii=False)) for w in wanted)
                    checks.append({'field':key,'passed':passed,'observed':value,'expected':wanted})
                result.update(figure_checks=checks,passed=bool(found and result['group_preserved'] and all(c['passed'] for c in checks)))
                result['one_parent_retrieval_unit']=bool(group and group['group_id'] in units and
                    all(not units[x]['retrieval_eligible'] for x in group['member_asset_ids']))
                result['group_id']=group['group_id'] if group else None
        results.append(result)
    stages={}
    for stage in ('base','selected','qwen_candidate'):
        checks=[c for result in results for c in result.get('checks_by_stage',{}).get(stage,[])]
        stages[stage]={'passed':sum(c['passed'] for c in checks),'total':len(checks)}
    report={'reference_version':contract['reference_version'],'gold_sha256':sha256(gold),'run':str(run),
        'scope':contract['scope'],'domain_expert_reviewed':contract['domain_expert_reviewed'],
        'cases':len(results),'case_passes':sum(r['passed'] for r in results),
        'cell_spotchecks':stages,'figure_groups_preserved':sum(r.get('group_preserved',False) for r in results),
        'figure_cases':sum(r['kind']=='figure' for r in results),
        'unsafe_retrieval_blocked':sum(r.get('unsafe_retrieval_blocked',False) for r in results),
        'cases_requiring_retrieval_block':sum(bool(c.get('must_review')) for c in contract['cases']),
        'results':results}
    write_json(output,report)
    return {k:v for k,v in report.items() if k!='results'}


def main(argv=None):
    parser=argparse.ArgumentParser(description='Evaluate source-reviewed literal RA visual spotchecks')
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--gold',type=Path,default=Path(__file__).resolve().parents[1]/'configs/preprocess.quality.gold.json')
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args(argv)
    print(json.dumps(evaluate(args.run,args.gold,args.out),ensure_ascii=True,indent=2))


if __name__=='__main__':
    main()
