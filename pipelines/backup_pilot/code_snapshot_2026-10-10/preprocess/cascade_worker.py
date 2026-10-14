"""Layout + native text + PP-OCR only. Qwen is a separate selected-region stage."""
import contextlib,json,os,sys,time,traceback
from pathlib import Path


class CascadeParser:
    def __init__(self,settings):
        from .light_ocr import LightOCR
        self.settings=settings
        self.layout=None
        self.ocr=LightOCR(settings)

    def parse(self,image_path,native=None,label_only=False):
        import math
        from PIL import Image
        from .light_ocr import literal
        from .native_text import extract,project
        from .simple_grid import reconstruct
        started=time.perf_counter();image=Image.open(image_path).convert('RGB');rows=[];proofs={};calls=0
        if label_only:
            result=self.ocr.predict(image)
            rows=[{'block_label':'text','block_bbox':[0,0,*image.size],'block_content':literal(result),'block_order':0}]
            proofs['0']={'ocr':result,'route':{'next_stage':'light_ocr_complete','task':None}}
            calls=1
        else:
            if self.layout is None:
                from paddleocr import LayoutDetection
                self.layout=LayoutDetection(model_name='PP-DocLayoutV3',model_dir=self.settings['layout_model_dir'],
                    engine='transformers',device=self.settings['device'])
            detections=list(self.layout.predict(input=str(image_path)))
            if len(detections)!=1:raise ValueError('Expected one layout result')
            boxes=detections[0].json.get('res',detections[0].json)['boxes']
            for b in boxes:
                x0,y0,x1,y1=b['coordinate']
                box=[max(0,math.floor(x0)),max(0,math.floor(y0)),min(image.width,math.ceil(x1)),min(image.height,math.ceil(y1))]
                if box[0]>=box[2] or box[1]>=box[3]:raise ValueError('Invalid layout box')
                label=b['label'];text=extract(native,label,box) if native else None
                proof={'layout_score':b['score'],'engine':'native' if text is not None else 'PP-OCR-mobile'}
                if text is None:
                    crop=image.crop(box);result=self.ocr.predict(crop);calls+=1
                    text=literal(result);proof['ocr']=result
                    if label=='table':
                        grid=reconstruct(crop,result);proof['grid']=grid;text=grid['html']
                rows.append({'block_label':label,'block_bbox':box,'block_content':text,'block_order':len(rows)})
                proofs[str(len(rows)-1)]=proof
        raw={'res':{'width':image.width,'height':image.height,'model_settings':{
            'use_doc_preprocessor':False,'use_layout_detection':not label_only},'parsing_res_list':rows},
            'ra_light_ocr':proofs,'ra_runtime':{'inference_seconds':round(time.perf_counter()-started,4),
            'light_ocr_calls':calls,'vlm_blocks':0,'parser_backend':'cascade'}}
        if native and not label_only:raw=project(raw,native)
        return raw


def main():
    for name in ('HF_HUB_OFFLINE','TRANSFORMERS_OFFLINE','HF_DATASETS_OFFLINE'):os.environ[name]='1'
    os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK']='True'
    from .paddle_worker import forbid_external_connections
    forbid_external_connections()
    settings=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    with contextlib.redirect_stdout(sys.stderr):parser=CascadeParser(settings)
    print(json.dumps({'ready':True}),flush=True)
    for line in sys.stdin:
        try:
            request=json.loads(line)
            with contextlib.redirect_stdout(sys.stderr):
                raw=parser.parse(request['image'],request.get('native'),request.get('task')=='ocr')
            print(json.dumps({'result':raw},ensure_ascii=False,allow_nan=False),flush=True)
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            print(json.dumps({'error':f'{type(exc).__name__}: {exc}'}),flush=True)


if __name__=='__main__':main()
