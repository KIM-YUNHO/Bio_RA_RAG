"""A literal colon lead-in for an immediately following source bullet list.

No page/section proximity or generated metadata establishes this relationship.
Gaps, tables and a new non-bullet paragraph close the list.
"""
import re

VERSION='prose-list-context-1'

def intro(candidate,evidence):
 if candidate['kind'] not in ('source_paragraph','source_line'):return None
 if not candidate['value'].lstrip().startswith(('•','●','▪')):return None
 text=evidence[candidate['evidence']]['text'];end=candidate['start']
 leads=list(re.finditer(r'(?m)^.*?:[ \t]*$',text[:end]))
 for lead in reversed(leads):
  tail=text[lead.end():end]
  if '[UNSUPPLIED SOURCE GAP]' in tail or '<table' in tail.lower():return None
  stripped=tail.strip()
  if stripped:
   paragraphs=re.split(r'\n\s*\n',stripped)
   if any(not p.lstrip().startswith(('•','●','▪')) for p in paragraphs):continue
  # Include the entire wrapped lead-in sentence, not only its final line.
  from rag.prose import containing
  sentence=containing(text,lead.start(),lead.end()).rstrip()
  if not sentence.endswith(':'):continue
  start=text.rfind(sentence,0,lead.end()+1)
  if start<0:continue
  if '[UNSUPPLIED SOURCE GAP]' in sentence or '<table' in sentence.lower():continue
  return {'version':VERSION,'evidence':candidate['evidence'],'quote':sentence,'source_range':[start,start+len(sentence)],
   'list_start':lead.end(),'basis':'literal_colon_lead_in_and_contiguous_bullet_run','semantic_approval':False}
 return None
