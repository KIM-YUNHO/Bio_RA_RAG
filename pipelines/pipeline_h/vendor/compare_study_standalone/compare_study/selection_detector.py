"""Layout-only inventory for pipeline selection; no OCR, VLM, gold or cached boxes."""
import contextlib
import json
import os
from pathlib import Path
import sys
import time


def main():
    inputs, target, settings_path = map(Path, sys.argv[1:4])
    for name in ['HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'HF_DATASETS_OFFLINE']:
        os.environ[name] = '1'
    os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'
    from .bench.common import read, sha, write
    from .bench.gpu import torch_devices
    settings = read(settings_path)['paddle']
    jobs = read(inputs)
    beginning = time.perf_counter()
    with contextlib.redirect_stdout(sys.stderr):
        from paddleocr import LayoutDetection
        model = LayoutDetection(model_name='PP-DocLayoutV3', model_dir=settings['layout_model_dir'],
                                engine='transformers', device=settings['device'])
    startup = time.perf_counter()-beginning
    result = {'status': 'running', 'model': 'PP-DocLayoutV3', 'startup_seconds': startup,
              'pages': [], 'ocr_calls': 0, 'vlm_calls': 0}
    write(target, result)
    for page in jobs:
        if sha(page['image']) != page['image_sha256']:
            raise ValueError('Detector input changed')
        start = time.perf_counter()
        with contextlib.redirect_stdout(sys.stderr):
            outputs = list(model.predict(input=page['image']))
            if len(outputs) != 1:
                raise ValueError('Detector must return one page')
            raw = outputs[0].json
        boxes = raw.get('res', raw)['boxes']
        result['pages'].append({**page, 'boxes': boxes, 'raw': raw,
                                'seconds': time.perf_counter()-start})
        write(target, result)
        print(page['document'], page['page'], len(boxes), flush=True)
    result.update(status='complete', processing_seconds=sum(p['seconds'] for p in result['pages']),
                  total_seconds=time.perf_counter()-beginning, gpu=torch_devices(model))
    write(target, result)


if __name__ == '__main__':
    main()
