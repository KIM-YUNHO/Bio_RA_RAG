"""Render saved HTML locally; original identity and derived PDF hashes stay separate."""
from pathlib import Path
from html import escape
from html.parser import HTMLParser
import re
import fitz
from preprocess.ingest import sha256

VERSION='saved-html-render-1'


class SavedContent(HTMLParser):
    allowed=frozenset(('p','div','section','h1','h2','h3','h4','h5','h6','ul','ol','li','table','thead','tbody','tfoot','tr','td','th','caption','br','b','strong','em','i','sup','sub','span'))
    blocked=frozenset(('script','style','nav','form','iframe','object','svg','img','link','meta','header','footer'))
    void=frozenset(('br','img','link','meta','input','hr','source','wbr'))
    def __init__(self, raw):
        super().__init__(convert_charrefs=True)
        self.root='main' if re.search(r'<main\b',raw,re.I) else 'article' if re.search(r'<article\b',raw,re.I) else 'body' if re.search(r'<body\b',raw,re.I) else None
        self.active=self.root is None;self.skip=[];self.output=[]
        self.feed(raw);self.close()
    def handle_starttag(self,tag,attrs):
        if self.skip:
            if tag not in self.void:self.skip.append(tag)
            return
        if tag==self.root:self.active=True
        if not self.active:return
        if tag in self.blocked:
            if tag not in self.void:self.skip=[tag]
            return
        if tag in self.allowed:
            attrs={k:v for k,v in attrs if k in ('rowspan','colspan') and v and v.isdigit() and 0<int(v)<=100}
            self.output.append('<'+tag+''.join(' '+k+'="'+escape(v,quote=True)+'"' for k,v in attrs.items())+'>')
    def handle_endtag(self,tag):
        if self.skip:
            if tag in self.skip:self.skip=self.skip[:len(self.skip)-1-self.skip[::-1].index(tag)]
            return
        if self.active and tag in self.allowed and tag not in self.void:self.output.append('</'+tag+'>')
        if tag==self.root:self.active=False
    def handle_data(self,data):
        if self.active and not self.skip:self.output.append(escape(data))


def prepare(documents, out):
    for doc in documents:
        if doc.kind!='html':continue
        if sha256(doc.raw_path)!=doc.version_id:raise ValueError('Changed HTML source')
        if doc.rendered_path:
            if sha256(doc.rendered_path)!=doc.rendered_sha256:raise ValueError('Changed HTML rendering')
            continue
        body=''.join(SavedContent(Path(doc.raw_path).read_text(encoding='utf-8-sig')).output)
        # No resource resolver is provided. External fonts, CSS and images cannot be fetched.
        story=fitz.Story(html=body,user_css='body {font-family: sans-serif; font-size: 10pt;} table {border-collapse: collapse;} td,th {border: 0.5pt solid #555; padding: 3pt;}')
        folder=Path(out)/'html_render';folder.mkdir(parents=True,exist_ok=True)
        path=folder/(doc.version_id+'.pdf')
        writer=fitz.DocumentWriter(str(path))
        bounds=fitz.Rect(0,0,595,842);area=bounds+(36,36,-36,-36)
        try:
            for number in range(1000):
                more,_=story.place(area)
                device=writer.begin_page(bounds);story.draw(device);writer.end_page()
                if not more:break
            else:raise ValueError('HTML rendering exceeded 1,000 pages')
        finally:writer.close()
        doc.rendered_path=str(path.resolve());doc.rendered_sha256=sha256(path)
        doc.metadata['source_metadata']={**(doc.metadata.get('source_metadata') or {}),
            'local_render':{'version':VERSION,'original_kind':'html','original_sha256':doc.version_id,
                'rendered_sha256':doc.rendered_sha256,'locator_notice':'HTML local render pages; not original PDF pages',
                'external_resources_fetched':False}}
