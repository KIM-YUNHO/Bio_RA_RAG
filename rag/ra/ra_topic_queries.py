"""Fixed topic queries for product-source retrieval.

The planner writes 1-2 English queries per task, and their wording varies run to
run for a byte-identical planner input. A small closed set of topics, chosen by
the planner per task (plans saved before that field read the ORIGINAL question
with literal patterns), maps to fixed English queries, so part of every
product-slot ranking does not depend on the planner's free-text wording.

This is retrieval relevance only. Topic queries are added next to the planner's
queries (never replace them), carry no expected values, product, study,
document or page identities, and never decide what an answer must contain.
Guideline retrieval is not affected.
"""
import re

VERSION = 'ra-fixed-topic-queries-2-planner-topics'

# (topic, pattern on the original question, fixed English queries)
TOPICS = [
    ('clinical_program',
     r'임상\s*(?:시험|실험|연구|개발|자료|프로그램)|clinical\s+(?:trials?|stud(?:y|ies)|development|programm?e?|data)',
     ['overview of the clinical studies submitted: pharmacokinetic similarity study and comparative clinical efficacy and safety study',
      'clinical study design, study population, treatment arms, dose and route of administration, primary endpoint']),
    ('pk_study',
     r'(?<![A-Za-z])PK(?![A-Za-z])|약동학|pharmacokinetic',
     ['pharmacokinetic similarity study in healthy subjects: study design, single dose, route of administration, AUC and Cmax']),
    ('efficacy',
     r'유효성|효능|(?<![A-Za-z])efficacy(?![A-Za-z])|(?<![A-Za-z])PASI(?![A-Za-z])',
     ['efficacy results of the comparative clinical study: primary endpoint and equivalence margin']),
    ('safety',
     r'안전성|이상\s*반응|부작용|(?<![A-Za-z])safety(?![A-Za-z])|adverse\s+events?',
     ['safety results: treatment-emergent adverse events in the comparative clinical study']),
    ('immunogenicity',
     r'면역\s*원성|항약물\s*항체|중화\s*항체|(?<![A-Za-z])ADAs?(?![A-Za-z])|immunogenicity|anti-?drug\s+antibod',
     ['immunogenicity: incidence of anti-drug antibodies and neutralizing antibodies']),
    ('design',
     r'설계|디자인|(?:study|trial)\s+design|무작위|randomi[sz]',
     ['study design: randomized, double-blind, parallel-group, treatment arms']),
]


def topics(text):
    """Topics literally named in the text, in TOPICS order."""
    return [name for name, pattern, _ in TOPICS if re.search(pattern, text or '', re.I)]


def for_task(question, task, task_count):
    """Fixed queries for one task: the planner's topics for that task.

    Legacy plans: topics named in the original question; with several tasks, a topic
    is used only for the task whose own request also names it.
    """
    if 'topics' in task:
        wanted = [name for name, _, _ in TOPICS if name in task['topics']]
    else:
        wanted = topics(question)
    if 'topics' not in task and task_count > 1:
        own = set(topics(task.get('request', '')))
        wanted = [t for t in wanted if t in own]
    queries = [q for name, _, fixed in TOPICS if name in wanted for q in fixed]
    return wanted, list(dict.fromkeys(queries))
