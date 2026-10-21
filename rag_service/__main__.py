import argparse
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import re
from threading import Lock
import time
from uuid import uuid4

from preprocess.hybrid_config import load_config
from rag.pipeline import RAGPipeline
from rag_service.reviews import ReviewStore
from rag_service import ingestion_api
from rag_service.workspaces import WorkspaceStore


class Jobs:
    def __init__(self,pipeline,archive_dir=None,review_dir=None,conversation_dir=None,workspace_store=None,ingestion_service=None,work_drafts=None,issue_guide_dir=None):
        self.pipeline=pipeline;self.lock=Lock();self.jobs={};self.busy=False
        self.archive_dir=Path(archive_dir).resolve() if archive_dir is not None else None
        self.archive_load_errors=[]
        self.reviews=ReviewStore(review_dir or (self.archive_dir/'reviews' if self.archive_dir else None))
        self.workspaces = workspace_store or WorkspaceStore(self.archive_dir.parent/'ra_workspaces' if self.archive_dir else None)
        self.ingestion = ingestion_service or ingestion_api.IngestionAPI()
        from rag_service.work_drafts import WorkDraftService
        self.work_drafts = work_drafts or WorkDraftService(self.workspaces, self.archive_dir.parent/'ra_work_drafts' if self.archive_dir else None,
            model=getattr(getattr(pipeline,'chat_client',None),'model','gpt-5.6-luna'))
        from rag_service.issue_guide import IssueGuideService
        self.issue_guides = IssueGuideService(self, issue_guide_dir or (self.archive_dir.parent/'ra_issue_guides' if self.archive_dir else None),
            model=getattr(getattr(pipeline,'chat_client',None),'model','gpt-5.6-luna'))
        if self.archive_dir is not None:
            self.archive_dir.mkdir(parents=True,exist_ok=True)
            for path in sorted(self.archive_dir.glob('*.json'),key=lambda p:p.stat().st_mtime)[-20:]:
                try:
                    job=json.loads(path.read_text(encoding='utf-8'))
                    if (not isinstance(job,dict) or job.get('id')!=path.stem or not re.fullmatch(r'[0-9a-f]{32}',path.stem)
                            or job.get('state') not in ('completed','failed')
                            or (job['state']=='completed' and not isinstance(job.get('report'),dict))):
                        raise ValueError('Invalid archived job')
                    self.jobs[job['id']]=job
                except (ValueError,OSError) as exc:
                    self.archive_load_errors.append({'file':path.name,'error':str(exc)})
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='rag-query')
        self._inventory=None;self._pages=None
        from rag_service.conversations import ConversationStore
        self.conversations=ConversationStore(conversation_dir)
        import inspect
        self.follow_up=('context' in inspect.signature(pipeline.ask).parameters) if hasattr(pipeline,'ask') else False

    def follow_up_context(self,conversation):
        """The last completed turn of a conversation, reduced to what a follow-up may use."""
        from rag.followup import context_from_report
        for turn in reversed(conversation['turns']):
            try:job=self.get(turn['job_id'])
            except KeyError:continue
            if job.get('state')=='completed' and isinstance(job.get('report'),dict):
                return {**context_from_report(job['report']),'job_id':job['id']}
        return None

    def inventory(self):
        if self._inventory is None:
            from rag_service.inventory import build
            self._inventory=build(self.pipeline)
        return self._inventory

    @property
    def pages(self):
        if self._pages is None:
            from rag_service.pages import EvidencePages
            self._pages=EvidencePages(self.pipeline, self.workspaces.document_path)
        return self._pages

    def persist(self,job):
        if self.archive_dir is None:return
        path=self.archive_dir/(job['id']+'.json')
        temp=self.archive_dir/(job['id']+'.tmp')
        created=False
        try:
            with temp.open('x',encoding='utf-8') as stream:
                created=True
                json.dump(job,stream,ensure_ascii=False,allow_nan=False)
                stream.flush();os.fsync(stream.fileno())
            os.replace(temp,path)
        except (OSError,ValueError,TypeError) as exc:
            job['archive_error']=str(exc)
            if created:
                try:temp.unlink(missing_ok=True)
                except OSError:pass

    def submit(self,payload):
        if not isinstance(payload,dict) or set(payload)-{'question','products','jurisdictions','documents','include_nondefault','conversation_id'}:
            raise ValueError('Invalid question request')
        self.pipeline.validate_request(payload.get('question'),payload.get('products'),payload.get('jurisdictions'),payload.get('documents'),payload.get('include_nondefault',False))
        conversation_id=payload.get('conversation_id')
        context=None
        if conversation_id is not None:
            conversation=self.conversations.get(conversation_id)
            context=self.follow_up_context(conversation) if self.follow_up else None
        with self.lock:
            if self.busy:raise BlockingIOError('다른 질문을 처리 중입니다. 완료 후 다시 시도하세요.')
            self.busy=True
            identity=uuid4().hex
            try:
                if conversation_id is None:conversation_id=self.conversations.create()
                self.conversations.append(conversation_id,{'job_id':identity,'user_text':payload['question'],'state':'running',
                    'context_job_id':context['job_id'] if context else None})
            except Exception:
                self.busy=False;raise
            self.jobs[identity]={'id':identity,'state':'running','stage':'queued','message':'질문 처리를 준비하고 있습니다.',
                'created_at':time.time(),'question':payload['question'],'conversation_id':conversation_id}
            for old in list(self.jobs)[:-20]:
                if self.jobs[old]['state']!='running':del self.jobs[old]
        self.executor.submit(self.run,identity,{**payload,'conversation_id':conversation_id},context)
        return {'id':identity,'state':'running','conversation_id':conversation_id,'follow_up':context is not None}

    def run(self,identity,payload,context=None):
        def progress(**event):
            with self.lock:self.jobs[identity].update(event)
        state='failed';standalone=None
        try:
            extra={'context':context} if context is not None else {}
            report=self.pipeline.ask(payload['question'],payload.get('products'),payload.get('jurisdictions'),payload.get('documents'),progress,include_nondefault=payload.get('include_nondefault',False),**extra)
            state='completed';standalone=(report.get('conversation') or {}).get('standalone_question')
            with self.lock:job={**self.jobs[identity],'state':'completed','stage':'completed','message':'답변 처리가 끝났습니다.','report':report}
            self.persist(job)
            with self.lock:self.jobs[identity]=job
        except Exception as exc:
            with self.lock:job={**self.jobs[identity],'state':'failed','stage':'failed','message':'질문 처리에 실패했습니다.','error':f'{type(exc).__name__}: {exc}','diagnostics':getattr(self.pipeline,'last_failure',None)}
            self.persist(job)
            with self.lock:self.jobs[identity]=job
        finally:
            try:self.conversations.update_turn(payload['conversation_id'],identity,{'state':state,'standalone_question':standalone})
            except (KeyError,ValueError,OSError):pass
            with self.lock:self.busy=False

    def get(self,identity):
        with self.lock:
            if identity in self.jobs:return deepcopy(self.jobs[identity])
        if self.archive_dir is not None and re.fullmatch(r'[0-9a-f]{32}',identity):
            path=self.archive_dir/(identity+'.json')
            if path.is_file():
                try:job=json.loads(path.read_text(encoding='utf-8'))
                except (OSError,ValueError) as exc:raise KeyError(identity) from exc
                if (isinstance(job,dict) and job.get('id')==identity and job.get('state') in ('completed','failed')
                        and (job['state']=='failed' or isinstance(job.get('report'),dict))):return job
        raise KeyError(identity)

    def list_records(self,limit=20,offset=0,query=''):
        if not 1<=limit<=50 or not 0<=offset<=10000 or len(query)>200:
            raise ValueError('목록 범위를 확인하세요.')
        with self.lock: records=dict(self.jobs); busy=self.busy
        if self.archive_dir is not None:
            for path in self.archive_dir.glob('*.json'):
                if path.stem in records: continue
                try: records[path.stem]=self.get(path.stem)
                except KeyError: pass
        rows=[]
        for job in records.values():
            if query.casefold() not in str(job.get('question','')).casefold(): continue
            report=job.get('report') or {}
            created=job.get('created_at',0)
            if not isinstance(created,(int,float)) or isinstance(created,bool): created=0
            result_rows=report.get('results',[])
            first=result_rows[0] if isinstance(result_rows,list) and result_rows and isinstance(result_rows[0],dict) else {}
            rows.append({'id':job['id'],'question':str(job.get('question','')),'created_at':created,
                'state':job['state'],'status':first.get('status'),
                'pipeline_version':report.get('pipeline_version')})
        rows.sort(key=lambda r:r['created_at'],reverse=True)
        return {'jobs':rows[offset:offset+limit],'total':len(rows),'limit':limit,'offset':offset,'busy':busy}

    def close(self):self.issue_guides.close();self.executor.shutdown(wait=True);self.work_drafts.close()


