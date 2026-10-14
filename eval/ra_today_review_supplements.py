"""Save explicitly authored AI review corrections with exact source provenance.
These notes never enter the retrieval corpus or generation prompt."""
import hashlib
import json
from pathlib import Path
import fitz
from eval.ra_today_eval import ROOT, GUIDELINES

def main():
    base=ROOT/'data/runs/ra_today_completion_2026-10-12_v3'
    e4=json.loads((base/'E04.json').read_text(encoding='utf-8'))['results'][0]['evidence']['E2']
    guidelines=[json.loads(s) for s in (GUIDELINES/'chunks.jsonl').read_text(encoding='utf-8').splitlines()]
    ca=next(c for c in guidelines if c['document_id']=='GL:HC-BIOSIMILAR-2026:non-clinical-and-clinical' and c['pdf_page']==5)
    raw=next((ROOT/'data/raw').glob('7783bf048fda*.pdf'))
    version=hashlib.sha256(raw.read_bytes()).hexdigest()
    with fitz.open(raw) as pdf:label_text=pdf[2].get_text(sort=True)
    assert 'Plaque Psoriasis' in label_text and 'Subcutaneous Adult' in label_text
    def source(block,begin,end=None):
        at=block['text'].index(begin);stop=block['text'].index(end,at) if end else len(block['text'])
        return {'document_id':block['document_id'],'version_id':block['version_id'],'pdf_page':block['pdf_page'],
            'source_url':block.get('source_url'),'quote':block['text'][at:stop],'source_range':[at,stop],
            'range_basis':'retained_evidence_text' if block is e4 else 'frozen_native_guideline_page',
            'source_refs':block.get('source_refs',block.get('provenance',{}).get('source_refs',[]))}
    rows=[
      {'case_id':'E04','corrected_review_statement_ko':'비EEA 비교약으로 특정 임상 및 생체 내 비임상시험을 수행하는 접근에서는 세 제품의 분석 브리징 자료가 항상 포함되어야 하며, 세 제품의 임상 PK/PD 브리징 자료가 추가로 포함될 수도 있다. 접근의 수용 가능성과 필요한 자료 종류는 사례별·제품 유형별 판단으로, 사전 규제기관 논의가 권고된다. 최종 적절성 판단은 신청서 심사에서 이루어진다.',
       'sources':[source(e4,'If certain clinical and in vivo non-clinical studies')]},
      {'case_id':'U01','corrected_review_statement_ko':'저장된 FDA Starjemza 라벨의 성인 판상 건선 피하 용법은 체중 100 kg 이하에서 45 mg, 100 kg 초과에서 90 mg을 최초 및 4주 후 투여하고 이후 같은 용량을 매 12주마다 투여한다. 건선성 관절염 절의 동반 판상 건선 조건과 구분한다.',
       'sources':[{'document_id':'FDA:label:761419s000lbl','version_id':version,'pdf_page':3,
          'source_url':'https://www.accessdata.fda.gov/drugsatfda_docs/label/2025/761419s000lbl.pdf',
          'quote':label_text,'source_range':[0,len(label_text)],'range_basis':'separately_extracted_native_pdf_page',
          'source_refs':[{'path':str(raw),'sha256':version,'pdf_page':3,'parser':'pymupdf_native','parser_version':fitz.VersionBind}]}]},
      {'case_id':'C04','corrected_review_statement_ko':'2026 Health Canada 임상 절에서는 바이오시밀러가 CRBD와 비교되고 분석 및 임상약리학 시험으로 충분히 특성평가된 경우 비교 임상 효능시험은 통상 요구되지 않는다고 설명한다. 안전성·비교 면역원성 자료는 필요하며 비교 임상약리학 시험에서 수집해야 한다. 비교 효능시험을 포함한다면 제출자료의 맥락에서 시험의 역할과 결과의 가치를 설명해야 한다. 모든 시험이 항상 불필요하다는 뜻으로 확대하지 않는다.',
       'sources':[source(ca,'Comparative clinical efficacy studies')]}]
    payload={'review_type':'ai_direct_source_review_supplement','human_expert_certified':False,
        'new_provider_calls':0,'used_in_generation_or_index':False,'original_reports_modified':False,'rows':rows}
    out=ROOT/'docs/rag/RA_TODAY_REVIEW_SUPPLEMENTS_2026-10-12.json'
    out.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['# 누락 항목의 AI 원문 검토 보완 메모','',
      '자동 답변의 누락을 제가 별도로 원문 확인하여 작성한 검토 문장이다. 모델이 새로 답한 결과 또는 사람 전문가 인증으로 표시하지 않는다. 원래 답변·인용을 수정하지 않았으며 검색 색인·생성 입력에도 넣지 않았다.','',
      '| 질문 | 검토 후 보완 문장 | 근거 |','|---|---|---|']
    for row in rows:
        s=row['sources'][0]
        lines.append(f"| {row['case_id']} | {row['corrected_review_statement_ko']} | [{s['document_id']} · p.{s['pdf_page']}]({s['source_url']}) |")
    lines+=['','정확한 원문·페이지·문서 버전·해시·추출 위치는 [JSON 추적 자료](RA_TODAY_REVIEW_SUPPLEMENTS_2026-10-12.json)에 저장했다.']
    out.with_suffix('.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'supplements':len(rows),'new_provider_calls':0,'used_in_generation':False}))

if __name__=='__main__':main()
