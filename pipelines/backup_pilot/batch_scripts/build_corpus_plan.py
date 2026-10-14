import json,sys
from pathlib import Path
from collections import Counter,defaultdict
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from preprocess.inventory import write_json
from preprocess.ingest import sha256
root=Path(__file__).resolve().parents[1]
inventory=json.loads((root/'data/runs/corpus_update_2026-10-09/inventory.json').read_text(encoding='utf-8'))
eligible=[r for r in inventory if r['source'] in ('EMA','FDA','Health Canada') and not r['protected']]
by_hash=defaultdict(list)
for r in eligible:by_hash[r['version_id']].append(r)
priorities={'ABP654':0,'CTP43':1,'BMAB1200':2,'BAT2206':3,'SB17':4}
plan=[]
for version,aliases in by_hash.items():
    row=aliases[0];plan.append({**row,'aliases':[r['document_id'] for r in aliases]})
plan.sort(key=lambda r:(0 if r['family'] in ('assessment_report','clinical_review','decision_summary') else 1,
    priorities[r['product_id']],r['source']!='EMA',r.get('page_count') or 0,r['document_id']))
write_json(root/'data/runs/corpus_update_2026-10-09/corpus_plan.json',{'scope':['EMA','FDA','Health Canada'],
    'catalog_document_rows':sum(r['source'] in ('EMA','FDA','Health Canada') for r in inventory),
    'eligible_document_rows':len(eligible),'unique_source_documents':len(plan),'documents':plan,
    'protected_exclusions':[r for r in inventory if r['source'] in ('EMA','FDA','Health Canada') and r['protected']],
    'deduplication':'Exact source SHA256; catalog aliases remain in plan. No source files removed.'})
print(json.dumps({'eligible_rows':len(eligible),'unique_documents':len(plan),'kind':dict(Counter(r['kind'] for r in plan)),
    'pages':sum(r.get('page_count') or 0 for r in plan),'by_product':dict(Counter(r['product_id'] for r in plan))}))
