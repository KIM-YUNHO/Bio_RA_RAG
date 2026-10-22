"""Original-DOM content units, independent of PDF preview coordinates."""
import html,re
from html.parser import HTMLParser
from dataclasses import dataclass,field
from preprocess.source.html_render import SavedContent
from preprocess.ingest import stable_hash

BLOCKED={'script','style','nav','form','iframe','object','header','footer','template'}
VOID={'br','img','hr','input','meta','link','source','wbr'}
LEAF={'p','h1','h2','h3','h4','h5','h6','li','dt','dd','figcaption','pre','blockquote'}

@dataclass
class Node:
    tag:str
    start:int
    end:int
    attrs:dict=field(default_factory=dict)
    children:list=field(default_factory=list)
    path:str=''

class Tree(HTMLParser):
    def __init__(self,raw):
        super().__init__(convert_charrefs=True);self.raw=raw;self.offsets=[0]
        for line in raw.splitlines(keepends=True):self.offsets.append(self.offsets[-1]+len(line))
        self.root=Node('document',0,len(raw),path='/document');self.stack=[self.root];self.warnings=[]
        self.feed(raw);self.close()
        for n in self.stack[1:]:n.end=len(raw);self.warnings.append({'path':n.path,'reason':'unclosed_node'})
    def pos(self):
        line,col=self.getpos();return self.offsets[line-1]+col
    def handle_starttag(self,tag,attrs):
        parent=self.stack[-1];index=1+sum(isinstance(n,Node) and n.tag==tag for n in parent.children)
        n=Node(tag,self.pos(),self.pos()+len(self.get_starttag_text()),dict(attrs),path=f'{parent.path}/{tag}[{index}]')
        parent.children.append(n)
        if tag not in VOID:self.stack.append(n)
    def handle_startendtag(self,tag,attrs):
        self.handle_starttag(tag,attrs)
        if tag not in VOID:self.handle_endtag(tag)
    def handle_endtag(self,tag):
        for i in range(len(self.stack)-1,0,-1):
            if self.stack[i].tag==tag:
                end=self.raw.find('>',self.pos())+1
                for n in self.stack[i:]:n.end=end
                self.stack=self.stack[:i];return
    def handle_data(self,data):self.stack[-1].children.append(data)

def walk(node):
    yield node
    for child in node.children:
        if isinstance(child,Node):yield from walk(child)

def plain(node,exclude_lists=False):
    if isinstance(node,str):return node
    if node.tag in BLOCKED or exclude_lists and node.tag in {'ul','ol'}:return ''
    if node.tag=='br':return '\n'
    parts=[];previous_block=False
    for c in node.children:
        block=isinstance(c,Node) and c.tag in {'p','div','li','h1','h2','h3','h4','h5','h6'}
        if previous_block and block:parts.append('\n')
        parts.append(plain(c,exclude_lists));previous_block=block
    return ''.join(parts)

def compact(text):return re.sub(r'\s+',' ',text).strip()

def table_html(node):
    def descendants(n,tag):
        for c in n.children:
            if not isinstance(c,Node):continue
            if c.tag==tag:yield c
            elif c.tag!='table':yield from descendants(c,tag)
    rows=[]
    for row in descendants(node,'tr'):
        cells=[]
        for cell in row.children:
            if not isinstance(cell,Node) or cell.tag not in {'td','th'}:continue
            attrs=''
            for name in ('rowspan','colspan'):
                value=cell.attrs.get(name,'1')
                if value.isdigit() and 1<int(value)<=100:attrs+=f' {name}="{value}"'
            def inline(n):
                if isinstance(n,str):return html.escape(n)
                if n.tag in BLOCKED:return ''
                body=''.join(inline(c) for c in n.children)
                if n.tag in {'sup','sub','b','strong','em','i'}:return f'<{n.tag}>{body}</{n.tag}>'
                return ('<br>' if n.tag=='br' else body)
            parts=[];previous_block=False
            for child in cell.children:
                block=isinstance(child,Node) and child.tag in {'p','div','li'}
                if previous_block and block:parts.append('<br>')
                parts.append(inline(child));previous_block=block
            cells.append(f'<{cell.tag}{attrs}>{"".join(parts).strip()}</{cell.tag}>')
        if cells:rows.append('<tr>'+''.join(cells)+'</tr>')
    return '<table>'+''.join(rows)+'</table>'

def parse(raw,document_id,version_id):
    tree=Tree(raw);all_nodes=list(walk(tree.root));root=tree.root
    for tag in ('main','article','body'):
        found=next((n for n in all_nodes if n.tag==tag),None)
        if found:root=found;break
    units=[];exclusions=[];headings=[]
    def emit(node,kind,text,render):
        if not text.strip():return
        part=sum(u['dom_ref']['node_path']==node.path for u in units)
        uid='dom'+stable_hash([document_id,version_id,node.path,node.start,node.end,kind,part])[:28]
        units.append({'id':uid,'kind':kind,'text':text,'render_html':render,
            'dom_ref':{'node_path':node.path,'start':node.start,'end':node.end,
                'raw_fragment_sha256':__import__('hashlib').sha256(raw[node.start:node.end].encode('utf-8')).hexdigest()},
            'tag':node.tag,'source_part':part,'section_path':list(headings),
            'source_representation':'canonical DOM table' if kind=='table' else 'DOM decoded text with whitespace normalization'})
    def visit(node):
        if node.tag in BLOCKED:
            exclusions.append({'node_path':node.path,'start':node.start,'end':node.end,'reason':'non_content_'+node.tag});return
        if node.tag=='table':
            for child in node.children:
                if isinstance(child,Node) and child.tag=='caption':
                    emit(child,'text',compact(plain(child)),'<p>'+html.escape(compact(plain(child)))+'</p>')
            content=table_html(node);emit(node,'table',content,content);return
        if node.tag=='img':
            alt=node.attrs.get('alt','');emit(node,'image_reference',alt or 'Image reference', '<p>'+html.escape(alt or 'Image reference')+'</p>')
            if units:units[-1]['image_reference']={'src':node.attrs.get('src'),'alt':alt,'external_resource_fetched':False}
            return
        if node.tag in LEAF:
            text=compact(plain(node,exclude_lists=node.tag=='li'))
            if re.fullmatch(r'h[1-6]',node.tag):
                level=int(node.tag[1]);headings[level-1:]=[text]
            emit(node,'text',text,f'<{node.tag if node.tag.startswith("h") else "p"}>{html.escape(text)}</{node.tag if node.tag.startswith("h") else "p"}>')
            if node.tag=='li':
                for child in node.children:
                    if isinstance(child,Node) and child.tag in {'ul','ol'}:visit(child)
            return
        bare=[]
        def flush():
            text=compact(''.join(bare));bare.clear()
            if text:emit(node,'text',text,'<p>'+html.escape(text)+'</p>')
        for child in node.children:
            if isinstance(child,str):bare.append(child)
            elif child.tag in {'span','a','b','strong','em','i','sup','sub','br'}:bare.append(plain(child))
            else:flush();visit(child)
        flush()
    visit(root)
    return {'units':units,'explicit_exclusions':exclusions,'parser_warnings':tree.warnings,
        'source_root':root.path,'version':'html-original-dom-1'}
