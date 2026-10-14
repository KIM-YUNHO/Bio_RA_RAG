"""Portable CLI: check prerequisites, accept PDFs, run the three systems serially."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request

from .settings import CASES, ROOT, activate, configuration


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def selected_cases(value):
    return list(CASES) if value == ['all'] else list(dict.fromkeys(value))


def check(config, cases, packages=True):
    """Read-only checks. No weights are loaded, downloaded, or removed."""
    errors, details = [], {}
    runtimes = ['controller']
    if any(c in cases for c in ['paddle', 'cascade']):
        runtimes.append('paddle')
        fields = ['layout_model_dir']
        if 'paddle' in cases:
            fields.append('vl_model_dir')
        if 'cascade' in cases:
            fields.extend(['light_det_model_dir', 'light_rec_model_dir'])
        for field in fields:
            folder = Path(config['paddle'].get(field, ''))
            if not (folder / 'config.json').is_file() or not any(folder.rglob('*.safetensors')):
                errors.append(f'paddle.{field}: local model config/weights missing ({folder})')
        try:
            from preprocess.hybrid_config import HybridConfig, PaddleSettings, QwenSettings
            HybridConfig(PaddleSettings(**config['paddle']), QwenSettings(**config['qwen'])).validate()
        except Exception as exc:
            errors.append(f'Paddle/Qwen settings: {exc}')
    if 'docling' in cases:
        runtimes.append('docling')
        values = config['docling']
        artifacts = Path(values.get('artifacts_path', ''))
        if not (artifacts / 'docling-project--docling-layout-heron').is_dir() or not (artifacts / 'docling-project--docling-models').is_dir():
            errors.append(f'docling.artifacts_path: layout/table model directories missing ({artifacts})')
        for field in ['det_model_path', 'cls_model_path', 'rec_model_path']:
            if not values.get(field) or not Path(values[field]).is_file():
                errors.append(f'docling.{field}: external resource missing ({values.get(field)})')
        if values.get('trigram_path') and Path(values['trigram_path']).is_file():
            try:
                resource = json.loads(Path(values['trigram_path']).read_text(encoding='utf-8'))
                if not resource.get('trigrams'):
                    raise ValueError('trigrams is empty')
            except Exception as exc:
                errors.append(f'docling.trigram_path: invalid resource ({exc})')
        elif values.get('trigram_path'):
            errors.append(f"docling.trigram_path: configured resource missing ({values['trigram_path']})")
    modules = {
        'controller': ['fitz', 'PIL', 'fontTools', 'numpy', 'cv2', 'pypdfium2'],
        'paddle': ['torch', 'paddleocr', 'paddlex', 'transformers'],
        'docling': ['torch', 'docling', 'rapidocr', 'onnxruntime', 'pypdfium2', 'bs4'],
    }
    for runtime in runtimes:
        python = config['runtimes'].get(runtime)
        if not python or not Path(python).is_file():
            errors.append(f'runtimes.{runtime}: Python executable missing ({python})')
            continue
        if not packages:
            continue
        probe = (
            'import sys,importlib,json; '
            f'names={modules[runtime]!r}; '
            'loaded={n:importlib.import_module(n).__file__ for n in names}; '
            'result={"python":sys.version,"modules":loaded}; '
        )
        if runtime in ('paddle', 'docling'):
            probe += 'import torch; result["cuda"]=torch.cuda.is_available(); '
        if runtime == 'docling':
            probe += 'import onnxruntime as ort; result["ort_providers"]=ort.get_available_providers(); '
        probe += 'print(json.dumps(result))'
        try:
            result = subprocess.run([python, '-I', '-B', '-X', 'utf8', '-c', probe],
                                    cwd=ROOT, capture_output=True, text=True, encoding='utf-8',
                                    timeout=60, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                raise RuntimeError(result.stderr.strip()[-1800:])
            details[runtime] = json.loads(result.stdout.strip().splitlines()[-1])
            if runtime in ('paddle', 'docling') and not details[runtime]['cuda']:
                errors.append(f'{runtime}: PyTorch CUDA unavailable')
            if runtime == 'docling' and 'CUDAExecutionProvider' not in details[runtime]['ort_providers']:
                errors.append('docling: ONNX Runtime CUDA provider unavailable')
        except Exception as exc:
            errors.append(f'{runtime} runtime probe: {exc}')
    if 'cascade' in cases and packages:
        try:
            from preprocess.hybrid_config import local_endpoint
            endpoint = local_endpoint(config['qwen']['endpoint'])
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(endpoint + '/api/tags', timeout=5) as reply:
                models = json.load(reply)['models']
            if not any(m['name'] == config['qwen']['model'] for m in models):
                raise ValueError(f"model not installed: {config['qwen']['model']}")
            details['ollama'] = {'endpoint': endpoint, 'model': config['qwen']['model']}
        except Exception as exc:
            errors.append(f'Ollama: {exc}')
    return {'ok': not errors, 'cases': cases, 'errors': errors, 'details': details}


def prepare_inputs(paths, modes, pages_value, run, agency, doc_type):
    import fitz
    from preprocess.hybrid_config import parse_pages
    requested = parse_pages(pages_value)
    manifest = {mode: [] for mode in modes}
    for source in paths:
        source = Path(source).resolve()
        if not source.is_file() or source.suffix.lower() != '.pdf':
            raise ValueError('Input must be an existing PDF: ' + str(source))
        digest = sha(source)
        key = re.sub(r'[^A-Za-z0-9_-]+', '_', source.stem).strip('_')[:50] or 'document'
        key += '_' + hashlib.sha256(str(source).encode('utf-8')).hexdigest()[:10]
        doc = {'key': key, 'document_id': 'LOCAL:' + key, 'agency': agency, 'doc_type': doc_type}
        with fitz.open(source) as pdf:
            if pdf.needs_pass:
                raise ValueError('Password-protected PDF: ' + str(source))
            page_map = requested or list(range(1, len(pdf) + 1))
            if not page_map or max(page_map) > len(pdf):
                raise ValueError('Requested page is outside PDF: ' + str(source))
            if 'native' in modes:
                native = source
                if requested:
                    native = run / 'prepared' / f'{key}_native.pdf'
                    native.parent.mkdir(parents=True, exist_ok=True)
                    with fitz.open() as subset:
                        for page in page_map:
                            subset.insert_pdf(pdf, from_page=page - 1, to_page=page - 1)
                        subset.save(native, garbage=4, deflate=True)
                manifest['native'].append({'document': doc, 'path': str(native), 'sha256': sha(native),
                                           'original_path': str(source), 'original_sha256': digest, 'page_map': page_map})
            if 'image' in modes:
                target = run / 'prepared' / f'{key}_image.pdf'
                target.parent.mkdir(parents=True, exist_ok=True)
                with fitz.open() as images:
                    for number in page_map:
                        original = pdf[number - 1]
                        pix = original.get_pixmap(dpi=300, alpha=False)
                        image = images.new_page(width=original.rect.width, height=original.rect.height)
                        image.insert_image(image.rect, stream=pix.tobytes('png'))
                    images.save(target, garbage=4, deflate=True)
                manifest['image'].append({'document': doc, 'path': str(target), 'sha256': sha(target),
                                          'original_path': str(source), 'original_sha256': digest, 'page_map': page_map})
    write(run / 'inputs.json', manifest)
    return manifest


def summarize(run, cases, modes):
    results = {}
    lines = ['# 전처리 실행 비교', '', '| 모드 | 케이스 | 페이지 | 본문 문자 | 표 / 빈 후보 | 처리(초) | 시작 포함(초) |',
             '|---|---|---:|---:|---:|---:|---:|']
    for mode in modes:
        results[mode] = {}
        for case in cases:
            stages = CASES[case]
            records = [json.loads((run / mode / s / 'run.json').read_text(encoding='utf-8')) for s in stages]
            page_files = sorted((run / mode / stages[-1]).glob('*/*.json'))
            pages = [json.loads(p.read_text(encoding='utf-8')) for p in page_files]
            candidates = [table for page in pages for table in page['tables']]
            empty = sum(not table or not any(cell.strip() for cells in table for cell in cells) for table in candidates)
            result = {'pages': len(pages), 'text_characters': sum(sum(map(len, p['text'])) for p in pages),
                      'tables': len(candidates) - empty, 'table_candidates': len(candidates), 'empty_tables': empty,
                      'processing_seconds': sum(r['processing_seconds'] for r in records),
                      'total_seconds': sum(r['total_seconds'] for r in records),
                      'output': str(run / mode / stages[-1])}
            result['embedded_image_objects']=sum(p.get('embedded_image_objects',0) for p in pages)
            result['figure_counts']={k:sum(p.get('figure_counts',{}).get(k,0) for p in pages)
                for k in ['figure_regions','figure_groups','logical_figures','decorative_images',
                          'interpretation_attempted','interpretation_completed','interpretation_needs_review']}
            results[mode][case] = result
            lines.append(f"| {mode} | {case} | {result['pages']} | {result['text_characters']} | {result['tables']} / {empty} | {result['processing_seconds']:.2f} | {result['total_seconds']:.2f} |")
    lines.extend(['', '표 수·문자 수는 출력량입니다. 정답 데이터 없이 정확도 점수나 우열을 산출하지 않습니다.',
                  'Paddle과 Cascade는 내부 단계의 시간을 합산합니다. 모델은 GPU로 추론하며 PDF 렌더링과 규칙 처리는 CPU에서 실행합니다.',
                  '그림·차트 처리 정책과 모델·검토 상태는 settings.json 및 페이지별 figures에 기록합니다. 정확도 평가가 있으면 accuracy.json과 review.html에서 따로 확인합니다.'])
    write(run / 'summary.json', results)
    (run / 'summary.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return results


def align_inputs(run, config):
    """Apply the same original RA watermark sanitizer once, retain input and coordinate evidence."""
    sys.path.insert(0,str(ROOT/'compare_study/bench/vendor'))
    from ra_prep.sanitize import remove_watermarks
    import fitz
    manifest=json.loads((run/'inputs.json').read_text(encoding='utf-8'))
    begin=time.perf_counter()
    for jobs in manifest.values():
        for job in jobs:
            meta=config.get('input_metadata',{}).get(Path(job['original_path']).name,{})
            job['document'].update(meta)
            job['document']['document_id']=job['document']['agency']+':'+job['document']['key']
            clean=run/'prepared'/(job['document']['key']+'_clean.pdf')
            removed=remove_watermarks(job['path'],str(clean))
            before=job['sha256']
            if removed:job.update(path=str(clean),sha256=sha(clean))
            with fitz.open(job['path']) as pdf:
                coordinates=[{'page':n,'rotation':p.rotation,'media_box':list(p.mediabox),
                    'crop_box':list(p.cropbox),'width':p.rect.width,'height':p.rect.height}
                    for n,p in enumerate(pdf,1)]
            job['common_preprocessing']={'watermarks_removed':removed,'before_sha256':before,
                'after_sha256':job['sha256'],'geometry':coordinates,
                'coordinate_space':'unrotated PDF top-left points; review renders original at 180 dpi',
                'policy':'original ra_prep diagonal text sanitizer; no resampling of scan input'}
    write(run/'inputs.json',manifest)
    write(run/'common_preprocessing.json',{'seconds':time.perf_counter()-begin,'inputs':manifest,
        'cache_policy':'charged once separately; engine-only times exclude this common cost'})


def main(argv=None):
    parser = argparse.ArgumentParser(description='Standalone Paddle / Cascade+Qwen / Docling PDF preprocessing')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ['check', 'run']:
        sub = commands.add_parser(name)
        sub.add_argument('--config', default=str(ROOT / 'config.json'))
        sub.add_argument('--cases', nargs='+', choices=['all', *CASES], default=['all'])
        if name == 'run':
            sub.add_argument('--input', nargs='+', required=True, help='One or more PDF paths')
            sub.add_argument('--mode', choices=['native', 'image', 'both'], default='native')
            sub.add_argument('--pages', help='One-based pages, for example 1,3,5-8')
            sub.add_argument('--output', default=str(ROOT / 'output'))
            sub.add_argument('--run-id', help='Fresh folder name; defaults to a timestamp')
            sub.add_argument('--agency', default='LOCAL')
            sub.add_argument('--doc-type', default='document')
            sub.add_argument('--dry-run', action='store_true', help='Prepare inputs only; no model inference')
    args = parser.parse_args(argv)
    try:
        config = activate(args.config)
        cases = selected_cases(args.cases)
        if 'all' in cases or not cases:
            raise ValueError('Use --cases all alone, or specify case names')
        if args.command == 'check':
            result = check(config, cases)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result['ok'] else 1
        if not args.dry_run:
            result = check(config, cases)
            if not result['ok']:
                print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
                return 1
        run_id = args.run_id or datetime.now(timezone.utc).strftime('run_%Y%m%dT%H%M%S_%fZ')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,100}', run_id):
            raise ValueError('run-id must be a simple folder name')
        runs = Path(args.output).resolve()
        run = runs / run_id
        run.mkdir(parents=True, exist_ok=False)
        modes = ['native', 'image'] if args.mode == 'both' else [args.mode]
        state = {'status': 'preparing', 'cases': cases, 'modes': modes, 'steps': []}
        write(run / 'run.json', state)
        try:
            prepare_inputs(list(dict.fromkeys(args.input)), modes, args.pages, run, args.agency, args.doc_type)
            if config.get('common_preprocessing'):align_inputs(run,config)
            if config.get('evaluation_gold'):
                gold=Path(config['evaluation_gold'])
                write(run/'gold.json',json.loads(gold.read_text(encoding='utf-8')))
                protocol=config.get('experiment_protocol',{})
                write(run/'experiment_manifest.json',{'frozen_at':datetime.now(timezone.utc).isoformat(),
                    'gold_sha256':sha(run/'gold.json'),'source_gold_sha256':sha(gold),
                    'output_token_limit':config['qwen']['num_predict'], 'context_tokens':config['qwen']['num_ctx'],
                    'protocol':protocol.get('description','one serial run per engine, no result selection by retry'),
                    'configuration_delta':protocol.get('configuration_delta'),
                    'development_set':protocol.get('development_set','development annotations; no generalization claim')})
            if config.get('dataset_manifest'):
                dataset=Path(config['dataset_manifest'])
                write(run/'dataset_manifest.json',json.loads(dataset.read_text(encoding='utf-8')))
                candidates=dataset.parent/'selected_candidates.json'
                if candidates.exists():write(run/'selected_candidates.json',json.loads(candidates.read_text(encoding='utf-8')))
            write(run / 'settings.json', {k: v for k, v in config.items() if k != '_path'})
            if args.dry_run:
                state['status'] = 'prepared_only'
                print('Inputs prepared; no inference:', run)
                return 0
            state['status'] = 'running'
            write(run / 'run.json', state)
            for mode in modes:
                for case in cases:
                    for stage in CASES[case]:
                        runtime = config['runtimes']['docling' if stage == 'DOC-GPU' else 'controller']
                        log = run / 'logs' / f'{mode}_{stage}.log'
                        log.parent.mkdir(exist_ok=True)
                        command = [runtime, '-B', '-X', 'utf8', '-m', 'compare_study.worker', stage, mode,
                                   '--run-id', run_id, '--config', config['_path'], '--runs', str(runs)]
                        print('START', mode, stage, flush=True)
                        start = time.perf_counter()
                        with log.open('w', encoding='utf-8') as stream:
                            process = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                        state['steps'].append({'mode': mode, 'stage': stage, 'exit_code': process.returncode,
                                               'seconds': time.perf_counter() - start, 'log': str(log)})
                        write(run / 'run.json', state)
                        if process.returncode:
                            raise RuntimeError(f'{mode} {stage} failed; see {log}')
                        print('DONE', mode, stage, flush=True)
            summarize(run, cases, modes)
            state['status'] = 'complete'
            print('Results:', run / 'summary.md')
            return 0
        except BaseException as exc:
            state.update(status='failed', error=f'{type(exc).__name__}: {exc}')
            raise
        finally:
            write(run / 'run.json', state)
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        return 1
