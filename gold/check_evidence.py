# 정답셋 근거 자동 대조: TEXT 근거 행의 인용문·숫자가 인용 페이지 텍스트 층에 있는지 확인
# 실행: python gold/check_evidence.py  (biosimilar_collection 폴더 기준)
import json, re, sys
sys.path.insert(0, '.')
from gold_data import ROWS, DOCS, DOC_SHA256
import os
BASE = os.environ.get('PARSED_DIR', os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'parsed')) + os.sep
pages = {}
for sid, (doc, fn, tot) in DOCS.items():
    rows = [json.loads(line) for line in open(BASE + fn, encoding='utf-8')]
    # 정답셋은 라벨링 당시 원문 버전에 고정: 다른 버전의 파싱 결과와 대조하지 않는다
    other = {d['version_id'] for d in rows} - {DOC_SHA256[sid]}
    if other: sys.exit(f'{sid} ({doc}): 파싱 결과의 원문 해시가 정답셋 기준과 다름 {sorted(other)}')
    pages[sid] = {d['pdf_page']: re.sub(r'\s+', ' ', d['text']) for d in rows}
def norm(s): return re.sub(r'\s+', ' ', s).strip()
def check(r):
    if r['status'] not in ('REPORTED', 'PENDING'): return 'N/A'
    if r['modality'] == 'IMAGE': return 'IMAGE(시각 확인)'
    t = pages[r['source_id']][r['pdf_page']]
    probs = []
    if r['quote'] and norm(r['quote']) not in t: probs.append('quote')
    if r['field_id'].startswith(('pk_gmr', 'n_', 'ada_inc', 'nab_inc')) and r['value_text']:
        for tok in re.findall(r'\d+\.\d+|\d+', r['value_text']):
            if tok not in t: probs.append('num:' + tok)
    return 'PASS' if not probs else 'FAIL ' + ','.join(probs)
res = {}
for i, r in enumerate(ROWS):
    c = check(r); r['auto_check'] = c
    res.setdefault(c.split()[0], 0); res[c.split()[0]] += 1
    if c.startswith('FAIL'): print(i, r['study_key'], r['field_id'], r['arm'], r['comparison'], r['pdf_page'], c, '|', (r['quote'] or '')[:80])
print(res, 'total rows', len(ROWS))
