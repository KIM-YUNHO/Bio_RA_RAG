"""Paired live comparison of missing-only G and source-preserving enhanced H."""
import argparse
import copy
from datetime import datetime,timezone
import json
from pathlib import Path
import re
import shutil
import time

import fitz
from PIL import Image
from compare_study.bench.common import read,write,sha
from compare_study.settings import ROOT,activate
from compare_study.design_policy import VERSION,table_alarms,candidate_gate,boxed_regions,preserve_charts,adjacent_visual_context
from extend_abp654_results import related,page_rows,score_extra,output_counts,embed_figures
from select_pipeline import launch,inventory_inputs,recognize,fresh_score

POLICY={
    'version':VERSION,'control':'live missing-only G, unchanged primary tables and figures',
    'improved':'H: preserve original chart evidence, missing closed-border regions, multi-value table repair',
    'primary':'fresh Heron + RapidOCR/PP-OCRv6 + TableFormer accurate + existing ra_prep corrections',
    'shared':'same new primary and same new independent PP-DocLayoutV3 inventory',
    'vlm':'PaddleOCR-VL-1.6-0.9B table; independent process and new calls for each branch',
    'crop_dpi':300,'crop_padding_px':12,'max_image_edge':1800,
    'box_policy':'closed source border: width>=40%, height 2.5%-45%, each edge coverage>=70%; adjacent visual notes preserved as figure context',
    'table_alarm':'two or more value(parenthesized-statistic) groups in one non-first-row cell',
    'replacement_gate':'valid nonempty structure, all original numeric literal types, >=90% label word coverage, fewer multi-value cells',
    'label_normalization':'existing comparable_text math-subscript normalization before label words and value groups',
    'chart_gate':'independent chart and original picture: preserve figure and archive conflicting table',
    'gold_used_for_routing':False,'retries':0,
    'limit':'same exposed development pages; gates do not prove clinical semantics',
}


def pictures(run,jobs):
    result={}
    for job in jobs:
        audit=read(run/'native/DOC-GPU/raw'/job['document']['key']/'visual_audit.json')
        events={e['ref']:e for e in audit.get('events',[])}
        with fitz.open(job['path']) as pdf:
            for local,number in enumerate(job['page_map'],1):
                h=pdf[number-1].rect.height
                items=[]
                for b in audit['before_blocks']:
                    if b['type']!='picture' or b['page']!=local:continue
                    l,bot,r,top=b['bbox']
                    items.append({'ref':b['ref'],'bbox':[l,h-top,r,h-bot],
                                  'caption':b.get('caption',''),'ocr_text':events.get(b['ref'],{}).get('ocr_text','')})
                result[Path(job['original_path']).name,number]=items
    return result


def detections(page):
    sx,sy=page['size'][0]/page['pixels'][0],page['size'][1]/page['pixels'][1]
    return [{**b,'bbox':[v*(sx if i%2==0 else sy) for i,v in enumerate(b['coordinate'])]}
            for b in page['boxes']]


def plan(primary,inventory,raw_pictures,improved):
    result=copy.deepcopy(primary)
    calls=[];events=[];visual_events=[]
    for page in inventory['pages']:
        key=(page['document'],page['page'])
        row=result[key]
        ds=detections(page)
        if improved:
            row,changes=preserve_charts(row,ds,raw_pictures[key])
            result[key]=row
            visual_events.extend({'document':key[0],'page':key[1],**c} for c in changes)
        proposals=[{'bbox':d['bbox'],'origin':'PP-DocLayoutV3 table','detector_score':d['score']}
                   for d in ds if d['label']=='table']
        if improved:
            with Image.open(page['image']) as image:
                proposals+=boxed_regions(image,page['size'])
        for proposal in proposals:
            box=proposal['bbox']
            overlaps=[i for i,b in enumerate(row['table_bboxes']) if related(box,b)]
            figures=[f for f in row.get('figures',[]) if not f.get('decorative') and not f.get('parent_id')
                     and related(box,f['bbox'])]
            event={'document':key[0],'page':key[1],**proposal,'primary_tables':overlaps,
                   'figure_conflicts':[f['figure_id'] for f in figures]}
            adjacent=adjacent_visual_context(box,row.get('figures',[]),page['size'][1]) if proposal['origin']=='source_closed_border' else None
            if overlaps:event['action']='keep_existing_table'
            elif figures:event['action']='preserve_figure'
            elif adjacent:
                event['action']='preserve_visual_context';event['figure_id']=adjacent
                row.setdefault('visual_context_regions',[]).append({**proposal,'figure_id':adjacent})
            elif any((c['document'],c['page'])==key and related(box,c['bbox']) for c in calls):
                event['action']='duplicate_region'
            else:
                event['action']='add_missing_region'
                calls.append({'document':key[0],'page':key[1],'key':page['key'],'agency':page['agency'],
                              **proposal,'action':'add','source_pdf':page['source_pdf']})
            events.append(event)
        if improved:
            for i,(table,box) in enumerate(zip(row['tables'],row['table_bboxes'])):
                alarm=table_alarms(table)
                if alarm['issues']:
                    calls.append({'document':key[0],'page':key[1],'key':page['key'],'agency':page['agency'],
                        'bbox':box,'origin':'existing_table_structure_alarm','action':'replace','table_index':i,
                        'original_table':copy.deepcopy(table),'quality_alarm':alarm,'source_pdf':page['source_pdf']})
                    events.append({'document':key[0],'page':key[1],'bbox':box,'action':'repair_existing_table',
                                   'table_index':i,'quality_alarm':alarm})
    return result,calls,events,visual_events


