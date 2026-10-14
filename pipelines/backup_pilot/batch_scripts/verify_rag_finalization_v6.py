from pathlib import Path
source=Path(__file__).with_name('verify_rag_finalization.py').read_text(encoding='utf-8')
source=source.replace('rag_finalization_2026-10-10_v1/verification','rag_finalization_2026-10-10_v2/verification_v6')
exec(compile(source,__file__,'exec'))
