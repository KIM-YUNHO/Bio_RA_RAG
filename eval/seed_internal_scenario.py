"""Register the v4 internal-document scenario as its own pipeline. No model calls.

Default: an isolated file workspace under work/. --postgres: the app database (adds one pipeline; other pipelines are
not read or changed). Re-running is safe: the same pipeline is reused and identical document versions are skipped.
"""
import argparse
import json
from pathlib import Path

from rag_service.workspaces import PostgresWorkspaceStore, WorkspaceStore

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / 'docs/demo/internal_pipeline_v4/02_INTERNAL_DOCUMENTS.json'
NAME = '[시나리오 v4] P17 FcRn 내부 업무'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--postgres', action='store_true')
    parser.add_argument('--out', type=Path, default=ROOT / 'work/internal_v4/seed_notebooks')
    args = parser.parse_args()
    if args.postgres:
        from rag.llm.llm import load_env
        load_env()
        store = PostgresWorkspaceStore()
    else:
        store = WorkspaceStore(args.out)
    bundle = json.loads(INPUT.read_text(encoding='utf-8'))
    existing = next((p for p in store.list()['projects'] if p['name'] == NAME), None)
    project_id = existing['id'] if existing else store.create({'name': NAME, 'ingredient': bundle['ingredient']})['id']
    result = store.register_documents(project_id, bundle)
    print(json.dumps({'project_id': project_id, 'name': NAME, 'storage': 'postgres' if args.postgres else str(args.out),
                      'added': len(result['added']), 'skipped': len(result['skipped']), 'document_count': result['document_count']},
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