def crops(branch,calls):
    for i,job in enumerate(calls):
        job['id']=f"{job['key']}_p{job['page']:03d}_{job['action']}_{i:03d}"
        target=branch/'crops'/(job['id']+'.png');target.parent.mkdir(exist_ok=True)
        with fitz.open(job['source_pdf']) as pdf:
            page=pdf[job['page']-1]
            padding=POLICY['crop_padding_px']*72/POLICY['crop_dpi']
            clip=(fitz.Rect(job['bbox'])+(-padding,-padding,padding,padding)) & page.rect
            pix=page.get_pixmap(dpi=POLICY['crop_dpi'],clip=clip,alpha=False)
            image=Image.frombytes('RGB',[pix.width,pix.height],pix.samples)
            image.thumbnail((POLICY['max_image_edge'],POLICY['max_image_edge']),Image.Resampling.LANCZOS)
            image.save(target)
        job.update(image=str(target),image_sha256=sha(target),pixels=list(image.size),context_bbox=list(clip))


def merge(rows,target,improved):
    result=copy.deepcopy(rows);decisions=[]
    for row in result.values():
        row['table_provenance']=[{'origin':'fresh Docling + corrections','table_index':i} for i in range(len(row['tables']))]
        row['flags'].update(selected_paddle_calls=0,added_tables=0,replaced_tables=0,
                            design_policy=VERSION if improved else 'missing-only-G',gold_consulted_for_selection=False)
    for candidate in target['results']:
        job=candidate['job'];row=result[job['document'],job['page']]
        row['seconds']+=candidate['seconds'];row['flags']['selected_paddle_calls']+=1
        gate=candidate_gate(candidate['table'],candidate['content'],job.get('original_table')) if improved else {
            'accepted':candidate['accepted'],'semantic_status':'unverified source extraction candidate'}
        decision={'crop_id':job['id'],'document':job['document'],'page':job['page'],'action':job['action'],
                  'gate':gate,'candidate_table':candidate['table'],'bbox':job['bbox']}
        row.setdefault('vlm_candidates',[]).append(copy.deepcopy(decision))
        if gate['accepted']:
            provenance={'origin':'new PaddleOCR-VL table crop','crop_id':job['id'],'gate':gate}
            if job['action']=='replace':
                index=job['table_index']
                row.setdefault('replaced_table_candidates',[]).append({'table_index':index,
                    'old':copy.deepcopy(row['tables'][index]),'bbox':row['table_bboxes'][index]})
                row['tables'][index]=copy.deepcopy(candidate['table'])
                row['table_provenance'][index]=provenance
                row['flags']['replaced_tables']+=1
            else:
                row['tables'].append(copy.deepcopy(candidate['table']))
                row['table_bboxes'].append(job['bbox']);row['table_provenance'].append(provenance)
                row['flags']['added_tables']+=1
        decisions.append(decision)
    return result,decisions


