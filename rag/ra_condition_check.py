"""Question-condition check on a finished RA answer (no provider call).

A question can restrict its request to a condition: an administration route, a device or
formulation, an indication or a special population. A source fact for a different condition
can still pass claim review (the review treats a sourced actual regimen as a supported fact),
and an answer that drops the condition then reads as the answer to the question. This check
reads the conditions literally named in the ORIGINAL question from a small closed vocabulary
and looks for them in the answer's own cited quotes and the section path / caption of the
cited blocks. A requested condition that this cited scope never states is reported: a gap is added, the answer gets a leading notice, and an
'answered' status becomes 'partial'. Claims, quotes and values are never changed.

Off by default; BIOSIM_CONDITION_CHECK=on enables it in the RA pipeline.
"""
import re

VERSION = 'ra-question-condition-check-2-value-claims'

# dimension -> {condition: (pattern in the Korean/English question, pattern in an English source quote)}
CONDITIONS = {
    'route': {
        '정맥(IV)': (r'정맥|(?<![A-Za-z])I\.?V\.?(?![A-Za-z])|intravenous',
                    r'intravenous|(?<![A-Za-z])I\.?V\.?(?![A-Za-z])|infusion'),
        '피하(SC)': (r'피하|(?<![A-Za-z])S\.?C\.?(?![A-Za-z])|subcutaneous',
                    r'subcutaneous|(?<![A-Za-z])S\.?C\.?(?![A-Za-z])'),
    },
    'device': {
        '자가주사기': (r'자가\s*주사|오토\s*인젝터|autoinjector|auto-injector|\bpen\b',r'auto-?injector|\bpen\b'),
        '프리필드 시린지': (r'프리필드|사전\s*충전|pre-?filled|PFS', r'pre-?filled|\bPFS\b'),
        '바이알': (r'바이알|vial', r'\bvials?\b'),
    },
    'indication': {
        '크론병': (r'크론|crohn', r'crohn'),
        '궤양성 대장염': (r'궤양성\s*대장염|ulcerative\s+colitis', r'ulcerative\s+colitis'),
        '건선성 관절염': (r'건선성\s*관절염|psoriatic\s+arthritis', r'psoriatic\s+arthritis'),
        '판상 건선': (r'판상\s*건선|(?<!건선성 )plaque\s+psoriasis|(?<![가-힣])건선(?!성)', r'plaque\s+psoriasis|psoriasis(?!\s*arthritis)|\bPsO\b'),
    },
    'population': {
        '소아': (r'소아|어린이|pediatric|paediatric|children', r'pa?ediatric|child|adolescent'),
        '청소년': (r'청소년|adolescent', r'adolescent|pa?ediatric'),
        '임부': (r'임부|임신|pregnan', r'pregnan'),
        '고령자': (r'고령|노인|elderly|geriatric', r'elderly|geriatric|≥\s*65|65 years'),
    },
}


def requested(question):
    """Conditions literally named in the question, as {dimension: [condition, ...]}."""
    found = {}
    for dimension, options in CONDITIONS.items():
        hits = [name for name, (q_pattern, _) in options.items() if re.search(q_pattern, question or '', re.I)]
        if hits:
            found[dimension] = hits
    return found


def cited_scope(result, claims=None):
    """Cited quotes plus the section path and caption of each cited evidence block.

    A table cell quote ('260 mg') carries its scope only in the table caption or section
    heading ('Dosage in Crohn's Disease'); the block's full text is not used, because a
    passage can mention other conditions than the one its cited sentence states.
    """
    evidence = result.get('evidence') or {}
    parts = []
    for claim in (result.get('claims', []) if claims is None else claims):
        for source in claim.get('sources', []):
            parts.append(source.get('quote', ''))
            structure = (evidence.get(source.get('evidence')) or {}).get('structure') or {}
            parts += list(structure.get('section_path') or []) + [structure.get('caption') or '']
    return ' '.join(p for p in parts if p)


def value_claim(claim):
    """A claim that reports a measured result (a decimal such as a ratio or CI bound, or a
    percentage); a dose strength or volume ("45 mg/0.5 mL") is not a result."""
    return bool(re.search(r'\d\.\d+(?!\d|\s*(?:mL|mg|ml))|\d\s*%', claim.get('text', '')))


def unlabelled_value(result, claim, dimension, name):
    """A result value whose own cited scope does not state the requested condition, while the
    claim text names no condition of this dimension (it would read as the requested one)."""
    if not value_claim(claim) or any(re.search(q, claim.get('text', ''), re.I)
                                     for q, _ in CONDITIONS[dimension].values()):
        return False
    return not re.search(CONDITIONS[dimension][name][1], cited_scope(result, [claim]), re.I)


def check(question, result):
    """Conditions requested by the question that the cited scope does not state, with conditions it does state.

    The whole answer's cited scope must state the condition. In addition a value claim must not
    silently carry another condition's value: another claim's quote about the requested condition
    (e.g. "extrapolation of the SC data to the IV route") does not make an SC study's GMR an IV
    result. A value claim that names its own condition ("경로는 피하주사였다") is not flagged.
    """
    quotes = cited_scope(result)
    missing = []
    if not quotes:
        return missing
    for dimension, names in requested(question).items():
        for name in names:
            pattern = CONDITIONS[dimension][name][1]
            if re.search(pattern, quotes, re.I):
                values = [c for c in result.get('claims', []) if unlabelled_value(result, c, dimension, name)]
                if not values:
                    continue
                scope = cited_scope(result, values)
                ids = [c.get('id') for c in values]
            else:
                scope, ids = quotes, None
            stated = [other for other, (_, s_pattern) in CONDITIONS[dimension].items()
                      if other != name and re.search(s_pattern, scope, re.I)]
            missing.append({'dimension': dimension, 'requested': name, 'cited_instead': stated,
                            **({'value_claims': ids} if ids else {})})
    return missing


def apply(question, result):
    """Annotate result in place when cited quotes do not state a requested condition."""
    missing = check(question, result)
    result['condition_check'] = {'version': VERSION, 'requested': requested(question), 'missing': missing}
    if not missing:
        return result
    notes = []
    for m in missing:
        other = f" 인용한 근거는 {', '.join(m['cited_instead'])} 자료입니다." if m['cited_instead'] else ''
        notes.append(f"질문한 조건({m['requested']})을 명시한 근거를 확인하지 못했습니다.{other}")
    notice = ' '.join(notes) + ' 아래 내용을 질문한 조건의 결과로 해석하지 마세요.'
    result['gaps'] = list(result.get('gaps', [])) + notes
    result['answer_ko'] = notice + '\n\n' + result.get('answer_ko', '')
    if result.get('status') == 'answered':
        result['status'] = 'partial'
    return result
