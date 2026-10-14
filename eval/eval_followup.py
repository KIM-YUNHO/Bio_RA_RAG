"""Score follow-up inheritance rules against eval/followup_cases_v0.1.json; exits 1 on any mismatch. No model calls."""
import json
from pathlib import Path
import sys

from rag import followup

ROOT = Path(__file__).resolve().parents[1]


def main(path=ROOT/'eval/followup_cases_v0.1.json'):
    cases = json.loads(Path(path).read_text(encoding='utf-8'))['cases']
    failures = []
    for case in cases:
        context = followup.context_from_report({'question': case['previous'], 'results': [{'answer_ko': ''}]})
        got = {k: sorted(v) for k, v in followup.resolve(case['follow_up'], context)['inherited'].items()}
        want = {k: sorted(v) for k, v in case['inherited'].items()}
        if got != want: failures.append({'id': case['id'], 'follow_up': case['follow_up'], 'want': want, 'got': got})
    summary = {'version': followup.VERSION, 'cases': len(cases), 'passed': len(cases)-len(failures), 'failures': failures,
               'model_calls': 0}
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main(*sys.argv[1:]))
