"""Finish the repaired extraction with exact-input reuse from the completed v1."""
import argparse,time
from pathlib import Path
from pipelines.pipeline_h.prepare import ROOT,read,write
from pipelines.pipeline_h.run import launch,state

def main(run):
    run=Path(run).resolve();settings=read(ROOT/'pipelines/pipeline_h/config.local.json');runtime=settings['runtimes']['controller']
    try:
        state(run,'source_validation')
        from pipelines.pipeline_h.source_contract import validate
        validate(run)
        if not (run/'candidate/chunks_summary.json').exists():
            launch(run,'chunks',[runtime,'-u','-X','utf8','-m','pipelines.pipeline_h.chunks','--run',str(run)])
        if not (run/'candidate/emb/bge-m3/meta.json').exists():
            launch(run,'embedding',[runtime,'-u','-X','utf8','-m','pipelines.pipeline_h.finish','--run',str(run),'--stage','embed'])
        launch(run,'validate_release',[runtime,'-u','-X','utf8','-m','pipelines.pipeline_h.finish','--run',str(run),'--stage','release'])
        state(run,'finished','complete',report=str(run/'FINAL_REPORT.md'),release=str(run/'release'))
    except BaseException as exc:
        import traceback
        state(run,'error','failed',error=str(exc),traceback=traceback.format_exc());raise

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);a=p.parse_args();main(a.run)