def save_rows(run,stage,rows,jobs,inventory):
    by_page={(p['document'],p['page']):p for p in inventory['pages']}
    for key,row in rows.items():
        for f in row.get('figures',[]):
            if f.get('crop_path'):continue
            page_info=by_page[key]
            with fitz.open(page_info['source_pdf']) as pdf:
                page=pdf[key[1]-1]
                clip=fitz.Rect(f['bbox']) & page.rect
                target=run/'visual_crops'/stage/page_info['key']/(f['figure_id']+'.png')
                target.parent.mkdir(parents=True,exist_ok=True)
                page.get_pixmap(dpi=300,clip=clip,alpha=False).save(target)
            f.update(crop_path=str(target),crop_sha256=sha(target),crop_dpi=300)
        directory=next(j['document']['key'] for j in jobs if Path(j['original_path']).name==key[0])
        saved=copy.deepcopy(row);saved.pop('_size',None)
        write(run/'native'/stage/directory/f'{key[1]:03d}.json',saved)


def review(run,reference,cases,scores,times,decisions):
    import build_review as view
    from review_extensions import extend
    old=(reference/'review_selection.html').read_text(encoding='utf-8')
    data=json.loads(re.search(r'<script id="review-data" type="application/json">([\s\S]*?)</script>',old).group(1))
    labels=json.loads(re.search(r'const labels=(\{[^\n]*?\});',old).group(1))
    labels.update(design_control='현재 G 재실행: 누락 표만 VL',
                  design_improved='보완 H: 그림 보존 + 누락·불완전 표 VL + 후보 검증')
    data['cases']+=list(cases);data['run_id']=run.name
    for case,rows in cases.items():
        data['accuracy']['cases'][case]=scores[case]
        data['metrics']['native'][case]={**times[case],**output_counts(rows),'status':'complete',
                                        'label':labels[case],'stages':[]}
        for agency in data['performance']['by_agency']:
            data['performance']['by_agency'][agency][case]={'processing_seconds':None,'seconds_per_page':None}
        for p in data['pages']:
            row=embed_figures(rows[p['document'],p['page']]);row.pop('_size',None)
            warnings=['같은 새 Docling·영역 검출을 공유하고 VLM은 분기마다 새로 실행했습니다.']
            if case=='design_improved':
                warnings.append(f"추가 표 {row['flags']['added_tables']}개, 교체 표 {row['flags']['replaced_tables']}개. 후보·검증 사유는 candidate_decisions.json에 있습니다.")
                if row.get('suppressed_table_candidates'):
                    warnings.append('차트와 원시 picture 근거가 겹쳐 가짜 표 후보를 분리하고 원본 그림을 보존했습니다.')
                rejects=sum(not c['gate']['accepted'] for c in row.get('vlm_candidates',[]))
                if rejects:warnings.append(f'검증에서 거절한 표 후보 {rejects}개는 원래 표를 유지했습니다.')
            row['review_warnings']=warnings;p['results'][case]=row
    html=extend(view.HTML)
    html=html.replace("const labels={paddle:'Paddle',cascade:'Cascade + Qwen',docling:'Docling'};",
                      'const labels='+json.dumps(labels,ensure_ascii=False)+';')
    html=html.replace('같은 페이지를 세 경로의 본문·표 값과 대조합니다.','기존 7개 결과와 새 G·H 비교를 같은 원문에서 확인합니다.')
    html=html.replace('처리 시간은 실제 1회 실행값입니다.','G·H 총시간은 공유 공통 단계와 독립 VLM 분기의 실측 합계입니다. 저장 후보 E의 전체 속도는 미측정입니다.')
    html=html.replace('</head>','<style>.cards{grid-template-columns:repeat(auto-fit,minmax(210px,1fr))}.caseTabs{flex-wrap:wrap;gap:5px}.panelhead{flex-wrap:wrap}</style></head>')
    html=html.replace('<main>','<main><section class="checks"><h2>현재 G와 보완 H의 실제 비교</h2><p>같은 기본 추출·검출·OCR·모델 설정입니다. H는 원시 그림 보존, 닫힌 테두리 누락 영역, 여러 수치 묶음이 합쳐진 표의 선별 전사와 보존 검증을 추가했습니다.</p><p><a href="DESIGN_COMPARISON_REPORT.md">실측 비교 보고서</a> · <a href="policy.json">동결 규칙</a> · <a href="candidate_decisions.json">후보 채택·거절</a></p></section>',1)
    script="<script>const savedCards=renderCards;renderCards=function(){savedCards();document.querySelectorAll('#cards .card').forEach((x,i)=>{if(DATA.cases[i]==='docling_paddle_cached'){x.querySelector('.big').textContent='속도 미측정';x.querySelector('small').textContent='저장 후보 조합';}})};renderCards();</script>"
    html=html.replace('</body>',script+'</body>')
    payload=json.dumps(data,ensure_ascii=False,allow_nan=False).replace('<',r'\u003c')
    (run/'review_design.html').write_text(html.replace('__DATA__',payload),encoding='utf-8')


