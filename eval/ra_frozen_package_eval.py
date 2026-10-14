"""Question-only evaluation of a frozen RA candidate; no gold-guided retrieval."""
import argparse
import hashlib
import json
from pathlib import Path

from eval.ra_pipeline_update_eval import RELEASE, GUIDELINES, ROOT, read, digest, write
from rag.openai_chat import EvaluationAPIChat
from rag.ra_pipeline import RAPipeline


def run(out):
    frozen=read(out/'candidate.json')
    if any(digest(ROOT/name)!=value for name,value in frozen['code_sha256'].items()):
        raise RuntimeError('Frozen candidate changed before package execution')
    files=['qa_gold_indomain_test.jsonl','qa_gold_external_holdout.jsonl']
    package=ROOT/'validation_report'
    # Discard answer/annotation fields immediately. They never enter planning,
    # search, source supply, generation or internal selected-source review.
    questions=[{key:row[key] for key in ['question_id','question','product_scope','agency','split']}
        for name in files for line in (package/name).read_text(encoding='utf-8').splitlines() if line.strip()
        for row in [json.loads(line)]]
    root=out/'package_eval'
    audit=read(out/'validation_package_audit.json')
    # A product overview does not supply the assessment report annotated in
    # this package. Check the exact package document's indexed coverage.
    annotated_coverage={d['product_id']:d['indexed_chunks']>0 for d in audit['source_documents']}
    write(root/'protocol.json',{'candidate_sha256':digest(out/'candidate.json'),
        'runner_sha256':digest(Path(__file__)), 'package_sha256':{name:digest(package/name) for name in files},
        'question_ids':[q['question_id'] for q in questions], 'gold_sent':False,
        'automatic_retries':0,'code_tuning_after_execution':False,
        'independent_holdout':False,'limits':['Prior gold preview exposure; mechanical approval only.',
            'Unavailable external documents are corpus coverage failures, not executed synthesis tests.']})
    client=EvaluationAPIChat(root,max_new_calls=9,model='gpt-4o-mini')
    pipeline=RAPipeline(RELEASE,guideline_snapshot=GUIDELINES,chat_client=client)
    from rag.ra_requirements import named_products
    products={d['product_id'] for d in pipeline.documents if d['product_id']}
    rows=[]
    try:
        for q in questions:
            key=q['question_id']
            requested=set(named_products(' '.join(q['product_scope'])))
            agency={'EMA':'EU','FDA':'US','HC':'CA'}[q['agency']]
            available={d['product_id'] for d in pipeline.documents if d['jurisdiction']==agency}
            if (not requested or not requested<=products or not requested<=available
                    or not all(annotated_coverage.get(p,False) for p in requested)):
                row={'id':key,'split':q['split'],'status':'corpus_unavailable','executed':False,
                    'products':sorted(requested),'new_api_calls':0}
            else:
                client.case=key
                print(key,'framework execution',flush=True)
                try:
                    report=pipeline.ask(q['question'],products=sorted(requested),jurisdictions=[agency],
                        progress=lambda **event:print(event['stage'],flush=True))
                    write(root/(key+'.json'),report)
                    result=report['results'][0]
                    row={'id':key,'split':q['split'],'status':result['status'],'executed':True,
                        'claims':len(result['claims']),'rejected':len(result['validation']['rejected_items']),
                        'usage':report['usage'],'seconds':report['seconds']}
                except Exception:
                    write(root/(key+'.failure.json'),pipeline.last_failure)
                    row={'id':key,'split':q['split'],'status':'failed','executed':True,
                        'error':pipeline.last_failure['error']}
                    if client.halted: raise
            rows.append(row)
            write(root/(key+'.summary.json'),row)
            print(json.dumps(row,ensure_ascii=False),flush=True)
        if any(digest(ROOT/name)!=value for name,value in frozen['code_sha256'].items()):
            raise RuntimeError('Frozen candidate changed during execution')
        write(root/'summary.json',{'rows':rows,'gold_sent':False,'independent_holdout':False})
    finally:
        pipeline.close();client.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',required=True,type=Path)
    run(parser.parse_args().out.resolve())
