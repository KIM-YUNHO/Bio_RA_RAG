"""Non-generative PP-OCR detection/recognition; never loads a document VLM."""
import os,time
from pathlib import Path

VERSION='pp-ocr-mobile-1'


class LightOCR:
    def __init__(self, settings):
        for name in ('HF_HUB_OFFLINE','TRANSFORMERS_OFFLINE'):
            os.environ[name]='1'
        os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK']='True'
        from paddleocr import PaddleOCR
        self.pipeline=PaddleOCR(engine='transformers',device=settings['device'],
            text_detection_model_name='PP-OCRv5_mobile_det',
            text_detection_model_dir=settings['light_det_model_dir'],
            text_recognition_model_name='latin_PP-OCRv5_mobile_rec',
            text_recognition_model_dir=settings['light_rec_model_dir'],
            use_doc_orientation_classify=False,use_doc_unwarping=False,
            use_textline_orientation=False,text_recognition_batch_size=16,
            text_rec_score_thresh=0.0)

    def predict(self,image):
        started=time.perf_counter()
        import numpy as np
        rows=list(self.pipeline.predict(input=np.array(image.convert('RGB'))))
        if len(rows)!=1:raise ValueError('Expected exactly one OCR result')
        raw=rows[0].json.get('res',rows[0].json)
        texts,scores,polys=raw['rec_texts'],raw['rec_scores'],raw['rec_polys']
        if not len(texts)==len(scores)==len(polys):raise ValueError('Inconsistent OCR tokens')
        tokens=[]
        for text,score,poly in zip(texts,scores,polys):
            points=[[float(x),float(y)] for x,y in poly]
            tokens.append({'text':text,'confidence':float(score),'polygon':points,
                'bbox':[min(x for x,y in points),min(y for x,y in points),
                        max(x for x,y in points),max(y for x,y in points)]})
        return {'engine':VERSION,'tokens':tokens,'seconds':round(time.perf_counter()-started,4),
            'image_size':list(image.size),'generative_model_calls':0}


def literal(result,min_confidence=.8):
    """Keep low-confidence readings in raw tokens, outside accepted literal text."""
    return '\n'.join(t['text'] for t in result['tokens'] if t['confidence']>=min_confidence)
