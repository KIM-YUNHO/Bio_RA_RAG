"""Source offsets for paragraphs, printed headings, bullets and wrapped sentences."""
import re

VERSION='source-prose-2'

def boundaries(text,start=0,end=None):
    end=len(text) if end is None else end
    cuts={start,end}
    for gap in re.finditer(r'(?m)^\[UNSUPPLIED SOURCE GAP\][^\n]*(?:\n|$)',text[start:end]):
        cuts.update((start+gap.start(),start+gap.end()))
    for match in re.finditer(r'(?m)^[^\n]+',text[start:end]):
        line=match[0].strip()
        population=bool(re.fullmatch(r'(?:adult|pediatric|paediatric) patients[^\n]{0,80}with:',line,re.I))
        # Standalone multiword capital headings delimit a new section. Do not
        # classify a wrapped sentence or a lone unit/abbreviation as a heading.
        capital=(6<=len(line)<=100 and len(re.findall(r'[A-Z]{2,}',line))>=2
            and not re.search(r'[a-z]',line) and bool(re.fullmatch(r'(?:\d+(?:\.\d+)*[.\s]+)?[A-Z][A-Z0-9 /&(),\-:]*',line)))
        if population or capital:cuts.add(start+match.start())
    return sorted(cuts)

def ranges(text,start,end,limit=1800):
    cuts=boundaries(text,start,end)
    for cursor,stop in zip(cuts,cuts[1:]):
        while stop-cursor>limit:
            bound=cursor+limit
            breaks=[m.end() for m in re.finditer(r'\n|(?<=[.!?])\s+',text[cursor:bound])]
            cut=cursor+breaks[-1] if breaks else bound
            yield cursor,cut
            cursor=cut
        if cursor<stop:yield cursor,stop

def sentences(text,start,end):
    """Keep line-wrapped source wording; bullets start distinct factual runs."""
    cuts={start,end}
    for match in re.finditer(r'(?m)^[ \t]*[•●▪][ \t]*|(?<=[.!?])\s+(?=[A-Z•●▪])',text[start:end]):
        cuts.add(start+match.start() if match[0].lstrip().startswith(('•','●','▪')) else start+match.end())
    for a,b in zip(sorted(cuts),sorted(cuts)[1:]):
        if text[a:b].strip(' \t\r\n•●▪'):yield a,b

def containing(text,start,end):
    for a,b in ranges(text,0,len(text)):
        if a<=start and end<=b:
            return next((text[x:y].strip() for x,y in sentences(text,a,b) if x<=start and end<=y),text[a:b].strip())
    return ''
