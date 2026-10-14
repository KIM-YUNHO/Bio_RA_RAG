import time,fitz,os
from ..common import BENCH,write,sha
from compare_study.settings import configuration
from compare_study.figures import chart_cues

class Docling:
    def __init__(self,device,parser_threads=1): self.device=device;self.parser_threads=parser_threads
    def start(self,mode):
        from ra_prep.extract import Extractor
        from ..gpu import second_reader,providers,torch_devices
        from docling.datamodel.base_models import InputFormat
        t=time.perf_counter()
        # Native rendering fault was reproduced inside the installed parser's
        # PIL.frombuffer(...).copy(). Avoid borrowed image-buffer lifetime;
        # preserve exactly the same RGBA bytes, dimensions and PDF backend.
        from docling_parse.pdf_parser import PageParseResult
        from PIL import Image
        def owned_image(raw_bytes,image_shape):
            height,width,channels=image_shape
            if channels!=4 or len(raw_bytes)!=height*width*4:
                raise ValueError('Docling renderer RGBA buffer shape mismatch')
            return Image.frombytes('RGBA',(width,height),memoryview(raw_bytes).tobytes(),'raw','RGBA',0,1)
        PageParseResult._image_from_bytes=staticmethod(owned_image)
        cuda_options={}
        if os.environ.get('BENCHMARK_DOCLING_CUDNN_SEARCH'):
            cuda_options={'cudnn_conv_algo_search':os.environ['BENCHMARK_DOCLING_CUDNN_SEARCH'],
                'arena_extend_strategy':os.environ.get('BENCHMARK_DOCLING_CUDA_ARENA','kSameAsRequested')}
            if cuda_options['cudnn_conv_algo_search'] not in ('HEURISTIC','EXHAUSTIVE'):raise ValueError('Unknown CUDA strategy')
        second,gpu=second_reader(cuda_options)
        def provider_options(reader):
            actual={name:getattr(reader,name).session.session.get_provider_options()['CUDAExecutionProvider']
                for name in ['text_det','text_cls','text_rec']}
            if cuda_options:
                for options in actual.values():
                    for key,value in cuda_options.items():
                        if options.get(key)!=value:raise RuntimeError(f'CUDA option was not applied: {key} {options}')
            return actual
        gpu['second_reader_cuda_options']=provider_options(second)
        self.extractor=Extractor(num_threads=4,table_mode='accurate',artifacts_path=configuration()['docling']['artifacts_path'],
            repair_ocr=True,picture_tables=True,device=self.device)
        # Keep the original proposal and its evidence, then apply the shared chart guard.
        import copy
        from ra_prep import grid
        from compare_study.figures import chart_cues
        original_pictures=self.extractor._pictures_to_tables
        def audited_pictures(blocks,lines):
            before=copy.deepcopy(blocks)
            original=original_pictures(copy.deepcopy(blocks),lines)
            proposed={b['ref']:b for b in original}
            events=[]
            for b in blocks:
                if b['type']!='picture':continue
                items=grid.lines_in(lines.get(b['page'],[]),b['bbox'])
                text='\n'.join(t for _,t in items)
                candidate=proposed.get(b['ref'],b)
                converted=candidate['type']=='table'
                guard=converted and chart_cues(text)
                history=['picture']+(['table_proposal','chart_guard_kept_picture'] if guard else ['table'] if converted else [])
                b.update(ocr_text=text,kind_history=history)
                if guard:
                    # The original converter must not see this candidate; preserve child OCR labels.
                    b['source']='chart_guard'
                events.append({'ref':b['ref'],'page':b['page'],'bbox':b['bbox'],'kind_history':history,
                    'numeric_grid_proposal':converted,'chart_cues':chart_cues(text),'ocr_text':text})
            final=original_pictures(blocks,lines)
            for b in final:
                if b.get('source')=='chart_guard':b['source']='ocr'
            self.visual_audit={'before_blocks':before,'original_rule_blocks':original,'events':events}
            return final
        self.extractor._pictures_to_tables=audited_pictures
        # Benchmark-only runtime option: keep the same parser and GPU models,
        # serialize native parser workers to diagnose its access violations.
        from docling.datamodel.backend_options import ThreadedDoclingParseBackendOptions
        backend=os.environ.get('BENCHMARK_DOCLING_PDF_BACKEND','docling_parse')
        if backend not in ('docling_parse','pypdfium2'):raise ValueError('Unknown comparison PDF backend')
        original_converter=self.extractor._converter
        def converter_for(route):
            converter=original_converter(route)
            pdf_options=converter.format_to_options[InputFormat.PDF]
            if route is not None:
                settings=configuration()['docling']
                for key in ['det_model_path','cls_model_path','rec_model_path']:
                    setattr(pdf_options.pipeline_options.ocr_options,key,settings[key])
            if backend=='pypdfium2':
                from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
                from docling.datamodel.backend_options import PdfBackendOptions
                pdf_options.backend=PyPdfiumDocumentBackend;pdf_options.backend_options=PdfBackendOptions()
            else:
                pdf_options.backend_options=ThreadedDoclingParseBackendOptions(
                    parser_threads=self.parser_threads,release_native_memory_every_n_pages=0)
            if route is not None and cuda_options:
                options=converter.format_to_options[InputFormat.PDF].pipeline_options.ocr_options
                options.rapidocr_params={**(options.rapidocr_params or {}),**{
                    'EngineConfig.onnxruntime.cuda_ep_cfg.'+k:v for k,v in cuda_options.items()}}
            return converter
        self.extractor._converter=converter_for
        # IMAGE and ARTWORK both use auto, not full: reproduce vendor routing exactly.
        for route in ([None,'auto'] if mode=='native' else ['auto']):
            converter=self.extractor._converter(route)
            converter.initialize_pipeline(InputFormat.PDF)
            pipeline=converter._get_pipeline(InputFormat.PDF)
            gpu['layout']=torch_devices(pipeline.layout_model)
            gpu['table']=torch_devices(pipeline.table_model)
            if route is not None:
                gpu['docling_ocr']=providers(pipeline.ocr_model.reader)
                gpu['docling_ocr_cuda_options']=provider_options(pipeline.ocr_model.reader)
        return {'model_load_seconds':time.perf_counter()-t,'device':self.device,'engine':self.extractor.version(),'gpu':gpu,
            'corpus_trigram_rule_enabled':bool(configuration()['docling'].get('trigram_path')),
            'trigram_sha256':sha(configuration()['docling']['trigram_path']) if configuration()['docling'].get('trigram_path') else None,
            'picture_table_policy':'original numeric-grid proposal followed by shared chart-cue guard; original proposal retained',
            'runtime_compatibility':'managed PDFium resource lock and owned image render; same GPU weights' if backend=='pypdfium2' else 'owned RGBA byte copy; original backend, dimensions and model settings',
            'benchmark_parser_threads':self.parser_threads if backend=='docling_parse' else None,'benchmark_native_memory_release_interval':0 if backend=='docling_parse' else None,
            'benchmark_cuda_options':cuda_options or 'installed defaults','benchmark_pdf_backend':backend}
    def close(self): pass
    def document(self,path,doc,page_map,work,mode):
        from ra_prep.pipeline import process_document
        from ra_prep.select import Selection
        dtype={'EMA-EPAR':'assessment_report','SmPC':'product_information','RMP':'risk_management','procedural':'procedural_steps'}.get(doc['doc_type'],doc['doc_type'])
        sel=Selection(1,sha(path),str(path),doc['agency'],dtype,None,'process')
        t=time.perf_counter();result=process_document(sel,self.extractor);elapsed=time.perf_counter()-t
        write(work/'vendor_document.json',result)
        write(work/'visual_audit.json',getattr(self,'visual_audit',{}))
        with fitz.open(path) as pdf: heights=[p.rect.height for p in pdf]
        out=[]
        for i,n in enumerate(page_map):
            row={'document':doc['key'],'page':n,'text':[],'tables':[],'table_bboxes':[],
                'seconds':elapsed/len(page_map),'flags':{'time_basis':'document_wall_amortized','route':result['routes'][i]['route']}}
            from compare_study.figures import figure,finalize,attach_document_structure
            row['figures']=[]
            for b in result['blocks']:
                if b['page']!=i+1:continue
                if b['type']=='picture':
                    l,bot,r,top=b['bbox'];box=[l,heights[i]-top,r,heights[i]-bot]
                    row['figures'].append(figure(b['ref'],box,'chart' if chart_cues(b.get('ocr_text','')) else 'picture',
                        b.get('ocr_text',''),b.get('caption',''),model='Heron + RapidOCR (no generative VLM)',
                        status='unsupported_interpretation',history=b.get('kind_history')))
                if b.get('furniture'):continue
                if b['type']=='table':
                    nr=max([b.get('n_rows',0)]+[c['r']+max(1,c.get('rs',1)) for c in b['cells']]);nc=max([b.get('n_cols',0)]+[c['c']+max(1,c.get('cs',1)) for c in b['cells']])
                    rows=[['']*nc for _ in range(nr)]
                    for c in b['cells']: rows[c['r']][c['c']]=c['text'] or ''
                    row['tables'].append(rows);l,bot,r,top=b['bbox'];row['table_bboxes'].append([l,heights[i]-top,r,heights[i]-bot])
                elif b['type']=='text' and b.get('text'):row['text'].append(b['text'])
            row['flags'].update(ra_timing=result['timing'],qa=result['qa'],agency=doc['agency'],doc_type=dtype)
            attach_document_structure(row,result['blocks'],i+1,heights[i])
            finalize(row,path,i+1,work);out.append(row)
        print(f"DOC-{self.device} {doc['key']} {len(out)} pages {elapsed:.1f}s",flush=True)
        return out
