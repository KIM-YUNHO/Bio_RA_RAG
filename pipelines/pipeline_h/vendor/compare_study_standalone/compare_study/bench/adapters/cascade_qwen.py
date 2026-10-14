"""Qwen enrichment of the identical GPU cascade extraction, with auditable routing."""
import time,copy,json
from collections import Counter
from PIL import Image
from ..common import read,write,parse_table,data
from compare_study.settings import runs_root

class CascadeQwen:
    def start(self,mode):
        from compare_study.settings import extraction_config
        from preprocess.qwen_adapter import QwenAdapter
        class GPUQwen(QwenAdapter):
            def request(self,path,body=None):
                if path in ('/api/chat','/api/generate') and body is not None and body.get('keep_alive')!=0:
                    body=copy.deepcopy(body);body.setdefault('options',{})['num_gpu']=999
                return super().request(path,body)
            def residency(self):
                state=self.request('/api/ps')
                m=next((m for m in state['models'] if m['name']==self.settings.model),None)
                if not m or m['size_vram']<m['size']:raise RuntimeError(f'Qwen not fully GPU resident: {state}')
                return state
            def generate(self,*args,**kwargs):
                reply=super().generate(*args,**kwargs);reply['benchmark_gpu_residency']=self.residency();return reply
        self.config=extraction_config();self.qwen=GPUQwen(self.config.qwen)
        t=time.perf_counter();fingerprint=self.qwen.fingerprint()
        self.qwen.request('/api/generate',{'model':self.config.qwen.model,'prompt':'Reply OK','think':False,
            'stream':False,'keep_alive':'5m','options':{'num_ctx':self.config.qwen.num_ctx,'num_predict':1}})
        proof=self.qwen.residency()
        return {'model_load_seconds':time.perf_counter()-t,'model':fingerprint,'gpu':proof,
            'routing':data()['rules'].get('qwen_routing','selected_complex'),'cascade_reused_from':'CUR',
            'figure_policy':data()['rules'].get('figure_policy','describe_selected')}
    def close(self):self.qwen.unload()
    def document(self,path,doc,page_map,work,mode,cur_run):
        from preprocess.hybrid import enrich_assets
        from preprocess.ingest import Document
        from preprocess.visual_groups import build_visual_groups
        t=time.perf_counter();src=runs_root()/cur_run/mode/'CUR'
        bundle=read(src/'raw'/doc['key']/'cascade_assets.json')
        assets=[a for a in bundle['assets'] if not a.get('is_logical_group')]
        # CUR already completed region OCR and source structure recovery.
        # Preserve those routes instead of recomputing from the first-pass OCR.
        groups,jobs=build_visual_groups(assets);assets+=jobs
        if data()['rules'].get('qwen_routing')=='all_visuals':
            for a in assets:
                if a['asset_type']=='table':a['route']={'task':'repair_table','next_stage':'qwen','reasons':['benchmark_user_all_visuals']}
                elif a['asset_type'] in ['figure','figure_group'] and not a.get('route',{}).get('parent_asset_id'):
                    a['route']={'task':'describe_figure','next_stage':'qwen','reasons':['benchmark_user_all_visuals']}
        detection_only=data()['rules'].get('figure_policy')=='detection_only'
        if detection_only:
            figures=[]
            for asset in assets:
                if asset['asset_type'] not in ['figure','figure_group']:continue
                prior=copy.deepcopy(asset['route'])
                asset['route']={'task':None,'next_stage':'figure_detection_only',
                    'reasons':['user_excluded_figure_description'],'prior_route':prior}
                figures.append({'asset_id':asset['asset_id'],'page':page_map[asset['pdf_page']-1],
                    'kind':asset['asset_type'],'bbox_pdf':asset.get('bbox_pdf'),
                    'block_label':asset.get('block_label'),'logical_group':bool(asset.get('is_logical_group')),
                    'prior_route':prior,'disposition':'kept as a detected figure region; excluded from body/table scoring'})
            write(work/'figure_detection.json',{'policy':'detection_only','qwen_figure_calls':0,'regions':figures})
        routing=[{'asset_id':a['asset_id'],'page':page_map[a['pdf_page']-1],'kind':a['asset_type'],'route':a['route']} for a in assets if a['asset_type'] in ['table','figure','figure_group']]
        write(work/'routing.json',routing)
        allowed={asset['asset_id'] for asset in assets if asset['route'].get('task')=='repair_table'} if detection_only else None
        stats=enrich_assets(assets,[Document(**bundle['document'])],work,self.config,self.qwen,only_asset_ids=allowed)
        # A failed interpretation is a retained experimental result, not a reason to discard other pages.
        write(work/'qwen_summary.json',{'stats':stats,'tasks':dict(Counter(a['route']['task'] for a in assets if a['route']['task'])),
            'assets':assets,'figure_accuracy':'unverified model interpretation; source gold scored separately'})
        elapsed=time.perf_counter()-t
        out=[]
        for i,n in enumerate(page_map):
            row=read(src/doc['key']/f'{n:03d}.json');row['flags']['qwen']=stats
            from compare_study.figures import from_assets,finalize
            decorations=[f for f in row.get('figures',[]) if f.get('decorative')]
            row['figures']=decorations+from_assets(assets,i+1,self.config.qwen.model)
            # Keep the same body/furniture selection. Replace only accepted table candidates.
            for a in assets:
                if a['pdf_page']!=i+1 or a['asset_type']!='table' or a['selected']['origin']!='qwen_table_candidate':continue
                index=next((j for j,b in enumerate(row['table_bboxes']) if b==a['bbox_pdf']),None)
                if index is None:raise RuntimeError('Qwen table lost source box correspondence')
                row['tables'][index]=parse_table(a['selected']['content'])
            row['seconds']+=elapsed/len(page_map)
            finalize(row,path,i+1,work);out.append(row)
        return out
