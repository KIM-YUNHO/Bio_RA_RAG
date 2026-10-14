"""One system/stage per process, with local GPU models and retained page outputs."""
import argparse
import os
from pathlib import Path
import sys
import time

from .settings import ROOT, activate, runs_root


def execute(stage, mode, run_id, config_path, runs):
    activate(config_path, runs)
    sys.path.insert(0, str(ROOT / 'compare_study/bench/vendor'))
    from .bench.common import read, write, sha, validate_output
    run = runs_root() / run_id
    jobs = read(run / 'inputs.json')[mode]
    out = run / mode / stage
    out.mkdir(parents=True, exist_ok=False)
    if stage in ('PVL', 'CUR'):
        from .bench.adapters.paddle import Paddle
        adapter = Paddle(stage, out)
    elif stage == 'P-HANDOFF':
        from .bench.adapters.p_handoff import Handoff
        adapter = Handoff()
    elif stage == 'CUR+Q':
        from .bench.adapters.cascade_qwen import CascadeQwen
        adapter = CascadeQwen()
    elif stage == 'DOC-GPU':
        from .bench.adapters.docling import Docling
        adapter = Docling('cuda')
    else:
        raise ValueError(stage)
    started = time.perf_counter()
    summary = {'stage': stage, 'mode': mode, 'status': 'running', 'documents': []}
    write(out / 'run.json', summary)
    try:
        summary['startup'] = adapter.start(mode)
        summary['startup_seconds'] = time.perf_counter() - started
        processing = time.perf_counter()
        for job in jobs:
            doc = job['document']
            path = Path(job['path'])
            if sha(path) != job['sha256']:
                raise ValueError('Input PDF changed after preparation: ' + str(path))
            work = out / 'raw' / doc['key']
            work.mkdir(parents=True)
            args = {}
            if stage == 'P-HANDOFF':
                source = run / mode / 'PVL' / doc['key']
                args['pvl'] = [read(source / f'{n:03d}.json') for n in job['page_map']]
            elif stage == 'CUR+Q':
                args['cur_run'] = run_id
            begin = time.perf_counter()
            pages = adapter.document(path, doc, job['page_map'], work, mode, **args)
            if len(pages) != len(job['page_map']):
                raise ValueError('Output page count differs from input')
            for page in pages:
                validate_output(page)
                write(out / doc['key'] / f"{page['page']:03d}.json", page)
            summary['documents'].append({'key': doc['key'], 'pages': len(pages),
                                         'seconds': time.perf_counter() - begin})
            write(out / 'run.json', summary)
        summary['processing_seconds'] = time.perf_counter() - processing
        summary['status'] = 'complete'
    except BaseException as exc:
        summary.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        # Cleanup must not mask an inference failure or discard its checkpoint.
        try:
            adapter.close()
        except Exception as exc:
            summary['close_warning'] = f'{type(exc).__name__}: {exc}'
        summary['total_seconds'] = time.perf_counter() - started
        write(out / 'run.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['PVL', 'CUR', 'P-HANDOFF', 'CUR+Q', 'DOC-GPU'])
    parser.add_argument('mode', choices=['native', 'image'])
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--runs', required=True)
    args = parser.parse_args()
    execute(args.stage, args.mode, args.run_id, args.config, args.runs)


if __name__ == '__main__':
    main()
