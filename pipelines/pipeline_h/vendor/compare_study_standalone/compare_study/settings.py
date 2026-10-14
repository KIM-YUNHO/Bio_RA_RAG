"""Paths are resolved from the supplied configuration, never from the old workspace."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES = {'paddle': ['PVL', 'P-HANDOFF'], 'cascade': ['CUR', 'CUR+Q'], 'docling': ['DOC-GPU']}


def configuration(path=None):
    path = Path(path or os.environ.get('COMPARE_CONFIG', ROOT / 'config.json')).resolve()
    config = json.loads(path.read_text(encoding='utf-8-sig'))
    config['_path'] = str(path)
    base = path.parent
    for section, fields in {
        'runtimes': ['controller', 'paddle', 'docling'],
        'paddle': ['python', 'layout_model_dir', 'vl_model_dir', 'light_det_model_dir', 'light_rec_model_dir'],
        'docling': ['artifacts_path', 'trigram_path', 'det_model_path', 'cls_model_path', 'rec_model_path'],
    }.items():
        values = config.setdefault(section, {})
        for field in fields:
            if values.get(field):
                values[field] = str((base / Path(values[field])).resolve())
    config['paddle']['python'] = config['runtimes'].get('paddle', config['paddle'].get('python', ''))
    if config.get('evaluation_gold'):
        config['evaluation_gold']=str((base/Path(config['evaluation_gold'])).resolve())
    if config.get('dataset_manifest'):
        config['dataset_manifest']=str((base/Path(config['dataset_manifest'])).resolve())
    return config


def activate(path, runs=None):
    config = configuration(path)
    os.environ['COMPARE_CONFIG'] = config['_path']
    if runs is not None:
        os.environ['COMPARE_RUNS_DIR'] = str(Path(runs).resolve())
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_DATASETS_OFFLINE='1',
                      HF_HUB_DISABLE_TELEMETRY='1', BENCHMARK_CASCADE_OCR_BATCH='1',
                      BENCHMARK_DOCLING_CUDNN_SEARCH='HEURISTIC',
                      BENCHMARK_DOCLING_CUDA_ARENA='kSameAsRequested',
                      BENCHMARK_DOCLING_PDF_BACKEND='pypdfium2')
    if config['docling'].get('trigram_path'):
        os.environ['COMPARE_TRIGRAM_PATH'] = config['docling']['trigram_path']
    else:
        os.environ.pop('COMPARE_TRIGRAM_PATH', None)
    return config


def extraction_config():
    from preprocess.hybrid_config import HybridConfig, PaddleSettings, QwenSettings
    config = configuration()
    return HybridConfig(PaddleSettings(**config['paddle']), QwenSettings(**config['qwen'])).validate()


def runs_root():
    return Path(os.environ.get('COMPARE_RUNS_DIR', ROOT / 'output')).resolve()


def rules():
    return {'rules': {'qwen_routing': 'selected_complex', 'figure_policy': 'detection_only',
                      **configuration().get('rules', {})}}
