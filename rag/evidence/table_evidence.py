"""Cell-bounded HTML matching; never flatten unrelated cells into a value."""
from html import unescape
from html.parser import HTMLParser
import re
from rag.evidence.evidence import normalize, contains_value


class Cells(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=False)
        self.text = text
        self.lines = [0]
        for m in re.finditer('\n',text): self.lines.append(m.end())
        self.cells = []; self.active = None; self.row = -1; self.col = 0; self.invalid = False
        self.feed(text)
        if self.active is not None: self.invalid = True

    def source_offset(self):
        line, col = self.getpos(); return self.lines[line-1]+col

    def handle_starttag(self, tag, attrs):
        if tag == 'tr': self.row += 1; self.col = 0
        if tag in ('td','th'):
            if self.active is not None: self.invalid = True
            pos = self.source_offset(); attr = dict(attrs)
            self.active = {'row':self.row,'column':self.col,'tag':tag,'start':pos,
                'content_start':pos+len(self.get_starttag_text()),
                'rowspan':attr.get('rowspan','1'),'colspan':attr.get('colspan','1')}
            self.col += 1

    def handle_endtag(self, tag):
        if tag in ('td','th'):
            if self.active is None or self.active['tag'] != tag:
                self.invalid = True; return
            end = self.source_offset()
            cell = self.active
            cell.update(content_end=end,end=end+len('</'+tag+'>'))
            cell['raw'] = self.text[cell['content_start']:end]
            # Formatting is normalized inside this cell only; nested table/cells fail.
            if re.search(r'<(?:table|td|th|tr)\b',cell['raw'],re.I): self.invalid = True
            cell['value'] = normalize(unescape(re.sub(r'<[^>]*>', ' ', cell['raw'])))
            self.cells.append(cell); self.active = None


def cells(text):
    parsed = Cells(text)
    return [] if parsed.invalid else parsed.cells


def matching_cells(quote, value):
    return [c for c in cells(quote) if contains_value(c['value'],value)]


def contains_cell_value(quote, value):
    return bool(matching_cells(quote,value))
