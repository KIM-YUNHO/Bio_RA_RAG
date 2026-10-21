"""Small, explicit terminology inventory, not a clinical semantic parser.

No product identifiers, question IDs, pages or answer values belong here.
An observation is a lexical candidate; its source relationship is checked elsewhere.
"""
import re
import unicodedata
from functools import lru_cache


VERSION = "contract-terms-7-written-arm-count"
# Patterns operate on source text, retaining offsets (including simple TeX forms).
RULES = [
    ("endpoint", "AUCinf", r"(?<![A-Za-z])AUC[\s$_{]*(?:0[\s$_{}-]*)?(?:\\(?:text|mathrm)\s*\{)?(?:inf|infinity|∞)\}*(?![A-Za-z])|무한대까지의\s*혈(?:중|장|청)\s*농도[\s-]*시간\s*곡선\s*하면적"),
    ("endpoint", "AUClast", r"(?<![A-Za-z])AUC[\s$_{]*(?:0[\s$_{}-]*)?(?:\\(?:text|mathrm)\s*\{)?last\}*(?![A-Za-z])"),
    ("endpoint", "Cmax", r"(?<![A-Za-z])C[\s$_{]*(?:\\(?:text|mathrm)\s*\{)?max\}*(?![A-Za-z])|(?:최고|최대)\s*혈(?:중|장|청)\s*농도"),
    ("endpoint", "tmax", r"(?<![A-Za-z])t[\s$_{]*(?:\\(?:text|mathrm)\s*\{)?max\}*(?![A-Za-z])"),
    ("endpoint", "CL/F", r"(?<![A-Za-z])CL\s*/\s*F(?![A-Za-z])"),
    ("endpoint", "Vz/F", r"(?<![A-Za-z])V\s*[_{]*z\}*\s*/\s*F(?![A-Za-z])"),
    ("endpoint", "V2/F", r"(?<![A-Za-z])V[\s$_{]*2[\s}$]*/\s*F(?![A-Za-z])"),
    ("endpoint", "V/F", r"(?<![A-Za-z])V\s*/\s*F(?![A-Za-z])"),
    ("endpoint", "PASI", r"\bPASI\b"),
    ("statistic", "median", r"\bmedian\b|중앙값|중앙치"),
    ("statistic", "mean", r"\b(?:arithmetic\s+)?mean\b|\baverage\b|평균"),
    ("statistic", "GMR", r"(?<![A-Za-z])GMR(?![A-Za-z])|geometric\s+mean\s+ratio|ratio\s+of\s+(?:(?:LS|least\s+squares?)\s+)?geometric\s+(?:(?:LS|least\s+squares?)\s+)?means?\b|기하\s*평균\s*비"),
    ("statistic", "CI90", r"90\s*%\s*(?:CI|confidence\s+interval|신뢰\s*구간)"),
    ("statistic", "CI95", r"95\s*%\s*(?:CI|confidence\s+interval|신뢰\s*구간)"),
    ("statistic", "CI99", r"99\s*%\s*(?:CI|confidence\s+interval|신뢰\s*구간)"),
    ("statistic", "difference", r"\b(?:estimated\s+|treatment\s+)?difference\b|군간\s*차이|군\s*간\s*차이"),
    ("analysis_set", "ADA_negative", r"\bADA[\s-]*negative\b|ADA\s*음성"),
    ("analysis_set", "ADA_positive", r"\bADA[\s-]*positive\b|ADA\s*양성(?!률)"),
    ("covariate", "protein_adjusted", r"(?:protein\s*(?:(?:concentration|content)\s*)?adjust(?:ed|ment)|adjust(?:ed|ment)\s+for\s+protein)|단백질\s*(?:함량\s*)?보정"),
    ("covariate", "body_weight", r"(?:body\s+)?weight\s+(?:as\s+)?(?:a\s+)?covariate|체중\s*공변량"),
    ("analysis_model", "ANCOVA", r"\bANCOVA\b|analysis\s+of\s+covariance"),
    ("analysis_timing", "post_hoc", r"post[ -]hoc|사후"),
    ("analysis_timing", "ad_hoc", r"ad[ -]hoc"),
    ("analysis_role", "primary", r"\bprimary\s+(?:(?:targeted|efficacy|clinical|PK|statistical)\s+)?(?:analysis|estimand)\b|주\s*분석(?!군)|일차\s*분석(?!군)"),
    ("analysis_role", "supportive", r"\b(?:supportive|supplementary)\s+(?:analysis|estimand)\b|보조\s*분석(?!군)"),
    ("analysis_role", "sensitivity", r"\bsensitivity\s+(?:analysis|analyses)\b|민감도\s*분석(?!군)"),
    ("attribute", "unit", r"\bunits?\b|단위"),
    ("attribute", "phase", r"\bphase\b|단계"),
    ("attribute", "randomization", r"randomi[sz](?:ation|ed)|무작위\s*배정"),
    ("attribute", "blinding", r"(?:double|single)[ -]blind|눈가림"),
    ("attribute", "arms", r"\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)[ -]arms?\b|군\s*구성"),
    ("attribute", "assay", r"\bassays?\b|분석법"),
    ("attribute", "margin", r"(?:\b(?:bioequivalence|equivalence|biosimilarity|similarity)\s+(?:margin|margins|limits|bounds|criteria|criterion)\b|\bBE\s+criteria\b)|(?:동등성|유사성)\s*(?:한계|마진)"),
    ("population", "healthy", r"healthy\s+(?:(?:adult|male|female)\s+)?(?:subjects|volunteers)|건강인|건강한\s*(?:성인|지원자)"),
    ("population", "psoriasis", r"\bpsoriasis\b|건선"),
    ("route", "SC", r"\bsubcutaneous\b|\bSC\b|피하"),
    ("route", "IV", r"\bintravenous\b|\bIV\b|정맥"),
]


