"""Reconstruct only fully ruled rectangular tables from literal OCR tokens."""
from html import escape

VERSION='light-flat-grid-1'


def borderless(result):
    """Accept aligned, fully populated rows only; never split guessed cell text."""
    tokens=sorted(result['tokens'],key=lambda t:(t['bbox'][1],t['bbox'][0]))
    rows=[]
    for t in tokens:
        if t['confidence']<.8:return None
        cy=(t['bbox'][1]+t['bbox'][3])/2
        if rows and abs(cy-rows[-1][0])<min(t['bbox'][3]-t['bbox'][1],rows[-1][2])*.4:
            rows[-1][1].append(t)
        else:rows.append([cy,[t],t['bbox'][3]-t['bbox'][1]])
    if len(rows)<3:return None
    columns=len(rows[0][1])
    if not 2<=columns<=20 or any(len(r[1])!=columns for r in rows):return None
    for r in rows:r[1].sort(key=lambda t:t['bbox'][0])
    for c in range(columns):
        lefts=[r[1][c]['bbox'][0] for r in rows]
        if max(lefts)-min(lefts)>12:return None
        if c+1<columns and max(r[1][c]['bbox'][2] for r in rows)+8>=min(r[1][c+1]['bbox'][0] for r in rows):return None
    cells=[{'row':ri,'column':ci,'text':t['text'],'token_indices':[result['tokens'].index(t)],
        'bbox_input_px':t['bbox']} for ri,r in enumerate(rows) for ci,t in enumerate(r[1])]
    html='<table>'+''.join('<tr>'+''.join('<td>'+escape(c['text'])+'</td>' for c in cells if c['row']==r)+'</tr>' for r in range(len(rows)))+'</table>'
    return {'html':html,'cells':cells,'complexity':{'simple_grid_complete':True,'borderless_unresolved':False},
        'version':VERSION,'geometry':'aligned_populated_rows'}


def reconstruct(image,result):
    import cv2,numpy as np
    gray=cv2.cvtColor(np.array(image.convert('RGB')),cv2.COLOR_RGB2GRAY)
    ink=cv2.threshold(gray,0,255,cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)[1]
    height,width=ink.shape
    horizontal=cv2.morphologyEx(ink,cv2.MORPH_OPEN,np.ones((1,max(20,width//4)),np.uint8))
    vertical=cv2.morphologyEx(ink,cv2.MORPH_OPEN,np.ones((max(20,height//4),1),np.uint8))
    def centers(values):
        indices=np.flatnonzero(values);runs=[]
        for i in indices:
            if runs and i<=runs[-1][-1]+1:runs[-1].append(int(i))
            else:runs.append([int(i)])
        return [int(round(sum(r)/len(r))) for r in runs]
    ys=centers((horizontal>0).sum(axis=1)>width*.65)
    xs=centers((vertical>0).sum(axis=0)>height*.65)
    complexity={'simple_grid_complete':False,'borderless_unresolved':len(xs)<3 or len(ys)<3}
    if len(xs)<3 or len(ys)<3:
        aligned=borderless(result)
        if aligned:return aligned
        # Token quantity or bad recognition alone does not establish a hard
        # table. Require repeated column anchors and inconsistent populated
        # row geometry after ordinary OCR.
        tokens=result['tokens'];rows=[]
        for t in sorted(tokens,key=lambda t:t['bbox'][1]):
            cy=(t['bbox'][1]+t['bbox'][3])/2
            if rows and abs(cy-rows[-1][0])<max(3,(t['bbox'][3]-t['bbox'][1])*.4):rows[-1][1].append(t)
            else:rows.append([cy,[t]])
        anchors=[]
        for t in sorted(tokens,key=lambda t:t['bbox'][0]):
            x=t['bbox'][0]
            if anchors and abs(x-anchors[-1][0])<=12:anchors[-1][1]+=1
            else:anchors.append([x,1])
        repeated=sum(count>=3 for x,count in anchors)
        row_widths={len(r[1]) for r in rows}
        complexity['borderless_unresolved']=(len(rows)>=3 and repeated>=2 and len(row_widths)>1
            and all(t['confidence']>=.8 for t in tokens))
        return {'html':'','cells':[],'complexity':complexity}
    # Missing internal lines / intersections indicate spans or broken geometry.
    full_h=all(np.mean(horizontal[max(0,y-2):y+3,xs[0]:xs[-1]+1].max(axis=0)>0)>.98 for y in ys)
    full_v=all(np.mean(vertical[ys[0]:ys[-1]+1,max(0,x-2):x+3].max(axis=1)>0)>.98 for x in xs)
    if not full_h or not full_v:
        complexity.update(merged_cells=True,irregular_grid=True)
        return {'html':'','cells':[],'complexity':complexity}
    cells=[];assigned=set()
    for r,(top,bottom) in enumerate(zip(ys,ys[1:])):
        for c,(left,right) in enumerate(zip(xs,xs[1:])):
            own=[]
            for i,t in enumerate(result['tokens']):
                x0,y0,x1,y1=t['bbox'];cx,cy=(x0+x1)/2,(y0+y1)/2
                if left<cx<right and top<cy<bottom:
                    if t['confidence']<.8 or x0<left-2 or x1>right+2 or y0<top-2 or y1>bottom+2:
                        complexity['irregular_grid']=True
                        return {'html':'','cells':[],'complexity':complexity}
                    own.append((i,t));assigned.add(i)
            own.sort(key=lambda pair:(pair[1]['bbox'][1],pair[1]['bbox'][0]))
            cells.append({'row':r,'column':c,'text':'\n'.join(t['text'] for _,t in own),
                'token_indices':[i for i,_ in own],'bbox_input_px':[left,top,right,bottom]})
    if len(assigned)!=len(result['tokens']):
        complexity['irregular_grid']=True
        return {'html':'','cells':[],'complexity':complexity}
    html='<table>'+''.join('<tr>'+''.join('<td>'+escape(c['text'])+'</td>'
        for c in cells if c['row']==r)+'</tr>' for r in range(len(ys)-1))+'</table>'
    complexity.update(simple_grid_complete=True,borderless_unresolved=False)
    return {'html':html,'cells':cells,'complexity':complexity,'version':VERSION}
