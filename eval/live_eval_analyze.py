"""Independent offline aggregation of live responses and explicit source reviews."""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from eval.current_server_eval import digest, read, write


def analyze(folder, require_complete=False):
    freeze, cases = read(folder/'frozen.json'), read(folder/'cases.json')
    rubric = read(folder/'rubric.json')['required_fact_groups']
    automatic = read(folder/'automatic.json')
    if digest(folder/'cases.json') != freeze['cases_sha256']: raise ValueError('Frozen cases changed')
    for group in [freeze['code_sha256'],read(folder/'additional_freeze.json')['sha256']]:
        for name, value in group.items():
            if digest(ROOT/name) != value: raise ValueError(f'Frozen input changed: {name}')
    if digest(folder/'rubric.json') != read(folder/'rubric_freeze.json')['sha256']: raise ValueError('Rubric changed')
    with (ROOT/'eval/labels_manual_v0.1.csv').open(encoding='utf-8-sig',newline='') as stream:
        labels = list(csv.DictReader(stream))
    judgments = read(folder/'source_judgments.json')['rows'] if (folder/'source_judgments.json').exists() else []
    judged = {j['id']:j for j in judgments}
    if len(judged) != len(judgments): raise ValueError('Duplicate judgments')
    rows, checklist = [], []
    for case in cases:
        path = folder/f"responses/{case['id']}.json"
        if not path.exists(): continue
        report = read(path)
        units = report['results']
        observed = all('retrieved_pages' in u for u in units)
        pages = [p for u in units for p in u.get('retrieved_pages',[])]
        original = set(case['gold_pages'])
        equivalent = {int(l['pdf_page']) for l in labels if l['query_id']==case['id']
            and l['document_id']==case['document_id'] and l['grade']=='2' and l['partial']=='0'}
        augmented = original | equivalent
        rank = next((i for i,p in enumerate(pages,1) if p['document_id']==case['document_id']
            and p['version_id']==case['version_id'] and p['pdf_page'] in augmented),None)
        context_hit = any(b['document_id']==case['document_id'] and b['version_id']==case['version_id']
            and b['pdf_page'] in augmented for u in units for b in u.get('evidence',{}).values())
        explicit = case['payload'].get('jurisdictions',[])
        scope_conflict = bool(explicit) and any(p['jurisdiction'] not in explicit for p in report['plan'])
        products = case['payload'].get('products',[])
        product_conflict = bool(products) and any(p['product'] not in products for p in report['plan'])
        item_count = sum(len(u['items']) for u in units)
        row = {'id':case['id'],'expected':case['expected'],'topic':case['topic'],
            'original_gold_pages':sorted(original),'qualified_alternative_pages':sorted(augmented-original),
            'qualified_page_rank':rank,'qualified_context_hit':context_hit,
            'planned_agencies':[p['jurisdiction'] for p in report['plan']],
            'request_agencies':explicit,'request_scope_conflict':scope_conflict,
            'request_products':products,'request_product_conflict':product_conflict,
            'retrieval_observed':observed,
            'unit_errors':[u.get('error') for u in units if u['status']=='error'],
            'omitted_chunks':sum(len(u.get('omitted_chunks',[])) for u in units),
            'accepted_items':item_count,'required_fact_groups':len(rubric[case['id']]),
            'judgment':judged.get(case['id'])}
        if row['judgment']:
            review = row['judgment']
            if not 0 <= review['correct_fact_groups'] <= row['required_fact_groups']: raise ValueError('Invalid fact count')
            if not 0 <= review['requested_scope_supported_items'] <= item_count: raise ValueError('Invalid item count')
            if not review.get('reason') or not review.get('source_pages'): raise ValueError('Review lacks source rationale')
        rows.append(row)
        checklist += [f"## {case['id']}: {case['question']}",f"Expected: {case['expected']}",
            f"Required fact groups: {json.dumps(rubric[case['id']],ensure_ascii=False)}",
            f"Plan: {row['planned_agencies']}; pages: {[p['pdf_page'] for p in pages]}"]
        for unit in units:
            checklist.append(f"Status: {unit['status']}; {unit['answer_ko']}")
            for n,item in enumerate(unit['items'],1):
                references = [(eid,unit['evidence'][eid]['pdf_page'],unit['evidence'][eid]['structure'].get('kind'))
                    for eid in item['evidence']]
                checklist += [f"Item {n}: {item['source_label']} | {item['value']} | study={item['study']} | exact={item['exact']}",
                    f"Quote: {item['quote']}",f"Evidence: {references}"]
            if unit.get('validation',{}).get('rejected_items'):
                checklist.append('Rejected: '+json.dumps(unit['validation']['rejected_items'],ensure_ascii=False))
        checklist.append('')
    if require_complete:
        if len(rows)!=len(cases) or len(judged)!=len(cases) or not (folder/'completed.json').exists():
            raise ValueError('Responses or source judgments incomplete')
    answerable = [r for r in rows if r['expected']=='answerable']
    reviewed = [r for r in answerable if r['judgment']]
    controls = [r for r in rows if r['expected']!='answerable' and r['judgment']]
    items = sum(r['accepted_items'] for r in reviewed)
    verdicts = Counter(r['judgment']['verdict'] for r in reviewed)
    analysis = {'scope':'Source-based agent judgments against draft gold and original sources; expert review pending.',
        'n_responses':len(rows),'n_expected':len(cases),'n_answerable':len(answerable),
        'retrieval_unknown_after_unit_error_n':sum(not r['retrieval_observed'] for r in answerable),
        'retrieval_metric_note':'Hit/MRR over all answerable requests are lower bounds when a unit error hides retrieval; unknown errors remain in the end-to-end denominator.',
        'qualified_page_hit@5':sum(r['qualified_page_rank'] is not None and r['qualified_page_rank']<=5 for r in answerable)/len(answerable) if answerable else None,
        'qualified_mrr@5':sum(1/r['qualified_page_rank'] for r in answerable if r['qualified_page_rank'] is not None and r['qualified_page_rank']<=5)/len(answerable) if answerable else None,
        'qualified_context_hits':sum(r['qualified_context_hit'] for r in answerable),
        'request_scope_conflicts':sum(r['request_scope_conflict'] for r in rows),
        'request_product_conflicts':sum(r['request_product_conflict'] for r in rows),
        'reviewed_answerable':len(reviewed),'verdict_counts':dict(verdicts),
        'complete_accuracy':verdicts['correct']/len(reviewed) if reviewed else None,
        'correct_fact_groups':sum(r['judgment']['correct_fact_groups'] for r in reviewed),
        'required_fact_groups':sum(r['required_fact_groups'] for r in reviewed),
        'fact_group_recall':sum(r['judgment']['correct_fact_groups'] for r in reviewed)/sum(r['required_fact_groups'] for r in reviewed) if reviewed else None,
        'requested_scope_supported_items':sum(r['judgment']['requested_scope_supported_items'] for r in reviewed),
        'reviewed_items':items,
        'requested_scope_item_precision':sum(r['judgment']['requested_scope_supported_items'] for r in reviewed)/items if items else None,
        'control_verdict_counts':dict(Counter(r['judgment']['verdict'] for r in controls)),
        'failure_categories':dict(Counter(category for r in rows if r['judgment'] for category in r['judgment'].get('failure_categories',[]))),
        'automatic':automatic['summary'],'rows':rows,
        'token_usage_note':'Reported usage is a lower bound when failed model calls are not retained by the live pipeline.',
        'all_frozen_files_unchanged':True,'evaluator_annotations_not_added_to_frozen_index':True}
    write(folder/'analysis.json',analysis)
    (folder/'review_checklist.txt').write_text('\n'.join(checklist),encoding='utf-8')
    with (folder/'case_results.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer = csv.DictWriter(stream,fieldnames=['id','topic','expected','qualified_page_rank','qualified_context_hit',
            'request_scope_conflict','request_product_conflict','accepted_items','verdict','correct_fact_groups','required_fact_groups','reason'])
        writer.writeheader()
        for r in rows:
            review = r['judgment'] or {}
            writer.writerow({**{k:r[k] for k in writer.fieldnames if k in r},
                **{k:review.get(k,'') for k in ('verdict','correct_fact_groups','reason')}})
    print(json.dumps({k:v for k,v in analysis.items() if k not in ('rows','automatic')},ensure_ascii=False,indent=2))
    return analysis


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--require-complete',action='store_true')
    args=parser.parse_args()
    analyze(args.run,args.require_complete)
