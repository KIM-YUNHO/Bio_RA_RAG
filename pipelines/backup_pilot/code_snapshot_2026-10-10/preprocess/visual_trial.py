"""Separate, source/code-sealed retrospective trial. Does not unlock the dataset."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import re
import shutil
import fitz
from .dataset import verify, rows
from .ingest import from_catalog, sha256
from .inventory import write_json
from .hybrid_config import load_config
from .hybrid import run_hybrid, save_rows
from .paddle_adapter import PaddleAdapter
from .qwen_adapter import QwenAdapter

VERSION='visual-retrospective-trial-1'


def prepare(dataset,out,root,config):
    dataset,out,root=map(lambda p:Path(p).resolve(),(dataset,out,root))
    verify(dataset)
    if out.exists():raise ValueError('Trial specification requires a new directory')
    docs=[d for d in rows(dataset/'catalog.jsonl') if d['product_id'] in {'BMAB1200','BAT2206'}
          and d['source']=='EMA' and d['document_type']=='assessment-report']
    if len(docs)!=2 or any(d['model_execution_allowed'] is not False for d in docs):
        raise ValueError('Expected exactly two protected EPAR source versions')
    plans={};selection=[]
    rng=random.Random(20261008)
    for d in sorted(docs,key=lambda d:d['product_id']):
        with fitz.open(root/d['raw_path']) as pdf:
            info=[]
            for p in pdf:
                text=p.get_text('text')
                figures=len(re.findall(r'^\s*(?:Figure|Fig\.)\s*\d+\b',text,re.M))
                tables=len(re.findall(r'^\s*Table\s*\d+\b',text,re.M))
                info.append({'page':p.number+1,'figures':figures,'tables':tables})
            fs=[p for p in info if p['figures']];ts=[p for p in info if p['tables']]
            if not fs or not ts:raise ValueError('Source requires explicit alternative sampling; no numbered visual captions')
            figure=max(fs,key=lambda p:(p['figures'],-p['page']))['page']
            table=max(ts,key=lambda p:(p['tables'],-p['page']))['page']
            random_page=rng.randint(1,len(pdf))
            pages=sorted({p for a in [figure,table] for p in [a-1,a,a+1] if 1<=p<=len(pdf)}|{random_page})
            plans[d['document_id']]=pages
            selection.append({'document_id':d['document_id'],'version_id':d['version_id'],'product_id':d['product_id'],
                'page_count':len(pdf),'figure_anchor':figure,'table_anchor':table,'random_anchor':random_page,
                'pages':pages,'sampling':'purposive native-caption stress anchors + adjacent context + one random page; not corpus estimate'})
    out.mkdir(parents=True)
    copied=deepcopy(docs)
    for d in copied:
        d['model_execution_allowed']=True
        d['evaluation_cohort']='retrospective_evaluation_only'
        d['default_search']=False
    save_rows(out/'catalog.jsonl',copied)
    files=sorted([*root.glob('preprocess/*.py'),*root.glob('rag/*.py'),root/config])
    code={}
    for path in files:
        relative=path.relative_to(root).as_posix();code[relative]=sha256(path)
        dst=out/'code_snapshot'/relative;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,dst)
    spec={'version':VERSION,'created_at':datetime.now(timezone.utc).isoformat(),'dataset':str(dataset),'root':str(root),
        'dataset_seal_sha256':sha256(dataset/'seal.json'),'catalog_sha256':sha256(out/'catalog.jsonl'),
        'config_path':config,'config_sha256':sha256(root/config),'source_selection':selection,'pages_by_document':plans,
        'code_sha256':code,'strict_holdout':False,'human_gold_ready':False,
        'claims':'Source-first agent literal spotchecks only. Prior QA exposure; no independent final-test or clinical accuracy claim.',
        'evaluation_metadata_override':'Dedicated copied catalog only; original protected policy remains false.',
        'qwen_call_budget':24,'deployment_gate':'No active index promotion without human reviewed visual evidence.'}
    settings=load_config(root/config)
    spec['model_contracts']={'paddle':PaddleAdapter(settings.paddle,out).fingerprint(),
                             'qwen':QwenAdapter(settings.qwen).fingerprint()}
    write_json(out/'trial.json',spec)
    write_json(out/'seal.json',{'trial_sha256':sha256(out/'trial.json')})
    return selection


def verify_trial(specification):
    folder=Path(specification).resolve();spec=json.loads((folder/'trial.json').read_text(encoding='utf-8'))
    if sha256(folder/'trial.json')!=json.loads((folder/'seal.json').read_text(encoding='utf-8'))['trial_sha256']:
        raise ValueError('Trial specification changed')
    verify(Path(spec['dataset']))
    if sha256(Path(spec['dataset'])/'seal.json')!=spec['dataset_seal_sha256']:raise ValueError('Dataset changed')
    if sha256(folder/'catalog.jsonl')!=spec['catalog_sha256']:raise ValueError('Trial source roles changed')
    root=Path(spec['root'])
    for relative,digest in spec['code_sha256'].items():
        if sha256(root/relative)!=digest or sha256(folder/'code_snapshot'/relative)!=digest:
            raise ValueError(f'Frozen implementation changed: {relative}')
    return spec


def execute(specification,out):
    spec=verify_trial(specification)
    folder=Path(specification).resolve();out=Path(out).resolve()
    annotations=folder/'source_annotations.json'
    annotation_seal=folder/'annotation_seal.json'
    if not annotations.is_file() or not annotation_seal.is_file():raise ValueError('Seal source-first annotations before inference')
    expected=json.loads(annotation_seal.read_text(encoding='utf-8'))['sha256']
    if sha256(annotations)!=expected:raise ValueError('Annotations changed after source review')
    if out.exists():raise ValueError('Use a new evaluation output')
    documents=from_catalog(folder/'catalog.jsonl',Path(spec['root']))
    settings=load_config(Path(spec['root'])/spec['config_path'])
    current={'paddle':PaddleAdapter(settings.paddle,out).fingerprint(),'qwen':QwenAdapter(settings.qwen).fingerprint()}
    if current != spec['model_contracts']:raise ValueError('Frozen model weights/runtime changed')
    result=run_hybrid(documents,out,settings,
                      max_qwen_calls=spec['qwen_call_budget'],pages_by_document=spec['pages_by_document'])
    verify_trial(folder)
    if sha256(annotations)!=expected:raise ValueError('Source annotations changed during inference')
    write_json(out/'trial_execution.json',{'specification':str(folder),'trial_sha256':sha256(folder/'trial.json'),
        'source_annotations_sha256':expected,'active_index_updated':False,'strict_holdout':False,'summary':result})
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    prep=sub.add_parser('prepare');prep.add_argument('--dataset',type=Path,required=True);prep.add_argument('--out',type=Path,required=True)
    prep.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);prep.add_argument('--config',default='configs/preprocess.local.json')
    run=sub.add_parser('run');run.add_argument('--specification',type=Path,required=True);run.add_argument('--out',type=Path,required=True)
    a=p.parse_args();result=prepare(a.dataset,a.out,a.root,a.config) if a.command=='prepare' else execute(a.specification,a.out)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
