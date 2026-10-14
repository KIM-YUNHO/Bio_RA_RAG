import time,re,fitz
from collections import defaultdict
from ..common import iou,compact

class Handoff:
    def start(self,mode):return {'model_load_seconds':0,'layout_reused_from':'PVL','device':'gpu:0 + CPU native tables'}
    def close(self):pass
    def document(self,path,doc,page_map,work,mode,pvl):
        rows=[];edges=defaultdict(set)
        with fitz.open(path) as pdf:
            for i,p in enumerate(pdf):
                lines=[x.strip() for x in p.get_text(sort=True).splitlines() if x.strip()]
                for x in lines[:4]+lines[-4:]:edges[re.sub(r'\d+','#',compact(x))].add(i)
            repeated={x for x,ps in edges.items() if len(ps)>=max(2,.3*len(pdf))}
            for i,page in enumerate(pdf):
                t=time.perf_counter();raw=pvl[i]
                if not page.get_text().strip():
                    row={**raw,'flags':{**raw.get('flags',{}),'handoff':'image PVL result reused'},'seconds':raw['seconds']}
                else:
                    tables=[];boxes=[];layout=raw.get('layout_tables',[])
                    for table in page.find_tables(strategy='lines').tables:
                        if any(iou(table.bbox,b['bbox'])>=.7 for b in layout):
                            tables.append([[c or '' for c in r] for r in table.extract()]);boxes.append(list(table.bbox))
                    for b in layout:
                        roi=fitz.Rect(b['bbox'])
                        bitmap=any((roi & fitz.Rect(info['bbox'])).get_area()>=.5*roi.get_area() for info in page.get_image_info())
                        if (bitmap or len(page.get_text(clip=roi).strip())<20) and not any(iou(b['bbox'],box)>=.7 for box in boxes):
                            tables.append(__import__('compare_study.bench.common',fromlist=['parse_table']).parse_table(b['content']));boxes.append(b['bbox'])
                    text=[]
                    for block in page.get_text('dict')['blocks']:
                        for line in block.get('lines',[]):
                            x=''.join(s['text'] for s in line['spans']).strip();bb=line['bbox'];cx=(bb[0]+bb[2])/2;cy=(bb[1]+bb[3])/2
                            if not x or any(b[0]<=cx<=b[2] and b[1]<=cy<=b[3] for b in boxes):continue
                            if re.sub(r'\d+','#',compact(x)) in repeated or (cy>page.rect.height*.9 and re.fullmatch(r'\d+',x)):continue
                            text.append(x)
                    overhead=time.perf_counter()-t
                    # PVL's reused inference is an upper bound on cold layout+bitmap recognition cost.
                    row={'document':doc['key'],'page':page_map[i],'text':text,'tables':tables,'table_bboxes':boxes,
                        'figures':raw.get('figures',[]),'figure_counts':raw.get('figure_counts',{}),
                        'embedded_image_objects':raw.get('embedded_image_objects',len(page.get_images(full=True))),
                        'seconds':overhead+raw['seconds'],'flags':{'handoff_native_seconds':overhead,'pvl_shared_seconds_upper_bound':raw['seconds'],
                        'timing_note':'shared PVL inference charged in full; conservative bound, not independent cold-layout timing'}}
                rows.append(row)
        return rows
