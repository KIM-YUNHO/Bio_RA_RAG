"""Independent raw text-node coverage, not a repeat of the DOM unit selector."""
import re
from html.parser import HTMLParser
from pipelines.pipeline_h.exclusion_audit import normalized
from preprocess.table_quality import inspect_table

class ExpectedText(HTMLParser):
    def __init__(self,raw):
        super().__init__(convert_charrefs=True);self.offsets=[0]
        for line in raw.splitlines(keepends=True):self.offsets.append(self.offsets[-1]+len(line))
        self.root='main' if re.search(r'<main\b',raw,re.I) else 'article' if re.search(r'<article\b',raw,re.I) else 'body' if re.search(r'<body\b',raw,re.I) else None
        self.active=self.root is None;self.blocked=[];self.data=[]
        self.feed(raw);self.close()
    def handle_starttag(self,tag,attrs):
        if tag==self.root:self.active=True
        if tag in {'script','style','nav','form','iframe','object','header','footer','template'}:self.blocked.append(tag)
    def handle_endtag(self,tag):
        if tag in self.blocked:self.blocked=self.blocked[:len(self.blocked)-1-self.blocked[::-1].index(tag)]
        if tag==self.root:self.active=False
    def handle_data(self,text):
        if self.active and not self.blocked and text.strip():
            line,col=self.getpos();self.data.append({'start':self.offsets[line-1]+col,'text':text})

def check(raw,units):
    expected=ExpectedText(raw);prepared=[]
    for u in units:
        text=inspect_table(u['text'])['text'] if u['kind']=='table' else u['text']
        prepared.append((u['dom_ref']['start'],u['dom_ref']['end'],normalized(text)))
    missing=[]
    for node in expected.data:
        needle=normalized(node['text'])
        if not any(start<=node['start']<end and needle in text for start,end,text in prepared):missing.append(node)
    return {'expected_nonempty_text_nodes':len(expected.data),'missing_source_text_nodes':missing,
        'scope':'decoded original HTML data nodes inside the content root; explicit non-content tags excluded'}
