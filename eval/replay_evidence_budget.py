"""Replay frozen retrieved pages without interpretation, embeddings or generation."""
import argparse
import json
from pathlib import Path
import sys
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rag.budgeted_evidence import BudgetedEvidence
from rag.local_tokenizer import for_model
from rag.preprocessed_prompt import build
from rag.preprocessed_evidence import EXTRA_RULES
from rag.answer_conditions import QUOTE_RULES
from review_viewer.data import Bundle


def configure(adapter, question, understood, unit, pages, output=2200, condition_rules=''):
    counter=for_model()
    limit=8192-output-256
    make=lambda blocks:build(question,understood,unit,blocks,EXTRA_RULES,condition_rules)
    base=counter.messages(make({}))
    n=max(1,len({(p['document_id'],p['version_id'],p['pdf_page']) for p in pages}))
    adapter.budget={'counter':counter,'fits':lambda blocks:counter.messages(make(blocks))<=limit,
        'query':understood['search_terms'],'core_tokens':max(96,(limit-base-350*n)//n),
        'context_tokens':8192,'output_reserved_tokens':output,'input_limit':limit,
        'base_prompt_tokens':base,'token_count_basis':counter.basis}
    return counter,make


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,default=Path('data/runs/rag_updates_2026-10-09/stage1_scope'))
    parser.add_argument('--out',type=Path,default=Path('data/runs/rag_updates_2026-10-09/stage3_evidence'))
    parser.add_argument('--output-tokens',type=int,default=2200)
    parser.add_argument('--ids',nargs='*')
    parser.add_argument('--conditions',action='store_true')
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    b=Bundle('data/preprocessed/pipeline_review_2026-10-08','data/rag/candidates/study_candidates_2026-10-09_v2')
    chunks=[c for rows in b.chunks_by_page.values() for c in rows]
    adapter=BudgetedEvidence(b,chunks,b.chunks_sha256,'http://127.0.0.1:8766')
    by_page={}
    for i,c in enumerate(chunks):by_page.setdefault((c['document_id'],c['version_id'],c['pdf_page']),[]).append(i)
    summary=[]
    for path in sorted((args.run/'responses').glob('*.json')):
        if '.job.' in path.name or '.request.' in path.name:continue
        if args.ids and path.stem not in args.ids:continue
        report=json.loads(path.read_text(encoding='utf-8'))
        for result in report['results']:
            pages=result.get('retrieved_pages')
            if not pages:continue
            corpus=SimpleNamespace(include_nondefault=report['scope']['include_nondefault'],
                allowed_documents={(p['document_id'],p['version_id']) for p in pages},by_page=by_page)
            counter,make=configure(adapter,report['question'],report['understanding'],result['unit'],pages,args.output_tokens,
                QUOTE_RULES if args.conditions else '')
            blocks=adapter.from_pages(pages,corpus,None,result['unit'])
            supplied=sorted({p['pdf_page'] for p in blocks.values()})
            for block in blocks.values():
                reconstructed=''.join(s['separator_before']+b.units[s['unit_id']]['text'][s['representation_start']:s['representation_end']]
                    for s in block['source_spans'])
                if reconstructed!=block['text']:raise ValueError('Excerpt source range reconstruction failed')
            row={'id':path.stem,'retrieved_pages':[p['pdf_page'] for p in pages],
                'supplied_pages':supplied,'previous_supplied_pages':sorted({x['pdf_page'] for x in result['evidence'].values()}),
                'estimated_prompt_tokens':counter.messages(make(blocks)), 'budget':adapter.last_budget,
                'omitted':adapter.last_omitted,'source_ranges_verified':True}
            (args.out/(path.stem+'.json')).write_text(json.dumps({'check':row,'evidence':blocks,'messages':make(blocks)},ensure_ascii=False,indent=2),encoding='utf-8')
            summary.append(row)
    (args.out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps([r for r in summary if r['id'] in ['Q014','D06','D02']],ensure_ascii=False,indent=2))


if __name__=='__main__':main()
