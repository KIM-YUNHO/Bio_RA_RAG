"""Replay the completion judgement on saved reports: recorded (old code) versus current code.

Claims, evidence and plans are taken from the reports; only completion is recomputed, so
no model call is made. Per report: recorded status, the current code's task-level checks
(checkable / minimum met), and whether the original request would close as complete.

  python eval/completion_replay.py data/runs/planner_facets_2026-10-15/e2e_branch_v2
"""
import glob, json, sys
from collections import Counter
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def replay(report):
    from rag import ra_requirements
    from rag.ra_obligations import planned
    question, result = report['question'], report['results'][0]
    understanding = deepcopy(report['understanding'])
    understanding['tasks'] = [ra_requirements.route_task(question, t) for t in understanding['tasks']]
    claims, evidence = result.get('claims', []), result.get('evidence', {})
    checks = ra_requirements.coverage(question, understanding, evidence, claims)['tasks']
    from rag.ra_obligations import uncovered_sentences
    uncovered = uncovered_sentences(question, understanding['tasks']) if planned(understanding['tasks']) else []
    obligations = (result.get('question_requirement_coverage') or {}).get('requirements', [])
    complete = bool(claims) and not uncovered and all(r['completion_checkable'] and r['minimum_requirements_met']
                                                      for r in checks) and all(
        r.get('observable_values_preserved', True) for r in obligations)
    return {'status_recorded': result['status'],
            'status_replayed': 'not_found' if not claims else 'answered' if complete else 'partial',
            'tasks': [{'facets': r['requested_conditions'].get('facets'), 'checkable': r['completion_checkable'],
                       'met': r['minimum_requirements_met'],
                       'failed': [c['kind'] + ':' + str(c['requested']) for c in r['checks'] if not c['satisfied']]}
                      for r in checks],
            'uncovered': uncovered}


def main():
    total = Counter()
    for folder in sys.argv[1:]:
        for path in sorted(glob.glob(str(Path(folder) / '*_[12].json'))):
            r = replay(json.loads(Path(path).read_text(encoding='utf-8')))
            total[(r['status_recorded'], r['status_replayed'])] += 1
            print(Path(path).stem, r['status_recorded'], '->', r['status_replayed'],
                  json.dumps(r['tasks'], ensure_ascii=False), r['uncovered'] or '')
    print({f'{a}->{b}': n for (a, b), n in total.items()})


if __name__ == '__main__':
    main()
