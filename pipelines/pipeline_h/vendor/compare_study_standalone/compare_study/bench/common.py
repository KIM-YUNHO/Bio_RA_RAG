"""Standalone extraction subset; original algorithms retained. See SOURCE_MAP.json."""

from __future__ import annotations

import hashlib, json, re, unicodedata

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]



BENCH = Path(__file__).resolve().parent



def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()



def write(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    tmp.replace(path)



def read(path): return json.loads(Path(path).read_text(encoding='utf-8'))



def compact(s): return re.sub(r'\s+', '', unicodedata.normalize('NFKC', s or '')).lower()



def data(): return read(BENCH/'data.json')



def iou(a,b):
    x=max(0,min(a[2],b[2])-max(a[0],b[0])); y=max(0,min(a[3],b[3])-max(a[1],b[1]))
    area=x*y; return area/((a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-area) if area else 0



def rows_from_html(s):
    from html.parser import HTMLParser
    class TableParser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True);self.depth=0;self.done=False;self.rows=[];self.row=None;self.cell=None
        def handle_starttag(self,tag,attrs):
            if tag=='table' and not self.done:self.depth+=1
            if self.depth!=1:return
            if tag=='tr':self.row=[];self.rows.append(self.row)
            elif tag in {'td','th'} and self.row is not None:
                self.cell=[dict(attrs),[]];self.row.append(self.cell)
            elif tag=='br' and self.cell:self.cell[1].append(' ')
        def handle_endtag(self,tag):
            if tag=='table' and self.depth:
                self.depth-=1
                if not self.depth:self.done=True
            elif tag in {'td','th'}:self.cell=None
            elif tag=='tr':self.cell=None;self.row=None
        def handle_data(self,text):
            if self.depth==1 and self.cell:self.cell[1].append(text)
    parser=TableParser();parser.feed(s)
    occupied={}; result=[]
    for r,tr in enumerate(parser.rows):
        row=[]; c=0
        for attrs,parts in tr:
            while (r,c) in occupied:
                while len(row)<=c: row.append('')
                c+=1
            cs=int(attrs.get('colspan',1)); rs=int(attrs.get('rowspan',1))
            if cs<1 or rs<1 or cs>100 or rs>1000:raise ValueError('Invalid HTML cell span')
            while len(row)<c+cs: row.append('')
            row[c]=''.join(parts).strip()
            for rr in range(r,r+rs):
                for cc in range(c,c+cs): occupied[rr,cc]=True
            c+=cs
        end=max([cc+1 for rr,cc in occupied if rr==r]+[len(row)])
        row+=['']*(end-len(row)); result.append(row)
    width=max(map(len,result),default=0)
    return [r+['']*(width-len(r)) for r in result]



def rows_from_markdown(s):
    rows=[]
    for line in s.splitlines():
        if line.strip().startswith('|') and line.strip().endswith('|'):
            row=[x.strip() for x in re.split(r'(?<!\\)\|',line.strip())[1:-1]]
            if not all(re.fullmatch(r':?-+:?',x.replace(' ','')) for x in row): rows.append(row)
    return rows



def parse_table(s): return rows_from_html(s) if '<table' in s.lower() else rows_from_markdown(s)



def validate_output(page):
    assert isinstance(page['text'],list) and all(isinstance(t,str) for t in page['text'])
    assert isinstance(page['tables'],list)
    for table in page['tables']:
        assert isinstance(table,list)
        assert all(isinstance(row,list) and all(isinstance(c,str) for c in row) for row in table)
    assert page.get('seconds') is None or page['seconds']>=0
    assert len(page.get('table_bboxes',[]))==len(page['tables'])



from compare_study.settings import rules as data
