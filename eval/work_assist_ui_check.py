"""Run the work-assist DOM check against an isolated fixture server: the v4 scenario, then the v5 issue scenario.

No provider calls and no project data: the server is the test fixture (tests/test_workspace_http.py) on a free port. The
v5 guide is made beforehand with the fake RA service and fake model from tests/test_issue_guide.py, into the server's own
guide folder, so the page reads it like any finished guide.
linkedom/cssom come from NODE_PATH (work/memo_ui_test/node_modules, test-only packages).
"""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from http_server import Running  # noqa: E402
from test_internal_work import BUNDLE  # noqa: E402
from test_issue_guide import BUNDLE as ISSUE_BUNDLE, FakeJobs, FakeModel  # noqa: E402
from test_workspace_http import fixture_jobs  # noqa: E402

from rag_service.issue_guide import IssueGuideService  # noqa: E402


def main():
    with fixture_jobs() as jobs:
        work = jobs.workspaces.create({'name': 'P17 내부 자료 UI 검사', 'ingredient': 'ustekinumab'})
        jobs.workspaces.register_documents(work['id'], BUNDLE)
        issue = jobs.workspaces.create({'name': 'P17 이슈 대응 UI 검사', 'ingredient': 'ustekinumab'})
        jobs.workspaces.register_documents(issue['id'], ISSUE_BUNDLE)
        maker = IssueGuideService(FakeJobs(jobs.workspaces), jobs.issue_guides.directory, chat_factory=lambda folder: FakeModel(), poll=0.01)
        maker.submit(issue['id'], {'issue_id': 'ISSUE-IV-FCRN'})
        maker.executor.shutdown(wait=True)
        server = Running(jobs)
        try:
            env = {**os.environ, 'NODE_PATH': str(ROOT / 'work/memo_ui_test/node_modules')}
            code = 0
            for project_id, flag in ((work['id'], []), (issue['id'], ['--issue'])):
                done = subprocess.run(['node', str(ROOT / 'eval/work_assist_dom_check.js'), f'http://127.0.0.1:{server.server_port}', project_id, *flag],
                                      env=env, capture_output=True, text=True, encoding='utf-8', timeout=120)
                print(done.stdout.strip() or done.stderr.strip())
                code = code or done.returncode
            return code
        finally:
            server.shutdown()


if __name__ == '__main__':
    raise SystemExit(main())
