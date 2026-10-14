"""Export authored source review and verify frozen evaluation artifacts. No model calls."""
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from urllib.request import urlopen

from eval.ra_workflow_update_eval import audit, digest
from rag.ra_quantities import numbers

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / 'data/runs/ra_structure_compare_2026-10-12_v1'
DOC = ROOT / 'docs/rag/RA_STRUCTURE_COMPARISON_2026-10-12'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    destination = DOC.with_suffix('.json')
    if destination.exists():
        raise ValueError('Review already exported; do not overwrite its evidence')
    protocol, summary = read(RUN/'protocol.json'), read(RUN/'summary.json')
    annotations = read(DOC.with_suffix('.annotations.json'))
    cases = [case['id'] for case in protocol['cases']]
    assert len(cases) == 14 and {c['case'] for c in annotations['cases']} == set(cases)
    assert summary['all_pairs_present_without_process_error']
    verified = {}
    for key in ('code_sha256', 'index_sha256'):
        verified[key] = all(digest(ROOT/name) == sha for name, sha in protocol[key].items())
        assert verified[key], key
    with urlopen('http://127.0.0.1:8775/api/summary', timeout=10) as response:
        live = json.load(response)
    live = {k: live[k] for k in protocol['live_before']}
    assert live == protocol['live_before']
    verified['live_configuration_unchanged'] = True
    reports, identical, changed, counts = {}, [], [], {}
    for case in cases:
        reports[case] = {}
        for arm in ('baseline', 'structure'):
            path = RUN/arm/(case+'.json')
            data = read(path)
            result = data['results'][0]
            check = audit(data)
            assert all(e['document_id'] != 'GL:FDA-SCIENTIFIC-CONSIDERATIONS-2015'
                       for e in result['evidence'].values())
            claims = []
            for disposition, values in (('final', result['claims']),
                                        ('rejected', result['validation']['rejected_items'])):
                counts[(arm, disposition)] = counts.get((arm, disposition), 0)+len(values)
                for claim in values:
                    selected = claim.get('sources', [])
                    own_quote = '\n'.join(s['quote'] for s in selected)
                    citations = []
                    for source in selected:
                        evidence = result['evidence'][source['evidence']]
                        start, end = source['source_range']
                        assert evidence['text'][start:end] == source['quote']
                        citations.append({**source, 'document_id': evidence['document_id'],
                            'pdf_page': evidence.get('pdf_page'), 'version_id': evidence.get('version_id')})
                    claims.append({'id': claim['id'], 'disposition': disposition,
                        'text': claim['text'], 'original_claim_text': claim.get('original_claim_text'),
                        'reason': claim.get('reason'), 'support_review': claim.get('support_review'),
                        'sources': citations,
                        'raw_number_tokens_absent_from_own_quotes': sorted(numbers(claim['text'])-numbers(own_quote)),
                        'number_diagnostic_is_semantic_grade': False})
            reports[case][arm] = {'path': str(path.relative_to(ROOT)), 'sha256': digest(path),
                'audit': check, 'answer_ko': result['answer_ko'], 'claims': claims,
                'stages': data['paired_comparison']['stages']}
        inputs = [tuple((s['schema'], s['payload_sha256']) for s in reports[case][arm]['stages']
                        if s['schema'] != 'query_understanding') for arm in ('baseline', 'structure')]
        if inputs[0] == inputs[1]:
            assert reports[case]['baseline']['answer_ko'] == reports[case]['structure']['answer_ko']
            identical.append(case)
        else:
            changed.append(case)
    assert len(identical) == 10 and set(changed) == {'E01', 'E03', 'T01', 'T02'}
    proof = RUN/'source_review'
    proof.mkdir(exist_ok=True)
    provenance = read(ROOT/'tmp/pdfs/ra_structure_compare_2026-10-12/render_provenance.json')
    for page in provenance['pages']:
        assert digest(Path(page['source'])) == page['sha256']
        original = Path(page['rendered_png'])
        target = proof/original.name
        shutil.copy2(original, target)
        page['original_rendered_png'] = str(original)
        page['rendered_png'] = str(target)
        page['rendered_png_sha256'] = digest(target)
        page['direct_visual_review_completed'] = True
    (proof/'render_provenance.json').write_text(json.dumps(provenance, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    review = {'created_at': datetime.now(timezone.utc).isoformat(),
        'protocol_sha256': digest(RUN/'protocol.json'), 'summary_sha256': digest(RUN/'summary.json'),
        'annotations_sha256': digest(DOC.with_suffix('.annotations.json')),
        'markdown_sha256': digest(DOC.with_suffix('.md')),
        'review_export_code_sha256': digest(Path(__file__)),
        'review': annotations, 'verified': verified, 'live_configuration': live,
        'new_provider_calls': summary['new_provider_calls'],
        'input_tokens': summary['input_tokens'], 'output_tokens': summary['output_tokens'],
        'identical_input_equivalence_controls': identical, 'fresh_changed_input_pairs': changed,
        'claim_counts_not_accuracy': {arm: {state: counts[(arm, state)] for state in ('final', 'rejected')}
                                      for arm in ('baseline', 'structure')},
        'raw_number_token_diagnostic': 'Literal tokens only. Study identifiers may be handled separately by the runtime. This does not certify values, units, columns or semantic support.',
        'pdf_source_review': provenance, 'reports': reports,
        'independent_holdout': False, 'human_expert_certified': False,
        'new_provider_calls_for_review_export': 0}
    destination.write_text(json.dumps(review, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'reports_verified': 28, 'controls': identical, 'changed_input_pairs': changed,
        'counts': review['claim_counts_not_accuracy'], 'decision': annotations['decision'],
        'output': str(destination), 'new_provider_calls': 0}, ensure_ascii=False))


if __name__ == '__main__':
    main()
