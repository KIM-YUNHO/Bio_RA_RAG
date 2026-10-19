"""Run the work-assist DOM check against an isolated fixture server with the v4 scenario and a fake model.

No provider calls and no project data: the server is the test fixture (tests/test_workspace_http.py) on a free port.
linkedom/cssom come from NODE_PATH (work/memo_ui_test/node_modules, test-only packages).
"""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from http_server import Running  # noqa: E402
from test_internal_work import BUNDLE  # noqa: E402
from test_workspace_http import fixture_jobs  # noqa: E402

# The fake model returns a draft with one stale date, so the audit warning path is rendered too.
ANSWER = {'position': '현재 적용 기준은 v1.0이다.', 'sections': {
    'judgment': [{'text': '재산정 범위 98–130%는 미승인 초안이라 적용할 수 없다.', 'basis': 'criterion', 'source_unit_ids': ['U21', 'U23']}],
    'evidence': [{'text': 'IV CPB 결과는 4월 24일에 받는다.', 'basis': 'status', 'source_unit_ids': ['U17']}],
    'gaps': [{'text': 'PK 관련성 자료는 아직 없다.', 'basis': 'gap', 'source_unit_ids': ['U24']}],
    'actions': [{'text': '결과 보고서는 v1.0 범위로 작성한다.', 'basis': 'inference', 'source_unit_ids': ['U23']}]}}


class Fake:
    def __call__(self, messages, json_schema=None):
        return {'text': json.dumps(ANSWER, ensure_ascii=False), 'prompt_tokens': 0, 'completion_tokens': 0, 'model_reported': 'fake'}

    def close(self):
        pass


def main():
    kind = sys.argv[1] if len(sys.argv) > 1 else 'fastapi'
    with fixture_jobs() as jobs:
        jobs.work_drafts.chat_factory = lambda folder: Fake()
        project = jobs.workspaces.create({'name': 'P17 내부 자료 UI 검사', 'ingredient': 'ustekinumab'})
        jobs.workspaces.register_documents(project['id'], BUNDLE)
        server = Running(jobs, kind)
        try:
            env = {**os.environ, 'NODE_PATH': str(ROOT / 'work/memo_ui_test/node_modules')}
            done = subprocess.run(['node', str(ROOT / 'eval/work_assist_dom_check.js'), f'http://127.0.0.1:{server.server_port}', project['id'], '--submit'],
                                  env=env, capture_output=True, text=True, encoding='utf-8', timeout=120)
            print(done.stdout.strip() or done.stderr.strip())
            return done.returncode
        finally:
            server.shutdown()


if __name__ == '__main__':
    raise SystemExit(main())
