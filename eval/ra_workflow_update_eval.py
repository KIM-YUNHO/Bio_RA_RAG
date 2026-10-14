"""Frozen development regression: saved replay plus five fresh end-to-end cases.

No independent accuracy claim. No gold answers in provider inputs. No retries.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from rag import ra_answer
from rag.openai_chat import ApiTokenizer, EvaluationAPIChat
from rag.ra_memo import build as build_memo
from rag.ra_pipeline import RAPipeline, VERSION

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT/'data/versions/01_pilot_baseline_2026-10-10/embedded/rag_finalization_2026-10-10_v2'
GUIDELINES = ROOT/'data/guidelines/ra_guidelines_native_2026-10-11_v1'
MODEL = 'gpt-5.6-luna'
SAVED = {
    'normal': ROOT/'data/runs/ra_luna_deployment_2026-10-12_v1/live_report.json',
    'RA04': ROOT/'data/runs/ra_pipeline_obligations_2026-10-12_v7/replay/RA04.json',
    'RA10': ROOT/'data/runs/ra_luna_trial_2026-10-12_v1/RA10.json',
    'CTP43_stratification': ROOT/'data/runs/ra_pipeline_obligations_2026-10-12_v7/replay/EMA_UST_PK_014.json',
}


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(report):
    result = report['results'][0]
    evidence = result['evidence']
    citations = [s for c in result['claims'] for s in c['sources']]
    for source in citations:
        start, end = source['source_range']
        assert evidence[source['evidence']]['text'][start:end] == source['quote']
    # Stable IDs identify the exact quote across packet aliases. All final
    # sources must be among this parent's sources actually sent to its review.
    assert all({s['citation_span_id'] for s in c['sources']} <=
               {s['citation_span_id'] for s in c['initial_sources']} for c in result['claims'])
    assert all(c['support_review']['verdict'] == 'supported' for c in result['claims'])
    memo = report['review_memo']
    ids = {c['id'] for c in result['claims']}
    assert all(c['claim_id'] in ids for issue in memo['issues'] for c in issue['claims'])
    return {'status': result['status'], 'claims': len(result['claims']), 'citations': len(citations),
        'exact_quote_ranges': True, 'own_review_source_boundary': True,
        'rejected': len(result['validation']['rejected_items']), 'gaps': result['gaps'],
        'reviewed_condition_restorations': sum('resolved_reviewed_condition' in c for c in result['claims']),
        'memo_issues': len(memo['issues']), 'meeting_question_drafts': len(memo['meeting_questions']),
        'seconds': report.get('seconds'), 'timings': report.get('timings'),
        'research_added_packets': len(report.get('research', {}).get('added_packets', [])),
        'semantic_accuracy_certified': False}


def replay(out):
    rows = []
    for key, path in SAVED.items():
        old = read(path)
        before = old['results'][0]
        ev = before.get('evidence', old.get('evidence'))
        class SavedClient:
            tokenizer = ApiTokenizer(MODEL)
            input_limit = 13128
            provider = 'saved_response_replay'
            def __init__(self): self.n = 0
            def __call__(self, messages, json_schema=None, provider=None):
                self.n += 1
                assert self.n <= 2
                data = before['request_generation'] if self.n == 1 else {
                    'claims': before['validation']['semantic_review']['claims']}
                return {'text': json.dumps(data, ensure_ascii=False)}
        try:
            result = ra_answer.generate(old['question'], old['understanding'], ev, SavedClient(), 'spans')
            result.update(evidence=deepcopy(ev))
            report = {**old, 'pipeline_version': VERSION, 'results': [result],
                'postprocessing_replay': {'source_report': str(path), 'source_sha256': digest(path),
                    'new_provider_calls': 0, 'fresh_semantic_review': False,
                    'changed_pre_review_citation_selection_needs_fresh_review':
                        bool(result['validation']['citation_completion'])}}
            report['review_memo'] = build_memo(report)
            write(out/'replay'/(key+'.json'), report)
            (out/'replay'/(key+'.md')).write_text(report['review_memo']['markdown'], encoding='utf-8')
            row = {'id': key, 'before_status': before['status'], **audit(report),
                'new_semantic_review': False,
                'changed_pre_review_citation_selection_needs_fresh_review':
                    bool(result['validation']['citation_completion'])}
            row['source_report_seconds'] = row.pop('seconds')
            row.pop('timings')
        except Exception as exc:
            row = {'id': key, 'replay_error': f'{type(exc).__name__}: {exc}', 'new_provider_calls': 0}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    write(out/'replay_summary.json', {'rows': rows, 'new_provider_calls': 0, 'independent_holdout': False})


def fresh(out, case_ids=None):
    if out.exists():
        raise ValueError('Choose a new output directory; never automatically repeat provider calls')
    code_paths = [*sorted((ROOT/'rag').glob('*.py')), ROOT/'rag_service/__main__.py',
                  ROOT/'rag_service/static/app.js', ROOT/'rag_service/static/style.css', Path(__file__)]
    code = {str(p.relative_to(ROOT)): digest(p) for p in code_paths}
    frozen = {str(p.relative_to(ROOT)): digest(p) for p in [RELEASE/'release_manifest.json',
        RELEASE/'candidate/chunks.jsonl', GUIDELINES/'manifest.json', GUIDELINES/'chunks.jsonl']}
    baseline = read(ROOT/'data/runs/ra_workflow_update_2026-10-12_v1/baseline.json')
    assert all(digest(ROOT/name) == sha for name, sha in baseline['index_sha256'].items())
    specs = [('normal', SAVED['normal'], ['SB17']), ('RA04', SAVED['RA04'], ['SB17','ABP654']),
             ('RA10', SAVED['RA10'], None), ('CTP43_stratification', SAVED['CTP43_stratification'], ['CTP43'])]
    cases = [{'id': key, 'question': read(path)['question'], 'products': products, 'jurisdictions': ['EU']}
             for key, path, products in specs]
    cases.append({'id': 'false_regimen', 'question': 'EMA에서 SB17 건강인 PK 시험의 135 mg 정맥 투여 조건을 알려줘.',
                  'products': ['SB17'], 'jurisdictions': ['EU']})
    if case_ids:
        if set(case_ids) - {c['id'] for c in cases}:
            raise ValueError('Unknown fixed case ID')
        cases = [c for c in cases if c['id'] in case_ids]
    cap = 3 * len(cases)
    write(out/'protocol.json', {'created_at': datetime.now(timezone.utc).isoformat(),
        'pipeline_version': VERSION, 'model': MODEL, 'reasoning_effort': 'none', 'cases': cases,
        'new_call_cap': cap, 'maximum_calls_per_case': 3, 'automatic_retries': 0, 'gold_sent': False,
        'external_grader': False, 'independent_holdout': False,
        'evaluation_scope': 'Previously inspected development cases plus one declared false-premise probe.',
        'promotion_gates': ['No process failure', 'Exact own-source quote ranges', 'Own reviewed-source boundary',
            'Normal actual-dose request answered', 'False requested regimen not answered'],
        'manual_review_required': ['Full requested meaning', 'Speaker attribution', 'Conditional bridging and dates',
                                   'Over-withholding', 'Meeting draft usefulness'],
        'code_sha256': code, 'frozen_index_sha256': frozen})
    for p in code_paths:
        target = out/'code_snapshot'/p.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(p.read_bytes())
    client = EvaluationAPIChat(out/'api', max_new_calls=cap, model=MODEL)
    pipeline = RAPipeline(RELEASE, guideline_snapshot=GUIDELINES, chat_client=client, research_rounds=1)
    rows = []
    try:
        for case in cases:
            client.case = case['id']
            print('Fresh full pipeline: '+case['id'], flush=True)
            report = pipeline.ask(case['question'], case['products'], case['jurisdictions'])
            assert len(report['generation_telemetry']) <= 3
            write(out/(case['id']+'.json'), report)
            (out/(case['id']+'.md')).write_text(report['review_memo']['markdown'], encoding='utf-8')
            row = {'id': case['id'], **audit(report), 'usage': report['usage'],
                   'new_calls': len(report['generation_telemetry'])}
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    except Exception as exc:
        message = str(exc).replace(client.secret, '[REDACTED]') if client.secret else str(exc)
        write(out/'failure.json', {'error': message, 'diagnostics': pipeline.last_failure, 'rows': rows})
        raise
    finally:
        pipeline.close();client.close()
        unchanged_code = all(digest(ROOT/name) == sha for name, sha in code.items())
        unchanged_index = all(digest(ROOT/name) == sha for name, sha in frozen.items())
        statuses = {r['id']: r['status'] for r in rows}
        write(out/'summary.json', {'rows': rows, 'attempts': client.attempts,
            'input_tokens': sum(r['usage']['input_tokens'] for r in rows),
            'output_tokens': sum(r['usage']['output_tokens'] for r in rows),
            'code_unchanged_during_evaluation': unchanged_code, 'frozen_indices_unchanged': unchanged_index,
            'observable_promotion_gates_passed': len(rows) == len(cases) and unchanged_code and unchanged_index and
                statuses.get('normal') == 'answered' and statuses.get('false_regimen') != 'answered',
            'independent_accuracy_evaluation': False, 'expert_certified': False})
        assert unchanged_code and unchanged_index


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--fresh', action='store_true')
    parser.add_argument('--case', action='append', help='Fixed fresh-case subset; cap remains three calls per case')
    args = parser.parse_args()
    if args.fresh:fresh(args.out.resolve(), args.case)
    else:replay(args.out.resolve())