def finish(run,baseline,reference,jobs,primary,cases,times,stages,decisions,visual):
    gold=read(baseline/'gold.json')
    original=page_rows(baseline,jobs,'DOC-GPU')
    base_metrics=fresh_score(primary,gold,original,read(baseline/'accuracy.json')['cases']['docling'])
    scores={case:score_extra(rows,gold,primary,base_metrics) for case,rows in cases.items()}
    before={(c['document'],c['page'],c['table_id'],c['r'],c['c']):c
            for c in scores['design_control']['cell_scientific_content_match']['details']}
    gains=[];losses=[]
    for c in scores['design_improved']['cell_scientific_content_match']['details']:
        old=before[c['document'],c['page'],c['table_id'],c['r'],c['c']]
        if not old['scientific_match'] and c['scientific_match']:gains.append(c)
        if old['scientific_match'] and not c['scientific_match']:losses.append(c)
    comparison={'gains':gains,'regressions':losses,'policy_sha256':sha(run/'policy.json'),
                'status':'measured development-set comparison, no deployment',
                'selected':'H' if not losses and (gains or scores['design_improved']['figure_detection']['true_positive']>
                                                scores['design_control']['figure_detection']['true_positive']) else 'G'}
    write(run/'design_accuracy.json',{'cases':scores,'scope':gold['metric_scope']})
    write(run/'design_performance.json',{'cases':times,'stages':stages,
          'timing_basis':'shared measured common stages + each separately measured branch including VLM load/exit',
          'experiment_wall_seconds':stages['experiment_wall_seconds']})
    write(run/'candidate_decisions.json',decisions);write(run/'visual_decisions.json',visual)
    write(run/'design_comparison.json',comparison)
    review(run,reference,cases,scores,times,decisions)
    lines=['# 현재 G와 보완 H의 실제 비교','','같은 ABP654 15쪽·원문·정답·평가기에서 비교했습니다. '
           '새 Docling/보정 추출과 새 PP-DocLayoutV3를 공유하고, 두 분기는 각각 별도 PaddleOCR-VL 프로세스와 새 응답을 사용했습니다.',
           '','| 구성 | 공통+분기 총시간 | 처리 단계 합계 | VLM 호출 | 셀 /90 | 구절 /34 | 표 TP/정답 (예측) | 그림 /4 |',
           '|---|---:|---:|---:|---:|---:|---:|---:|']
    for case,label in [('design_control','현재 G: 누락 표만 VL'),('design_improved','보완 H: 그림 보존·누락/불완전 표·후보 검증')]:
        a,t=scores[case],times[case]
        lines.append(f"| {label} | {t['controller_seconds']:.2f}초 | {t['processing_seconds']:.2f}초 | {t['calls']} | "
                     f"{a['cell_scientific_content_match']['correct']} | {a['number_unit_scientific_sample_recall']['correct']} | "
                     f"{a['table_detection']['true_positive']}/18 ({a['table_detection']['predicted']}) | {a['figure_detection']['true_positive']} |")
    lines+=['',f"기준 G에서 맞았던 평가 셀 중 개선 {len(gains)}개, 손실 {len(losses)}개입니다. 자동 판단상 선택은 {comparison['selected']}입니다. 이 선택은 15쪽 개발 표본에 한정됩니다.",
        '', '검증 구현의 수식 표기 처리 오류를 수정한 뒤 같은 모델·선별 규칙·임계값으로 최종 비교를 새로 실행했습니다. 예비 v1 결과는 보관했으며 모델 응답 재시도 중 좋은 것을 골라 채택한 실행이 아닙니다. 헤더와 수치 묶음 검사는 기존 comparable_text 수식 정규화를 사용합니다.',
        '', '## 실제 사용한 모델·도구','',
        '- 기본 추출: Heron + native 문자/RapidOCR·PP-OCRv6 small + TableFormer accurate + 기존 ra_prep 추가 OCR·문자·표·문서 보정.',
        '- 영역 대조: 두 분기 모두 같은 새 PP-DocLayoutV3 15쪽 결과.',
        '- 표 전사: 두 분기 모두 PaddleOCR-VL-1.6-0.9B table, 300dpi crop, 동일 출력 제한 4096, temperature=0.',
        '- H 추가 도구: 원시 picture/독립 chart 근거의 충돌 처리, OpenCV 닫힌 테두리 검출, 반복 수치 묶음 규칙, 후보 숫자/라벨 보존 및 구조 검사.',
        '- Qwen·그림 의미 생성·곡선 수치 생성 없음. native 표 전체 덮어쓰기 없음.',
        '', '## H의 후보 채택과 거절','']
    for d in decisions['design_improved']:
        gate=d['gate'];page=d['page']
        lines.append(f"- {d['document']} 발췌 {page}쪽 / {d['action']} / {'채택' if gate['accepted'] else '거절'}: "
                     f"{', '.join(gate.get('issues',[])) or '구조·기존 문자 보존 검사 통과; 의미 미검증'}")
    lines+=['','## 그림·표 유형 처리','',
            f"독립 chart 근거에 의해 새 그림을 보존한 영역은 {sum(v['restored'] for v in visual)}개이며, 표로 승격된 원래 후보는 {sum(len(v['suppressed_tables']) for v in visual)}개를 별도 보관했습니다.",
            '그림 원본·OCR 라벨·캡션 후보는 유지하며 가짜 표를 최종 표 목록에서 분리했습니다. 원래 표 후보는 visual_decisions.json 및 출력의 suppressed_table_candidates에 남아 있습니다.',
            '', '## 공정성·시간과 한계','',
            '선별 규칙·대상·crop 해시는 정답을 읽고 점수를 계산하기 전에 동결했습니다. 이미 본 개발 자료에서 설계한 정책이며 독립 테스트가 아닙니다. 페이지·제품·기관별 정답 값으로 라우팅하지 않았고 재시도로 응답을 고르지 않았습니다.',
            '두 분기는 기본 추출·영역 검출을 공유합니다. 표의 총시간은 공유 단계 실측과 각 분기의 선별·crop·VLM 시작/추론/종료·검증·저장을 합친 값입니다. 두 번의 독립 PDF 전체 시작 시간을 각각 측정한 값이나 반복 평균이 아닙니다. 실행 순서는 G→H입니다.',
            f"실험 전체 벽시계 시간 {stages['experiment_wall_seconds']:.2f}초. 점수 계산과 HTML 생성은 전처리 시간 밖입니다.",
            '숫자 집합 보존과 라벨 단어 보존은 의미 정확도의 보장이 아닙니다. 같은 숫자의 치료군 위치 오류, 체크박스 판단, 본문·표 중복, 연속 페이지 관계는 원문 대조가 필요합니다. 그림 재현도 그림 의미 설명 정확도가 아닙니다.',
            '', '## 확인 파일','',
            '- [기존 7개와 새 2개 결과 원문 대조](review_design.html)',
            '- [정확도](design_accuracy.json) · [실측 시간](design_performance.json)',
            '- [동결 선택 규칙](policy.json) · [후보 채택/거절](candidate_decisions.json)',
            '- [그림 유형 변환](visual_decisions.json) · [비교 판정](design_comparison.json)',
            '- [입력·기존 결과 불변 검증](design_verification.json)','']
    (run/'DESIGN_COMPARISON_REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    return comparison


def build(baseline,reference,run,config_path):
    baseline,reference,run=map(lambda p:Path(p).resolve(),(baseline,reference,run))
    protected=[p for d in (baseline,reference) for p in d.rglob('*') if p.is_file()]
    hashes={str(p):sha(p) for p in protected}
    run.mkdir(parents=True,exist_ok=False);(run/'logs').mkdir()
    config=activate(config_path,run.parent)
    jobs=read(baseline/'inputs.json')['native']
    write(run/'inputs.json',{'native':jobs});write(run/'settings.json',config);write(run/'policy.json',POLICY)
    for source in (Path(__file__),ROOT/'compare_study/design_policy.py'):
        dest=run/'code_snapshot'/source.name;dest.parent.mkdir(exist_ok=True);shutil.copy2(source,dest)
    beginning=time.perf_counter();stages={}
    stages['docling_controller_seconds']=launch([config['runtimes']['docling'],'-B','-X','utf8','-m','compare_study.worker',
        'DOC-GPU','native','--run-id',run.name,'--config',config['_path'],'--runs',str(run.parent)],ROOT,run/'logs/docling.log')
    primary=page_rows(run,jobs,'DOC-GPU')
    start=time.perf_counter();inventory_inputs(run,jobs);stages['layout_render_seconds']=time.perf_counter()-start
    stages['detector_controller_seconds']=launch([config['runtimes']['paddle'],'-B','-X','utf8','-m','compare_study.selection_detector',
        str(run/'detector_inputs.json'),str(run/'detector_output.json'),str(run/'settings.json')],ROOT,run/'logs/detector.log')
    inventory=read(run/'detector_output.json');raw_pictures=pictures(run,jobs)
    plans={};frozen={}
    for case,improved in [('design_control',False),('design_improved',True)]:
        branch=run/case;branch.mkdir()
        start=time.perf_counter();rows,calls,events,visual=plan(primary,inventory,raw_pictures,improved)
        selection=time.perf_counter()-start
        start=time.perf_counter();crops(branch,calls);crop_seconds=time.perf_counter()-start
        manifest={'created_utc':datetime.now(timezone.utc).isoformat(),'policy':POLICY,'jobs':calls,'decisions':events,
                  'visual_decisions':visual,'selection_frozen_before_gold_read':True,'policy_sha256':sha(run/'policy.json')}
        write(branch/'selection_manifest.json',manifest);frozen[str(branch/'selection_manifest.json')]=sha(branch/'selection_manifest.json')
        plans[case]=(rows,manifest,visual)
        stages[case]={'selection_seconds':selection,'crop_seconds':crop_seconds}
        print(case,'frozen calls',len(calls),'add',sum(c['action']=='add' for c in calls),
              'replace',sum(c['action']=='replace' for c in calls),'restore',sum(v['restored'] for v in visual),flush=True)
    cases={};times={};decisions={}
    common=stages['docling_controller_seconds']+stages['layout_render_seconds']+stages['detector_controller_seconds']
    doc_record=read(run/'native/DOC-GPU/run.json')
    common_processing=doc_record['processing_seconds']+stages['layout_render_seconds']+inventory['processing_seconds']
    for case in ('design_control','design_improved'):
        rows,manifest,visual=plans[case];branch=run/case;improved=case=='design_improved'
        start=time.perf_counter();target=recognize(branch,manifest,config);wall=time.perf_counter()-start
        start=time.perf_counter();merged,chosen=merge(rows,target,improved)
        save_rows(run,'DESIGN-H' if improved else 'DESIGN-G',merged,jobs,inventory)
        merge_seconds=time.perf_counter()-start
        stages[case].update(vlm_process_wall_seconds=wall,merge_write_seconds=merge_seconds,
                           vlm={k:v for k,v in target.items() if k!='results'})
        own=stages[case]
        times[case]={'controller_seconds':common+own['selection_seconds']+own['crop_seconds']+wall+merge_seconds,
                     'total_seconds':common+own['selection_seconds']+own['crop_seconds']+wall+merge_seconds,
                     'processing_seconds':common_processing+own['selection_seconds']+own['crop_seconds']+
                                          target['processing_seconds']+merge_seconds,
                     'calls':target['calls'],'accepted_calls':sum(d['gate']['accepted'] for d in chosen),
                     'timing_basis':'shared common measured stages + independently measured branch'}
        cases[case]=merged;decisions[case]=chosen
    stages['experiment_wall_seconds']=time.perf_counter()-beginning
    for job in jobs:
        dest=run/'originals'/(job['document']['key']+'.pdf');dest.parent.mkdir(exist_ok=True);shutil.copy2(job['original_path'],dest)
    comparison=finish(run,baseline,reference,jobs,primary,cases,times,stages,decisions,plans['design_improved'][2])
    changed=[p for p,expected in hashes.items() if sha(p)!=expected]
    assert not changed,changed
    assert all(sha(p)==expected for p,expected in frozen.items())
    write(run/'design_verification.json',{'status':'passed','protected_files':len(hashes),'changed_files':changed,
        'frozen_manifests':frozen,'fresh_docling_pages':len(primary),'fresh_layout_pages':len(inventory['pages']),
        'fresh_vlm_calls':sum(t['calls'] for t in times.values()),'gold_used_for_routing':False,
        'production_service_config_index_changed':False})
    print(json.dumps({'selected':comparison['selected'],'gains':len(comparison['gains']),
                      'regressions':len(comparison['regressions']),'times':times},ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('baseline','reference','run','config'):parser.add_argument('--'+name,required=True)
    args=parser.parse_args();build(args.baseline,args.reference,args.run,args.config)