def key(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value).casefold()).replace("–", "-").replace("−", "-")


@lru_cache(maxsize=4096)
def _cached_atoms(text):
    result = []
    for dimension, value, pattern in RULES:
        for match in re.finditer(pattern, text, re.I):
            result.append({"dimension": dimension, "value": value,
                           "quote": match.group(), "start": match.start(), "end": match.end()})
    # A printed geometric mean (or its ratio) is not an arithmetic mean.
    geometric=[m.span() for m in re.finditer(r'geometric\s+(?:(?:LS|least\s+squares?)\s+)?means?\b|기하\s*평균',text,re.I)]
    result = [a for a in result if not (a["value"] == "mean" and any(
        start <= a['start'] and a['end'] <= end for start,end in geometric))]
    for match in re.finditer(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(mg|mcg|µg|μg)(?![A-Za-z/])", text, re.I):
        number, unit = match.groups()
        result.append({"dimension": "dose", "value": number + " " + unit.lower(),
                       "quote": match.group(), "start": match.start(), "end": match.end()})
    # Parse compound units before scalar units; never treat mL/day/kg as day.
    unit_token = r"(?:mcg|µg|μg|ug|\\mu\s*g|ng|mg|g|mL|L|days?|hours?|hr|h|kg)"
    unit_pattern = r"(?<![A-Za-z])" + unit_token + r"(?:[\s$]*[·*/][\s$]*" + unit_token + r")+(?![A-Za-z])|(?<![A-Za-z/])(?:days?|hours?|hr)(?![A-Za-z/])(?!(?:[\s$]*[·*/]))"
    for match in re.finditer(unit_pattern, text, re.I):
        unit = key(match.group()).replace("$", "").replace("\\mug", "μg").replace("days", "day").replace("hours", "h").replace("hour", "h").replace("hr", "h")
        result.append({"dimension": "unit", "value": unit, "quote": match.group(), "start": match.start(), "end": match.end()})
    for match in re.finditer(r'\b(Week|Day)\s*([−-]?\d+)\b|(\d+)\s*(주|일)차',text,re.I):
        period=(match[1].capitalize() if match[1] else 'Week' if match[4]=='주' else 'Day')
        number=(match[2] or match[3]).replace('−','-')
        result.append({'dimension':'timepoint','value':period+' '+number,'quote':match.group(),
                       'start':match.start(),'end':match.end()})
    return tuple(tuple(a.items()) for a in sorted(result, key=lambda a: (a["start"], a["end"], a["dimension"])))


def atoms(text):
    """Known literal mentions with exact offsets and caller-owned results.

    Repeated budget probes reuse only the pure parse of IDENTICAL source text.
    Modifying a returned list/dict cannot modify another source/query's parse.
    Absence is not proof of semantic absence.
    """
    return [dict(a) for a in _cached_atoms(text)]


def grounded_value(dimension, value, quote):
    """Validate recognized aliases or literal labels, not arbitrary paraphrases."""
    return any(a["dimension"] == dimension and key(a["value"]) == key(value) for a in atoms(quote)) or key(value) == key(quote)
