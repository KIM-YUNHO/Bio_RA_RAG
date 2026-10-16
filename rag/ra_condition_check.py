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

VERSION = 'ra-question-condition-check-3-extrapolation-timepoint'

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


# A condition named only where a source extrapolates or waives it ("extrapolation of the SC data
# to the IV route", "waive a clinical study using the IV presentation") is not studied data for it.
EXTRAPOLATION = re.compile(r'extrapolat|waiv|외삽|면제', re.I)


def states(pattern, scope):
    """The scope states the condition in a sentence that is not an extrapolation or waiver."""
    return any(re.search(pattern, sentence, re.I) and not EXTRAPOLATION.search(sentence)
               for sentence in re.split(r'(?<=[.;])\s+|\n', scope))


def value_claim(claim):
    """A claim that reports a measured result (a decimal such as a ratio or CI bound, or a
    percentage); a dose strength or volume ("45 mg/0.5 mL") is not a result."""
    return bool(re.search(r'\d\.\d+(?!\d|\s*(?:mL|mg|ml))|\d\s*%', claim.get('text', '')))


def unlabelled_value(result, claim, dimension, name):
    """A result value whose own cited scope does not state the requested condition, while the
    claim text names no OTHER condition of this dimension (naming only the requested one, as in
    "IV 외삽을 뒷받침한 시험의 GMR", still reads as the requested condition's result)."""
    if not value_claim(claim) or any(re.search(q, claim.get('text', ''), re.I)
                                     for other, (q, _) in CONDITIONS[dimension].items() if other != name):
        return False
    return not states(CONDITIONS[dimension][name][1], cited_scope(result, [claim]))


# Visit time points that make a value time-point specific; dosing/baseline days are not.
TIMEPOINT = re.compile(r'\b(Day|Week|Month)\s*(-?\d+)\b', re.I)
# A source sentence that reports the whole period ("overall incidence ... across timepoints").
OVERALL = re.compile(r'overall|across\s+(?:all\s+)?time\s*points|any\s+time\s*point|at\s+least\s+one|whole\s+study|entire\s+study', re.I)
TIME_WORDS = re.compile(r'(?:Day|Week|Month)\s*\d+|\d+\s*(?:일|주|개월)\s*(?:차|째|시점|까지|후)?|시점|기간|기준선|baseline|EOS|종료', re.I)


def timepoint_values(question, result):
    """Value claims citing time-point specific results (Day 29, Day 99) without naming a time
    point, when the question named none either: the value would read as the whole-period result."""
    if TIME_WORDS.search(question or ''):
        return [], []
    claims, points = [], []
    for claim in result.get('claims', []):
        if not value_claim(claim) or TIME_WORDS.search(claim.get('text', '')):
            continue
        scope = cited_scope(result, [claim])
        if OVERALL.search(scope):
            continue
        found = [f'{u.title()} {n}' for u, n in TIMEPOINT.findall(scope)
                 if not (u.lower() in ('day', 'week') and int(n) <= 1)]
        if found:
            claims.append(claim)
            points += found
    return claims, list(dict.fromkeys(points))


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
            if states(pattern, quotes):
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
    claims, points = timepoint_values(question, result)
    if claims:
        missing.append({'dimension': 'timepoint', 'requested': '시점 미지정', 'cited_instead': points,
                        'value_claims': [c.get('id') for c in claims]})
    return missing


def apply(question, result):
    """Annotate result in place when cited quotes do not state a requested condition."""
    missing = check(question, result)
    result['condition_check'] = {'version': VERSION, 'requested': requested(question), 'missing': missing}
    if not missing:
        return result
    notes = []
    for m in missing:
        if m['dimension'] == 'timepoint':
            notes.append(f"인용한 값은 특정 시점({', '.join(m['cited_instead'][:4])})의 결과입니다. 전체 기간의 값으로 해석하지 마세요.")
            continue
        other = f" 인용한 근거는 {', '.join(m['cited_instead'])} 자료입니다." if m['cited_instead'] else ''
        notes.append(f"질문한 조건({m['requested']})을 명시한 근거를 확인하지 못했습니다.{other}")
    notice = ' '.join(notes) + ' 아래 내용을 질문한 조건의 결과로 해석하지 마세요.'
    result['gaps'] = list(result.get('gaps', [])) + notes
    result['answer_ko'] = notice + '\n\n' + result.get('answer_ko', '')
    if result.get('status') == 'answered':
        result['status'] = 'partial'
    return result
