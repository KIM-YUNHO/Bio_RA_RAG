"""Create a separate SB17 source-replay notebook using normal memo persistence; never runs a model.

Default output is isolated local files. --postgres adds only the named demo notebook to the app DB.
Existing demo notebooks are returned as-is, including any user edits. Gold answers are not read.
Run from the repository root: python -m eval.seed_memo_scenario [--postgres]
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import time
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid4, uuid5

import fitz

from rag_service.__main__ import Jobs
from rag_service.workspaces import PostgresWorkspaceStore, WorkspaceStore, source_packet

ROOT = Path(__file__).resolve().parents[1]
SHA = 'fea275487bb971962201b77aec3d2e12cee7d545be026b378f1980fa272f6e76'
PDF = ROOT / 'data/raw' / (SHA + '.pdf')
NAME = '[공개자료 재현 v1] SB17 IV FcRn 검토'
SUBJECT = 'SB17 · 130 mg IV vial · FcRn'
JOB_ID = uuid5(NAMESPACE_URL, 'biosim-rag/public-source-replay/sb17-fcrn-memo/v1').hex


def report():
    if hashlib.sha256(PDF.read_bytes()).hexdigest() != SHA:
        raise ValueError('Canonical SB17 PDF hash mismatch')
    with fitz.open(PDF) as pdf:
        text = re.sub(r'\s+', ' ', pdf[34].get_text()).strip()
    start = text.index('Binding activity to FcRn')
    end = text.index('Secondary pharmacology,', start)
    paragraph = text[start:end].strip()
    numeric = paragraph[:paragraph.index('Thus,')].strip()
    if '98-102%' not in numeric or '107-127%' not in numeric:
        raise ValueError('Canonical numeric quote changed')
    quote = lambda value: {'evidence': 'E1', 'quote': value, 'source_range': [text.index(value), text.index(value) + len(value)],
                           'offset_basis': 'evidence_text', 'unit_id': 'public-replay-p35-fcrn'}
    claims = [
        {'id': 'KU-05', 'text': 'SB17/Pyzchiva 130 mg IV vial의 공개 FcRn 상대 결합활성 관측 범위는 98–102%다. 개별 lot 값·평균·반복 수를 뜻하지 않는다.',
         'kind': 'source_fact', 'sources': [quote(numeric)]},
        {'id': 'KU-06', 'text': '같은 SB17 IV FcRn 사례의 유사성 범위는 107–127%다. 다른 개발품의 승인 기준이나 출하 규격으로 옮기지 않는다.',
         'kind': 'source_fact', 'sources': [quote(numeric)]},
        {'id': 'KU-10', 'text': 'CHMP는 SB17 IV vial의 작은 차이와 SC PFS에서 차이가 관찰되지 않은 맥락에서 생물학적으로 의미 있는 영향이 예상되지 않는다고 평가했다. 이 판단은 해당 제품·자료에 한정한다.',
         'kind': 'regulator_position', 'sources': [quote(paragraph)]}]
    claims = [{**c, 'products': ['SB17'], 'task_ids': ['SB17-REPLAY'], 'claim_basis': 'public_source_reconstruction'} for c in claims]
    evidence = {'document_id': 'EMA:63563', 'version_id': SHA, 'chunk_id': 'public-replay-p35',
        'title': 'Pyzchiva EPAR — Public assessment report (2024-02-22)', 'source': 'EMA', 'jurisdiction': 'EU',
        'product_id': 'SB17', 'pdf_page': 35, 'pdf_page_label': '35', 'text': text,
        'source_url': 'https://www.ema.europa.eu/en/documents/assessment-report/pyzchiva-epar-public-assessment-report_en.pdf',
        'source_refs': [], 'source_spans': [], 'structure': {'kind': 'text'},
        'document_metadata': {'source_kind': 'public_regulatory_assessment', 'report_date': '2024-02-22'}}
    return {'mode': 'ra', 'origin': 'public_source_replay',
        'question': '[공개자료 재현 · 수동 작성] SB17 IV FcRn 결과·사례 범위·기관 판단을 구분해 검토',
        'seconds': 0, 'usage': {'input_tokens': 0, 'output_tokens': 0},
        'understanding': {'products': ['SB17'], 'jurisdictions': ['EU']},
        'results': [{'status': 'answered', 'answer_ko': '\n\n'.join(c['text'] + ' [E1]' for c in claims),
                     'claims': claims, 'evidence': {'E1': evidence}}]}


def seed(store, archive):
    existing = next((p for p in store.list()['projects'] if p['name'] == NAME), None)
    if existing:
        return {'created': False, 'project_id': existing['id'], 'job_id': JOB_ID,
                'note': 'Existing demo left unchanged; user edits preserved.'}
    replay = report()  # Verify all scientific source input before writing.
    pipeline = SimpleNamespace(bundle=SimpleNamespace(docs={'sb17': {
        'document_id': 'EMA:63563', 'version_id': SHA, 'raw_path': str(PDF)}}), guidelines=None)
    jobs = Jobs(pipeline, archive, workspace_store=store)
    try:
        job = {'id': JOB_ID, 'state': 'completed', 'created_at': time.time(), 'question': replay['question'], 'report': replay}
        path = Path(archive) / (JOB_ID + '.json')
        if path.exists():
            archived = jobs.get(JOB_ID)
            if jobs.reviews.report_hash(archived) != jobs.reviews.report_hash(job):
                raise ValueError('Existing replay report differs; no overwrite performed')
        else:
            jobs.persist(job)
            if job.get('archive_error'):
                raise OSError(job['archive_error'])
        jobs.jobs[JOB_ID] = job
        source = source_packet(jobs, {'job_id': JOB_ID, 'result_index': 0, 'kind': 'answer'})
        project = store.create({'name': NAME, 'ingredient': 'ustekinumab'})
        payload = {'request_id': uuid4().hex, 'body': '공개 EPAR의 수동 재구성 예. 실제 내부 실험자료·RAG 생성 결과가 아니다.',
                   'stage_ids': ['cmc'], 'products': ['SB17'], 'tags': ['공개자료 재현', 'FcRn'],
                   'annotations': [{'key': key, 'type': kind, 'subject': SUBJECT, 'modality': 'reported',
                                    'note': '원 시험 유효성·성숙도는 미공개. 공개자료 재구성, 전문가 검토 전.'}
                                   for key, kind in [('KU-05', 'fact'), ('KU-06', 'criterion'), ('KU-10', 'interpretation')]]}
        store.add(project['id'], payload, source)
        for kind, modality, body in [
            ('interpretation', 'proposal', '[합성 내부 의견 · REPLAY-D06] 가상 검토팀: 수치 비교와 의미 해석을 분리하고 IV/PFS·전하 변이·결합 설명을 추가 확인하자. 미승인 의견이며 실제 SB17 내부 코멘트가 아니다.'),
            ('action', 'planned', '[합성 업무 · REPLAY-D08 TASK-R01] 가상 이분석에게 관측/사례 범위와 IV/PFS 출처를 구분한 검토표를 요청한다. 모의 변경 합의일은 2026-09-04이며 T3(09-07) 공급자료에는 완료 증빙이 없다. 실제 회사의 일정이 아니다.'),
            ('question', 'proposal', '[합성 질문 · REPLAY-D09] 향후 자사 후보에서 FcRn 차이가 관찰되면 제형별 비교와 구조·기능·PK 관련성 자료를 어떻게 구성할 것인가? 현재 자사 시험값은 미제공이며 기관에 미제출·답변 없음.')]:
            store.add(project['id'], {'request_id': uuid4().hex, 'body': body, 'stage_ids': ['cmc'],
                'products': [], 'tags': ['합성 업무', 'FcRn'],
                'annotations': [{'key': 'manual', 'type': kind, 'subject': SUBJECT, 'modality': modality,
                                 'data_maturity': 'not_applicable', 'run_validity': 'not_applicable',
                                 'note': '공개 사례를 활용해 만든 합성 업무. 실제 회사 발언·약속·기관 답변이 아니다.'}]})
        final = store.get(project['id'])
        return {'created': True, 'project_id': project['id'], 'job_id': JOB_ID,
                'memo_count': len(final['memos']), 'item_count': sum(len(m['items']) for m in final['memos']),
                'model_calls': 0, 'gold_cases_evaluated': 0}
    finally:
        jobs.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--postgres', action='store_true', help='Add the separate named demo notebook to the application DB')
    parser.add_argument('--output', type=Path, default=ROOT/'work/internal_pipeline_memo_demo')
    args = parser.parse_args()
    store = PostgresWorkspaceStore() if args.postgres else WorkspaceStore(args.output/'notebooks')
    archive = ROOT/'data/runs/ra_service_reports' if args.postgres else args.output/'reports'
    result = seed(store, archive)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
