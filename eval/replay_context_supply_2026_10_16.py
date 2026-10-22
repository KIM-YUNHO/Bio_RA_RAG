"""Exercise production context assembly with fixed source seeds, no model calls.

This is a controlled context-stage probe, not an end-to-end retrieval benchmark.
"""
from collections import Counter
import argparse
from copy import deepcopy
import json
import hashlib
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eval.audit_context_links_2026_10_16 import OUT, read, rows, save
from rag.index.portable_paths import resolve
from rag.evidence.budgeted_evidence import BudgetedEvidence
from rag.llm.openai_chat import ApiTokenizer
from rag.ra import ra_retrieval, ra_answer


def main():
    global OUT
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=OUT)
    parser.add_argument('--expect',choices=('baseline','fixed'),default='baseline')
    args=parser.parse_args()
    OUT=args.out.resolve()
    # save() belongs to the audit module; keep all new results in this run.
    import eval.audit_context_links_2026_10_16 as audit
    audit.OUT=OUT
    OUT.mkdir(parents=True, exist_ok=True)
    registry = read(ROOT/'data/versions/versions.json')
    version = next(v for v in registry['versions'] if v['id']==registry['roles']['served_by_ra_service'])
    release = ROOT/version['embedded_path']; folder=release/'candidate'
    chunks = rows(folder/'chunks.jsonl'); summary = read(folder/'chunks_summary.json')
    assets, docmap, documents = {}, {}, {}
    for component in read(release/'release_manifest.json')['components']:
        run = resolve(component['run'])
        assets.update({a['asset_id']:a for a in rows(run/'extracted_assets.jsonl')})
        for d in read(run/'extraction_manifest.json')['documents']:
            docmap[(d['document_id'],d['version_id'])] = f'd{len(docmap)+1}'
            documents[d['document_id']] = d
    bundle = SimpleNamespace(candidate=folder,units={u['unit_id']:u for u in rows(folder/'source_units.jsonl')},
        assets=assets,doc_by_identity=docmap,chunks_sha256=summary['chunks_sha256'])
    adapter = BudgetedEvidence(bundle,chunks,summary['chunks_sha256'])
    corpus=SimpleNamespace(chunks=chunks,evidence_adapter=adapter,allowed_documents=None)
    # Uses production token accounting; this object has no transport/session.
    client=SimpleNamespace(tokenizer=ApiTokenizer('gpt-5.6-luna'),output_tokens=3000,input_limit=13128)
    template=read(ROOT/'data/runs/answer_eval_2026-10-15/paired_dev/branch/E03_1.json')['understanding']
    cases=[
        ('healthy_table5',44,'table_candidate','Table 5.',
         'EMA ABP 654 Study 20190230 건강인 PK 시험의 용량·경로·대상자 수와 AUCinf 결과를 알려줘.',
         ['20190230 healthy subjects dose route single injection AUCinf']),
        ('psoriasis_ref_table5',45,'text','From baseline to week 28',
         'EMA ABP 654 Study 20190232 건선 환자의 week 28 최저혈중농도 결과를 알려줘.',
         ['20190232 psoriasis week 28 trough serum concentrations']),
        ('psoriasis_table49',103,'table_candidate','Table 49.',
         'EMA ABP 654 Study 20190232에서 ADA 상태별 week 28 최저혈중농도 결과를 알려줘.',
         ['20190232 ADA week 28 trough concentrations']),
        ('psoriasis_ref_table47',103,'text','To further evaluate the potential impact of ADA',
         'EMA ABP 654 Study 20190232에서 ADA 상태에 따른 week 12 PASI 개선 결과를 알려줘.',
         ['20190232 ADA efficacy PASI week 12 improvement']),
    ]
    result=[]
    for key,page,kind,needle,question,queries in cases:
        seeds=[c for c in chunks if c['document_id']=='EMA:64196' and c['pdf_page']==page
               and c['structure']['kind']==kind and needle in c['text']]
        if len(seeds)!=1:raise ValueError(f'{key}: non-unique seed: {len(seeds)}')
        seed=seeds[0];understanding=deepcopy(template)
        understanding.update(products=['ABP654'],jurisdictions=['EU'],intent='lookup',english_query=' '.join(queries),
                             source_question=question)
        task=understanding['tasks'][0]
        task.update(request=question,queries=queries,intent={'intent':'lookup','request_quote':question},
                    original_request_segments=[question])
        # The context code reads request/queries directly; no planning or generation.
        group={'task_id':task['id'],'product':'ABP654','jurisdiction':'EU','queries':queries,
               'hits':[{'score':1.0,'chunk':seed}]}
        evidence,budget=ra_retrieval.context(corpus,[group],question,understanding,client,'spans')
        msgs=ra_answer.messages(question,understanding,evidence,'spans')
        result.append({'case':key,'question':question,'seed_chunk_id':seed['chunk_id'],
                       'evidence':evidence,'context_budget':budget,'generation_messages':msgs,
                       'no_provider_calls':True})
        print(json.dumps({'case':key,'sources':[(e['pdf_page'],e['retrieval_role'],
             (e.get('structure') or {}).get('caption'),e['text'][:110]) for e in evidence.values()],
             'omitted':budget.get('omitted')},ensure_ascii=False))
    save('controlled_context_replays.json',result)
    # Existing reports separate supplied evidence from final retained statements.
    report_rows=[]
    for case in ('E03','T01','T02','T03','T04'):
        path=ROOT/f'data/runs/answer_eval_2026-10-15/paired_dev/branch/{case}_1.json'
        d=read(path);r=d['results'][0]
        rejected=r.get('validation',{}).get('rejected_items',[])
        report_rows.append({'case':case,'path':str(path),'question':d['question'],
            'pipeline_version':d.get('pipeline_version'),'created_at':d.get('created_at'),
            'status':r['status'],'final_claims':[c['text'] for c in r['claims']],
            'rejected':[{'text':c.get('text'),'reason':c.get('reason') or c.get('citation_error')} for c in rejected],
            'evidence':r['evidence'],'context_budget':r.get('context_budget'),
            'validation_keys':list(r.get('validation',{}))})
    save('saved_evidence_trace.json',report_rows)
    from rag.ra import ra_quantities
    original=read(ROOT/'data/runs/answer_eval_2026-10-15/paired_dev/branch/E03_1.json')['results'][0]
    rechecks=[]
    for claim in original['validation']['rejected_items']:
        if claim.get('reason')=='claim_quantity_value_or_unit_not_in_final_citations':
            rechecks.append({'claim':claim['text'],'saved_reason':claim['reason'],
                'recheck_reason':ra_quantities.check(claim,original['evidence']),
                'source_quotes':[{'evidence':s['evidence'],'page':original['evidence'][s['evidence']]['pdf_page'],
                                 'quote':s['quote']} for s in claim['sources']]})
    save('eligibility_citation_recheck.json',rechecks)
    # Verify the actual source PDF and retain full relevant pages, not snippets.
    import fitz
    docmeta=documents['EMA:64196'];pdf_path=resolve(docmeta['raw_path'])
    pdf_hash=hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    assert pdf_hash==docmeta['version_id']
    pdf=fitz.open(pdf_path)
    pages=(42,43,44,45,46,83,84,85,101,102,103,104)
    save('abp654_ema_source_pages.json',{str(n):pdf[n-1].get_text() for n in pages})
    for n in (44,45,84,103,104):
        pdf[n-1].get_pixmap(matrix=fitz.Matrix(1.5,1.5)).save(OUT/f'abp654_ema_page_{n}.png')
    code_files=('rag/index/document_structure.py','preprocess/visual/visual_context.py','rag/index/preprocessed_chunks.py',
                'rag/ra/ra_retrieval.py','rag/ra/ra_parent_context.py','rag/ra/ra_quantities.py',
                'rag/evidence/source_study_scope.py','rag/evidence/budgeted_evidence.py','rag/ra/ra_requirements.py','rag/ra/ra_pipeline.py')
    save('provenance.json',{'source_pdf':str(pdf_path),'source_pdf_sha256':pdf_hash,
                          'release_id':version['id'],'chunks_sha256':summary['chunks_sha256'],
                          'code_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in code_files},
                          'method':'fixed-source-seed production context replay, offline, not full retrieval/generation',
                          'new_provider_calls':0})
    # Assertions pin both working behavior and the reproduced failure modes.
    by_case={r['case']:r for r in result}
    def evidence(key):return list(by_case[key]['evidence'].values())
    healthy='\n'.join(e['text'] for e in evidence('healthy_table5'))
    assert '90 mg SC' in healthy
    assert any(e['pdf_page']==42 and e['retrieval_role']=='same_study_context' for e in evidence('healthy_table5'))
    bad5=evidence('psoriasis_ref_table5')
    has_bad5=any(e['pdf_page']==44 and e['retrieval_role']=='linked_visual_candidate' and
                (e['structure'].get('caption') or '').startswith('Table 5.') for e in bad5)
    has_good7=any((e['structure'].get('caption') or '').startswith('Table 7.') for e in bad5)
    assert any(c['document_id']=='EMA:64196' and (c['structure'].get('caption') or '').startswith('Table 7.') for c in chunks)
    table49=evidence('psoriasis_table49')
    wrong_heading=any(e['pdf_page']==83 and e['retrieval_role']=='source_heading' and '20190230' in e['text'] for e in table49)
    assert any('20190232' in (e['structure'].get('caption') or '') for e in table49)
    bad47=evidence('psoriasis_ref_table47')
    has_bad47=any(e['pdf_page']==101 and e['retrieval_role']=='linked_visual_candidate' and
                 (e['structure'].get('caption') or '').startswith('Table 47.') for e in bad47)
    assert len(rechecks)==2
    if args.expect=='baseline':
        assert has_bad5 and not has_good7 and wrong_heading and has_bad47
        assert all(x['recheck_reason']==x['saved_reason'] for x in rechecks)
    else:
        assert not has_bad5 and has_good7 and not wrong_heading and not has_bad47
        assert all(x['recheck_reason'] is None for x in rechecks)
        assert any(e.get('structure',{}).get('study_context_conflict') for e in table49)
    save('verification.json',{'audit_assertions':'passed','controlled_replays':len(result),
                             'saved_reports_inspected':len(report_rows),'expected_behavior':args.expect,
                             'wrong_table5_supplied':has_bad5,'correct_table7_supplied':has_good7,
                             'wrong_table47_supplied':has_bad47,'wrong_table49_heading_supplied':wrong_heading,
                             'eligibility_rejections_remaining':sum(x['recheck_reason'] is not None for x in rechecks),
                             'source_pdf_hash_checked':True,'new_provider_calls':0})


if __name__=='__main__':main()
