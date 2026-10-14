"""Export manually authored AI review judgments with immutable report provenance.

No model calls; exact quote checks do not produce the semantic judgments.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def cell(value):
    return str(value).replace('|', '\\|').replace('\n', ' ').replace('\r', '')


def export(annotations, out):
    review = json.loads(annotations.read_text(encoding='utf-8'))
    rows = []
    cases = []
    for case in review['cases']:
        path = Path(case['report'])
        report = json.loads(path.read_text(encoding='utf-8'))
        result = report['results'][0]
        candidates = [('final', c) for c in result['claims']] + [('rejected', c) for c in result['validation']['rejected_items']]
        assert len({c['id'] for _, c in candidates}) == len(candidates)
        assert set(case['reviews']) == {c['id'] for _, c in candidates}, case['id']
        case_rows = []
        for stage, claim in candidates:
            sources = []
            for citation in claim['sources']:
                evidence = result['evidence'][citation['evidence']]
                start, end = citation['source_range']
                assert evidence['text'][start:end] == citation['quote'], (case['id'], claim['id'])
                sources.append({**citation, 'title': evidence['title'], 'pdf_page': evidence['pdf_page'],
                    'document_id': evidence.get('document_id'), 'version_id': evidence.get('version_id'),
                    'product_id': evidence.get('product_id'), 'source_url': evidence.get('source_url')})
            judgment, finding, action = case['reviews'][claim['id']]
            row = {'case_id': case['id'], 'pipeline_version': report['pipeline_version'], 'claim_id': claim['id'],
                'stage': stage, 'claim_text': claim['text'], 'statement_role': claim['kind'],
                'pipeline_rejection_reason': claim.get('reason') if stage == 'rejected' else None,
                'judgment': judgment, 'finding': finding, 'next_action': action, 'sources': sources,
                'exact_quote_ranges_checked': True, 'review_type': 'ai_direct_source_review', 'human_expert_certified': False}
            rows.append(row)
            case_rows.append(row)
        cases.append({'id': case['id'], 'report': case['report'], 'report_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'pipeline_version': report['pipeline_version'], 'question': report['question'], 'status': result['status'],
            'final_claims': len(result['claims']), 'rejected_claims': len(result['validation']['rejected_items']), 'summary': case['summary']})
    payload = {k: v for k, v in review.items() if k != 'cases'}
    payload.update(cases=cases, rows=rows, counts=dict(Counter(r['judgment'] for r in rows)),
        new_provider_calls=0, independent_holdout=False, all_guidelines_current_validity_verified=False)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix('.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    lines = ['# AI 원문·규제 해석 검토표', '', '검토일: '+review['reviewed_on']+' · 검토자: '+review['reviewer'], '', review['scope'], '',
        '각 주장을 자기 인용 원문과 직접 대조해 아래 판정을 작성했다. 인용 문자 위치는 별도 검사했다. 사람 RA 전문가 인증·독립 정확도 점수로 해석하지 않는다. 원래 보고서·파이프라인 판정은 수정하지 않았다.', '',
        '## 답변별 요약', '', '| 답변 | 버전 | 상태 | 최종/제외 주장 | 검토 결과 |', '|---|---|---|---|---|']
    for case in cases:
        lines.append('| '+ ' | '.join(cell(x) for x in [case['id'], case['pipeline_version'], case['status'], str(case['final_claims'])+'/'+str(case['rejected_claims']), case['summary']])+' |')
    lines += ['', '## 주장별 검토', '', '| 답변 / 주장 | 기존 처리 | 검토 판정 | 원문 위치 | 발견 사항 | 다음 조치 |', '|---|---|---|---|---|---|']
    for row in rows:
        positions = list(dict.fromkeys(f"{s['evidence']} · p.{s['pdf_page']}" for s in row['sources']))
        lines.append('| '+' | '.join(cell(x) for x in [row['case_id']+' / '+row['claim_id'], row['stage'], row['judgment'], '; '.join(positions), row['finding'], row['next_action']])+' |')
    check = review['guideline_status_check']
    if check.get('registry_table'):
        lines += ['', '## 지침 상태 확인', '', check['scope'], '', '[문서별 공식 상태 검토표]('+check['registry_table']+'). 확인일: '+check['checked_on']+'.']
    else:
        lines += ['', '## 지침 현행 상태 확인', '', check['scope']+'.', '', check['current_effective']+' '+check['revision'], '',
            '[EMA 공식 등록 페이지]('+check['url']+'), [공식 지침 PDF]('+check['pdf_url']+'). 확인일: '+check['checked_on']+'. 기존 검색 스냅샷은 유지했다.']
    lines += ['', '## 추적 자료', '', '전체 주장 문장·선택 인용·문서 버전·원문 위치·보고서 해시는 [JSON 검토표]('+out.with_suffix('.json').name+')에 저장했다. 이 수동 검토·표 생성의 제공자 호출은 0회다. '+review.get('evaluation_note','모든 문항은 기존 EMA 개발 문항이며 FDA/HC 전이는 아직 검토하지 않았다.'), '']
    out.with_suffix('.md').write_text('\n'.join(lines), encoding='utf-8')
    return {'cases':len(cases), 'final_claims':sum(r['stage']=='final' for r in rows), 'rejected_claims':sum(r['stage']=='rejected' for r in rows), 'counts':payload['counts'], 'new_provider_calls':0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--annotations', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export(args.annotations, args.out), ensure_ascii=False))


if __name__ == '__main__':
    main()
