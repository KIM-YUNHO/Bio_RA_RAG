"""Evaluate fixed development samples after source-only repairs, leaving old runs immutable."""
import copy,sys
from pathlib import Path
from pipelines.pipeline_h.prepare import ROOT,read,write
from pipelines.pipeline_h.policy import VERSION,candidate_gate
from pipelines.pipeline_h.structure import parsed_rows
from preprocess.ingest import sha256

def main():
    sys.path.insert(0,str(ROOT/'pipelines/pipeline_h/vendor/compare_study_standalone'))
    from extend_abp654_results import page_rows
    from evaluate import score
    old=ROOT/'pipelines/pipeline_h/vendor/compare_study_standalone/output/abp654_design_20261014_v2'
    rows=page_rows(old,read(old/'inputs.json')['native'],'DESIGN-H');before=copy.deepcopy(rows);changes=[]
    for result in read(old/'design_improved/targeted_paddle.json')['results']:
        job=result['job']
        if job['action']=='replace' and candidate_gate(result['table'],result['content'],job['original_table'])['accepted']:
            current=rows[job['document'],job['page']]['tables'][job['table_index']]
            if current!=result['table']:changes.append({'kind':'saved_candidate_gate_fix','document':job['document'],'page':job['page']})
            rows[job['document'],job['page']]['tables'][job['table_index']]=copy.deepcopy(result['table'])
    path=ROOT/'pipelines/pipeline_h/runs/glyph_table_context_regression/vlm/glyph_table_context.json';r=read(path)
    gate=candidate_gate(parsed_rows(r['content']),r['content'],r['job']['original_table'])
    if not gate['accepted']:raise ValueError('Source glyph reread did not pass the gate')
    tables=rows['ABP654_FDA_ChemR_p012_013.pdf',2]['tables']
    matches=[table for table in tables if table==r['job']['original_table']]
    if len(matches)!=1:raise ValueError('Original table identity is not unique')
    for check in gate['glyph_cell_checks']:matches[0][check['r']][check['c']]=check['content']
    changes.append({'kind':'one_new_cpu_table_context_call','source_sha256':sha256(path),'cells':gate['glyph_cell_checks']})
    # Gold is read only now, after all routing, model calls and source gates.
    gold=read(ROOT/'pipelines/pipeline_h/vendor/compare_study_standalone/output/abp654_15p_20261013_v1/gold.json')
    metrics=score(rows,gold);previous=score(before,gold)
    report={'policy':VERSION,'scope':'exposed 15-page development sample; partial revalidation',
            'basis':'existing H outputs + saved HC candidate revalidation + one new CPU source-table OCR call; not a fresh full GPU comparison',
            'old_run_modified':False,'full_corpus_accuracy':False,'gold_used_for_selection':False,
            'new_inference_calls':1,'new_inference_device':'cpu','before':previous,'after':metrics,'changes':changes}
    output=ROOT/'pipelines/pipeline_h/runs/ustekinumab_20261014_v1/fix_regression.json';write(output,report)
    print('REGRESSION',[(k,{name:value for name,value in v.items() if name!='details'}) for k,v in metrics.items()
                         if k in ('cell_scientific_content_match','number_unit_phrase_recall','table_detection','figure_detection')])

if __name__=='__main__':main()
