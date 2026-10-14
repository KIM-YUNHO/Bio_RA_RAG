"""Bounded Luna trial on existing RA sources; production service stays unchanged."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import time

from rag import ra_answer, ra_tasks
from rag.ra_pipeline import VERSION

ROOT = Path(__file__).resolve().parents[1]
MODEL = 'gpt-5.6-luna'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def trial_transport(out):
    """Adapt a private copy of the audited transport, never the active code."""
    source = (ROOT / 'rag/openai_chat.py').read_text(encoding='utf-8')
    changes = [
        ("model not in ('gpt-4o-mini','gpt-4.1-2025-04-14')",
         "model not in ('gpt-4o-mini','gpt-4.1-2025-04-14','gpt-5.6-luna')"),
        ("payload={'model':self.model,'messages':messages,'temperature':0,'seed':0,'max_tokens':output}",
         "payload={'model':self.model,'messages':messages,'temperature':0,'seed':0,'max_tokens':output}\n"
         "        if self.model == 'gpt-5.6-luna':\n"
         "            payload={'model':self.model,'messages':messages,'max_completion_tokens':output,'reasoning_effort':'none'}"),
    ]
    for old, new in changes:
        assert source.count(old) == 1
        source = source.replace(old, new)
    path = out / 'transport_snapshot.py'
    path.write_text(source, encoding='utf-8')
    spec = importlib.util.spec_from_file_location('rag._luna_trial_transport', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(out):
    if out.exists():
        raise ValueError('Trial output exists; never automatically repeat provider calls')
    out.mkdir(parents=True)
    baseline = ROOT / 'data/runs/ra_pipeline_obligations_2026-10-12_v7'
    sources = {'normal': baseline / 'service/live_report.json',
               'RA10': baseline / 'replay/RA10.json'}
    code = {str(p.relative_to(ROOT)): digest(p) for p in (ROOT / 'rag').glob('*.py')}
    index = read(baseline / 'baseline.json')['index_sha256']
    assert all(digest(ROOT / name) == sha for name, sha in index.items())
    transport = trial_transport(out)
    write(out / 'protocol.json', {
        'created_at': datetime.now(timezone.utc).isoformat(), 'model': MODEL,
        'pipeline_version': VERSION, 'reasoning_effort': 'none',
        'new_call_cap': 5, 'automatic_retries': 0, 'external_grader': False,
        'gold_sent': False, 'independent_holdout': False,
        'production_service_changed': False, 'new_retrieval_executions': 0,
        'scope': 'One fresh planning check; fresh generation and internal review for two fixed-source cases.',
        'same_prompt_and_postprocessing': True, 'same_16384_context_and_3000_output_budget': True,
        'source_reports': {key: {'path': str(path), 'sha256': digest(path)} for key, path in sources.items()},
        'code_sha256': code, 'index_sha256': index,
        'trial_transport_sha256': digest(out / 'transport_snapshot.py'),
        'documentation': 'https://developers.openai.com/api/docs/models/gpt-5.6-luna',
    })
    client = transport.EvaluationAPIChat(out / 'api', max_new_calls=5, model=MODEL)
    rows = []
    try:
        normal = read(sources['normal'])
        client.case = 'normal_planning'
        print('Luna planning check', flush=True)
        plan, _ = ra_tasks.interpret(normal['question'], client, ['SB17'], ['EU'],
                                     selected_products=['SB17'], selected_agencies=['EU'])
        write(out / 'planning.json', {'question': normal['question'], 'understanding': plan,
                                      'calls': deepcopy(client.calls),
                                      'not_used_to_change_fixed_comparison_context': True})
        for key, path in sources.items():
            old = read(path)
            before = old['results'][0]
            call_start = len(client.calls)
            client.case = key
            started = time.monotonic()
            print(key, 'Luna synthesis and internal review', flush=True)
            result = ra_answer.generate(old['question'], old['understanding'], before['evidence'], client, 'spans')
            calls = deepcopy(client.calls[call_start:])
            report = {'question': old['question'], 'understanding': old['understanding'],
                      'model': MODEL, 'pipeline_version': VERSION, 'results': [result],
                      'evidence': before['evidence'], 'generation_telemetry': calls,
                      'context_origin': 'exact_saved_admitted_sources',
                      'fresh_generation_and_internal_review': True,
                      'seconds': round(time.monotonic() - started, 3)}
            write(out / (key + '.json'), report)
            row = {'id': key, 'mini_before_status': before['status'], 'status': result['status'],
                   'claims': len(result['claims']),
                   'rejected': len(result['validation']['rejected_items']),
                   'new_calls': len(calls), 'seconds': report['seconds']}
            rows.append(row)
            print(json.dumps(row), flush=True)
            print(result['answer_ko'], flush=True)
    except Exception as exc:
        write(out / 'failure.json', {'error': str(exc).replace(client.secret, '[REDACTED]') if client.secret else str(exc),
                                    'calls': deepcopy(client.calls), 'completed_cases': rows})
        raise
    finally:
        client.close()
        assert all(digest(ROOT / name) == sha for name, sha in code.items())
        assert all(digest(ROOT / name) == sha for name, sha in index.items())
        write(out / 'summary.json', {
            'model': MODEL, 'rows': rows, 'new_calls': client.attempts,
            'input_tokens': sum(c.get('prompt_tokens', 0) for c in client.calls),
            'output_tokens': sum(c.get('completion_tokens', 0) for c in client.calls),
            'all_calls_requested_luna': all(c['model_requested'] == MODEL for c in client.calls),
            'provider_reported_models': sorted({c.get('model_reported', '') for c in client.calls}),
            'all_calls_completed': all(c.get('status') == 'completed' for c in client.calls),
            'production_code_and_index_unchanged': True, 'independent_accuracy_evaluation': False,
            'limits': 'Two diagnostic questions at reasoning none; planning checked separately; no new retrieval or general accuracy estimate.',
        })


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    run(parser.parse_args().out.resolve())
