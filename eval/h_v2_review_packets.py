"""Export source-linked review packets, without provider calls or gold feedback."""
from collections import Counter
import argparse
import json
from pathlib import Path


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))


def export(root, phase):
    case_path=(Path(__file__).resolve().with_name('h_v2_transfer_cases_2026-10-14.json')
               if phase=='transfer_utf8' else
               Path(__file__).resolve().with_name('statistical_table_holdout_cases_2026-10-14.json')
               if phase=='statistical_holdout' else root/('transfer_cases.json' if phase=='transfer' else 'cases.json'))
    case_data=read(case_path)
    cases=case_data['cases'] if isinstance(case_data,dict) else case_data
    folder=root/phase
    packet={}; compact=[]
    for case in cases:
        arms={}
        for arm in ('baseline','v2'):
            path=folder/arm/(case['id']+'.json')
            if not path.exists():continue
            report=read(path);r=report['results'][0];evidence=r['evidence']
            claims=[]
            for c in r['claims']:
                sources=[]
                for s in c['sources']:
                    e=evidence[s['evidence']]
                    sources.append({**s,'document_id':e['document_id'],'version_id':e['version_id'],
                        'pdf_page':e['pdf_page'],'header':e.get('header'),'structure':e.get('structure')})
                claims.append({'id':c['id'],'text':c['text'],'sources':sources})
            rejected=r['validation']['rejected_items']
            reasons=Counter(str(c.get('reason') or c.get('citation_error') or 'unspecified') for c in rejected)
            arms[arm]={'question':report['question'],'status':r['status'],'answer_ko':r['answer_ko'],
                'claims':claims,'rejected':rejected,'rejection_reasons':dict(reasons),
                'context_budget':r.get('context_budget'),'research':report.get('research'),
                'requirements':r.get('question_requirement_coverage'),'timings':report['timings'],
                'retrieval':report['retrieval'],'report_path':str(path),
                'source_evidence':evidence,'new_provider_calls':report['development_execution']['new_provider_calls']}
            compact.append({'case':case['id'],'arm':arm,'status':r['status'],
                'claims':[c['text'] for c in claims],'rejected':dict(reasons),
                'packets':len(evidence),'new_calls':report['development_execution']['new_provider_calls']})
        if arms:packet[case['id']]={'question':case['question'],'required_facets':case.get('required_facets'),
                                  'control':case.get('control'),'arms':arms}
    dest=folder/'review_packets.json'
    dest.write_text(json.dumps(packet,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (folder/'compact_answers.json').write_text(json.dumps(compact,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'packets':len(packet),'reports':len(compact),'output':str(dest)},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--phase',default='paired')
    a=p.parse_args();export(a.out,a.phase)
