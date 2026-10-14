"""Explicit model preparation; inference never auto-downloads models."""
import argparse
import os
from pathlib import Path

from compare_study.settings import ROOT, configuration

PADDLE_MODELS = {
    'layout_model_dir': ('PaddlePaddle/PP-DocLayoutV3_safetensors', 'e4f489dc536556fc1be02b973ba06756e0d4a2ac'),
    'vl_model_dir': ('PaddlePaddle/PaddleOCR-VL-1.6', 'c5630abae1d940eafe0697512a0325494b02ab42'),
    'light_det_model_dir': ('PaddlePaddle/PP-OCRv5_mobile_det_safetensors', 'c5041d225cf951ff06900ab81a3c7d543c45e2ad'),
    'light_rec_model_dir': ('PaddlePaddle/latin_PP-OCRv5_mobile_rec_safetensors', '47901138f5f32dad06282fa88d2c672fa816d450'),
}


def main():
    parser = argparse.ArgumentParser(description='Download model weights to the configured model directories')
    parser.add_argument('case', choices=['paddle', 'cascade', 'docling'])
    parser.add_argument('--config', default=str(ROOT / 'config.json'))
    args = parser.parse_args()
    config = configuration(args.config)
    for name in ['HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'HF_DATASETS_OFFLINE']:
        os.environ.pop(name, None)
    if args.case == 'docling':
        from docling.utils.model_downloader import download_models
        download_models(output_dir=Path(config['docling']['artifacts_path']), progress=True,
                        with_code_formula=False, with_picture_classifier=False,
                        with_rapidocr=True, rapidocr_models=['onnxruntime:en'], rapidocr_model_size='small')
    else:
        from huggingface_hub import snapshot_download
        fields = ['layout_model_dir', 'vl_model_dir'] if args.case == 'paddle' else ['layout_model_dir', 'light_det_model_dir', 'light_rec_model_dir']
        for field in fields:
            repo, revision = PADDLE_MODELS[field]
            snapshot_download(repo_id=repo, revision=revision, local_dir=config['paddle'][field])
    print('Model files prepared. Cascade additionally needs Ollama with qwen3.5:9b.')


if __name__ == '__main__':
    main()