def main():
    parser=argparse.ArgumentParser(description='로컬 RA RAG 메인 서비스')
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--candidate',type=Path)
    parser.add_argument('--config',type=Path,default=Path('configs/preprocess.local.json'))
    parser.add_argument('--review-url',default='http://127.0.0.1:8766')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--retrieval-pages',type=int,default=8,choices=range(1,11),
        help='Hits per product x agency slot (2026-10-14 benchmark: 8 + fixed topic queries)')
    parser.add_argument('--mode',choices=('legacy','ra'),default='legacy')
    parser.add_argument('--llm-model',choices=('gpt-4o-mini','gpt-5.6-luna'),default='gpt-4o-mini',
        help='Framework model for RA planning, synthesis and internal review')
    parser.add_argument('--guidelines',type=Path,help='Frozen native guideline snapshot for RA mode')
    parser.add_argument('--research-rounds',type=int,choices=(0,1),default=1,
        help='Bounded local missing-facet search rounds; no extra model calls')
    parser.add_argument('--approval-timeline',action='store_true',
        help='Experimental: answer approval-date questions from code-read date fields (off by default)')
    parser.add_argument('--archive-dir',type=Path,help='Durable completed and failed question records')
    parser.add_argument('--review-dir',type=Path,help='Separate append-only review history')
    parser.add_argument('--conversation-dir',type=Path,help='Conversation turn records (default data/runs/ra_conversations in RA mode)')
    parser.add_argument('--workspace-dir',type=Path,help='Use local notebook files instead of PostgreSQL (offline fixture/standalone mode)')
    parser.add_argument('--work-draft-dir',type=Path,help='Internal work draft records (default: ra_work_drafts next to the report archive)')
    parser.add_argument('--issue-guide-dir',type=Path,help='Issue guide records (default: ra_issue_guides next to the report archive)')
    parser.add_argument('--warmup',action='store_true',help='Warm local retrieval before accepting questions; no provider calls')
    parser.add_argument('--context-tokens',type=int,default=65536,
        help='RA reading budget per model call (prompt + output); a project limit, not the model limit')
    args=parser.parse_args()
    if not 1<=args.port<=65535:parser.error('Invalid port')
    if args.guidelines and args.mode!='ra':parser.error('--guidelines requires --mode ra')
    if args.llm_model!='gpt-4o-mini' and args.mode!='ra':parser.error('--llm-model requires --mode ra')
    # The root .env decides the database and the search backend (BIOSIM_SEARCH_BACKEND) before the index is loaded.
    from rag.llm import load_env
    load_env()
    if args.mode=='ra':
        from rag.ra_pipeline import RAPipeline
        from rag.openai_chat import OpenAIPipelineChat
        pipeline=RAPipeline(args.run,args.candidate,load_config(args.config),args.review_url,
            retrieval_pages=args.retrieval_pages,guideline_snapshot=args.guidelines,
            research_rounds=args.research_rounds,approval_timeline=args.approval_timeline,
            chat_client=OpenAIPipelineChat(model=args.llm_model,context_tokens=args.context_tokens))
    else:
        pipeline=RAGPipeline(args.run,args.candidate,load_config(args.config),args.review_url,retrieval_pages=args.retrieval_pages)
    archive_dir=args.archive_dir or (Path('data/runs/ra_service_reports') if args.mode=='ra' else None)
    if args.warmup and args.mode=='ra':print(json.dumps({'retrieval_warmup':pipeline.warmup()},ensure_ascii=False),flush=True)
    from rag_service.workspaces import PostgresWorkspaceStore
    workspace_store=WorkspaceStore(args.workspace_dir) if args.workspace_dir else PostgresWorkspaceStore()
    from rag_service.work_drafts import WorkDraftService
    # Internal work drafts use the service's selected model; legacy mode has no framework model, so they default to Luna.
    draft_dir=args.work_draft_dir or (Path(archive_dir).parent/'ra_work_drafts' if archive_dir else None)
    work_drafts=WorkDraftService(workspace_store,draft_dir,model=args.llm_model if args.mode=='ra' else 'gpt-5.6-luna')
    jobs=Jobs(pipeline,archive_dir,args.review_dir or (Path('data/runs/ra_review_records') if args.mode=='ra' else None),
        args.conversation_dir or (Path('data/runs/ra_conversations') if args.mode=='ra' else None), workspace_store, work_drafts=work_drafts,
        issue_guide_dir=args.issue_guide_dir)
    import uvicorn
    from rag_service.app import create_app
    # One worker: every worker would load its own embedding and reranker models. Questions run in Jobs' own thread.
    config=uvicorn.Config(create_app(jobs,args.port),host='127.0.0.1',port=args.port,workers=1,access_log=False,log_level='warning',lifespan='off')
    print(f'RA RAG 메인 서비스: http://127.0.0.1:{args.port}/',flush=True)
    print(json.dumps(pipeline.summary(),ensure_ascii=False),flush=True)
    try:uvicorn.Server(config).run()
    except KeyboardInterrupt:pass
    finally:jobs.close();pipeline.close()


if __name__=='__main__':main()
