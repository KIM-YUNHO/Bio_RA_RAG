from pathlib import Path
source=Path(__file__).with_name('build_rag_finalization.py').read_text(encoding='utf-8')
source=source.replace("BASE=ROOT/'data/rag/releases/rag_context_update_2026-10-10_v1'", "BASE=ROOT/'data/rag/releases/rag_finalization_2026-10-10_v1'")
source=source.replace("ROOT/'data/runs/rag_finalization_2026-10-10_v1'", "ROOT/'data/runs/rag_finalization_2026-10-10_v2'")
source=source.replace("ROOT/'data/rag/candidates/rag_finalization_2026-10-10_v1'", "ROOT/'data/rag/candidates/rag_finalization_2026-10-10_v2'")
source=source.replace("RELEASE=ROOT/'data/rag/releases/rag_finalization_2026-10-10_v1'", "RELEASE=ROOT/'data/rag/releases/rag_finalization_2026-10-10_v2'")
source=source.replace(' headings=[]', " previous=read(candidate/'chunks_summary.json')['config'].get('section_rules_version','sections-4')\n headings=[]")
source=source.replace("if h and h['kind']=='study_boundary':", "if h and h!=ds.identify(line.strip(),products[(unit['document_id'],unit['version_id'])],version=previous):")
exec(compile(source,__file__,'exec'))
