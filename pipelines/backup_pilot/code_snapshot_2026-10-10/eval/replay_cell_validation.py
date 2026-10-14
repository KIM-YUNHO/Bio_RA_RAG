"""Replay prior proposed items: recover formatting, preserve wrong-population rejection."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rag.preprocessed_evidence import grounded_answer


def main():
    root=Path('data/runs/rag_updates_2026-10-09');report=[]
    for key in ['D06','Q008']:
        raw=json.loads((root/f'stage1_scope/responses/{key}.json').read_text(encoding='utf-8'))
        unit=raw['results'][0]
        proposed=unit['items']+unit['validation']['rejected_items']
        out=grounded_answer({'status':'answered','items':proposed,'answer_ko':''},unit['evidence'])
        row={'id':key,'before_items':len(unit['items']),'after_items':len(out['items']),
             'answer':out['answer_ko'],'items':out['items'],'rejected':out['validation']['rejected_items'],
             'control':'identical previously generated values/quotes and evidence; no new model call'}
        if key=='D06':
            recovered=[i for i in out['items'] if i.get('value_validation',{}).get('method')=='within_single_html_cell']
            assert len(recovered)==3
            assert all(len(i['value_validation']['quote_relative_cells'])==1 for i in recovered)
        else:assert not out['items']
        report.append(row)
    out=root/'stage4_cells';out.mkdir(exist_ok=True)
    (out/'validation_replay.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps([{k:v for k,v in r.items() if k not in ('items','rejected')} for r in report],ensure_ascii=False,indent=2))


if __name__=='__main__':main()
