"""Numeric thresholds can be printed category labels, rather than outcomes."""
import re
VERSION='printed-numeric-categories-1'
def weight_category(value):
    return bool(re.search(r'\d+(?:\.\d+)?\s*kg\b',value,re.I) and re.search(
        r'\bor\s+(?:less|more)\b|less\s+than|more\s+than|greater\s+than|\b(?:over|under|up to|to)\b|[<>≤≥]|이하|초과|미만|이상',value,re.I))

def weight_category_key(value):
    """Canonical printed comparator grammar; retain strict/inclusive bounds."""
    text=re.sub(r'\s+',' ',value).strip().casefold()
    text=re.sub(r'greater than|more than|over', '>',text)
    text=re.sub(r'less than|under', '<',text)
    text=re.sub(r'(\d+(?:\.\d+)?)\s*kg\s+or less', r'<=\1 kg',text)
    text=re.sub(r'(\d+(?:\.\d+)?)\s*kg\s+or more', r'>=\1 kg',text)
    text=text.replace('≤','<=').replace('≥','>=')
    return re.sub(r'\s+','',text)
