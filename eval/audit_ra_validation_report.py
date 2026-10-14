"""Read-only metadata audit; never sends gold content to a model or an index."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
SPLITS = {
    'dev': 'qa_gold_dev.jsonl',
    'in_domain_holdout': 'qa_gold_indomain_test.jsonl',
    'external_holdout': 'qa_gold_external_holdout.jsonl',
}


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tally(records, field):
    return dict(Counter(str(record.get(field)) for record in records))


def audit(package, release):
    manifest = json.loads((package / 'release_manifest.json').read_text(encoding='utf-8-sig'))
    evidence = rows(package / 'evidence_records.jsonl')
    splits = {split: rows(package / name) for split, name in SPLITS.items()}
    questions = [q for values in splits.values() for q in values]
    pending = rows(package / 'pending_records.jsonl')
    records = evidence + questions
    emap = {e['evidence_id']: e for e in evidence}
    findings = []
    for values, field in ((evidence, 'evidence_id'), (questions, 'question_id')):
        for key, count in Counter(v[field] for v in values).items():
            if count != 1:
                findings.append({'check': 'duplicate_id', 'record': key, 'count': count})
    for split, values in splits.items():
        for q in values:
            qid = q['question_id']
            if q['split'] != split:
                findings.append({'check': 'split_mismatch', 'record': qid})
            references = set(q['required_evidence_ids'])
            if references - emap.keys():
                findings.append({'check': 'missing_required_evidence', 'record': qid})
            fact_ids = {f['fact_id'] for f in q['required_facts']}
            if fact_ids != {f['fact_id'] for f in q['condition_labels']}:
                findings.append({'check': 'condition_fact_ids', 'record': qid})
            groups = {e for group in q['retrieval_labels']['required_evidence_groups'] for e in group}
            if groups != references:
                findings.append({'check': 'retrieval_group_ids', 'record': qid})
            for fact in q['required_facts']:
                if not set(fact['evidence_ids']) <= references:
                    findings.append({'check': 'fact_reference_outside_question', 'record': qid})
    for e in evidence:
        if set(e['related_evidence_ids']) - emap.keys():
            findings.append({'check': 'missing_related_evidence', 'record': e['evidence_id']})
    pending_keys = {(p['record_type'], p['record'].get('question_id', p['record'].get('evidence_id'))) for p in pending}
    approved_keys = {('qa', q['question_id']) for q in questions} | {('evidence', e['evidence_id']) for e in evidence}
    if pending_keys & approved_keys:
        findings.append({'check': 'pending_approved_overlap'})
    for field, expected in (('qa_count', len(questions)), ('evidence_count', len(evidence)), ('pending_count', len(pending))):
        if manifest[field] != expected:
            findings.append({'check': 'manifest_count', 'field': field})

    catalog = rows(ROOT / 'data/catalog/documents.jsonl')
    chunks_path = release / 'candidate/chunks.jsonl'
    chunks = rows(chunks_path)
    versions = Counter(c['version_id'] for c in chunks)
    sources = []
    pages_by_hash = {}
    for name, digest in manifest['source_hashes'].items():
        raw = ROOT / 'data/raw' / (digest + '.pdf')
        docs = [d for d in catalog if d.get('version_id') == digest]
        source = {'filename': name, 'sha256': digest, 'raw_exists': raw.exists(),
                  'raw_hash_matches': raw.exists() and sha(raw) == digest,
                  'catalog_matches': len(docs), 'indexed_chunks': versions[digest]}
        if len(docs) == 1:
            doc = docs[0]
            source.update(document_id=doc['document_id'], product_id=doc['product_id'])
            parsed = ROOT / doc['parsed_path']
            source['parsed_sha256'] = sha(parsed)
            pages_by_hash[digest] = {p['pdf_page']: p['text'] for p in rows(parsed)}
        sources.append(source)
    quote_counts = Counter()
    wrapping_ids = []
    for e in evidence:
        page = pages_by_hash.get(e['document_sha256'], {}).get(e['pdf_page'])
        if page is None:
            quote_counts['page_unavailable'] += 1
            continue
        normalize = lambda s: re.sub(r'\s+', ' ', s).strip()
        if normalize(e['evidence_text']) in normalize(page):
            quote_counts['contiguous_whitespace_normalized_match'] += 1
        elif re.sub(r'\s+', '', e['evidence_text']) in re.sub(r'\s+', '', page):
            quote_counts['nonwhitespace_sequence_match_word_wrapping'] += 1
            wrapping_ids.append(e['evidence_id'])
        else:
            quote_counts['needs_source_review'] += 1

    preview_path = ROOT / 'docs/evaluation/QA_GOLD_QA_PREVIEW_2026-10-06.md'
    preview = preview_path.read_text(encoding='utf-8-sig')
    exposure = {}
    for split, values in splits.items():
        exposure[split] = {
            'records': len(values),
            'ids_present': sum(q['question_id'] in preview for q in values),
            'questions_present': sum(q['question'] in preview for q in values),
            'gold_answers_present': sum(bool(q['gold_answer']) and q['gold_answer'] in preview for q in values),
        }
    return {
        'scope': 'Metadata, hashes, references, native-text quote alignment, existing preview exposure; no RA semantic certification.',
        'package': str(package.resolve()), 'package_modified': False,
        'manifest_sha256': sha(package / 'release_manifest.json'),
        'file_hash_matches': {name: sha(package / name) == expected for name, expected in manifest['files'].items()},
        'counts': {'qa': len(questions), 'evidence': len(evidence), 'pending': len(pending)},
        'splits': {split: {'count': len(values), 'types': tally(values, 'question_type'),
                          'topics': tally(values, 'topic'), 'answerable': tally(values, 'answerable'),
                          'required_facts': sum(len(q['required_facts']) for q in values)}
                   for split, values in splits.items()},
        'approval': {field: tally([r['approval'] for r in records], field)
                     for field in ('review_status', 'approval_scope', 'domain_expert', 'reviewer', 'verification_model')},
        'legacy_human_review_status': tally([r['human_review'] for r in records], 'status'),
        'qa_required_fact_roles': tally([f for q in questions for f in q['required_facts']], 'evidence_role'),
        'pending_types': tally(pending, 'record_type'),
        'pending_qa_types': tally([p['record'] for p in pending if p['record_type'] == 'qa'], 'question_type'),
        'structural_findings': findings,
        'source_documents': sources,
        'active_chunks_sha256': sha(chunks_path),
        'quote_alignment': dict(quote_counts), 'word_wrapping_evidence_ids': wrapping_ids,
        'existing_preview_exposure': {'path': str(preview_path.relative_to(ROOT)), 'sha256': sha(preview_path), 'splits': exposure},
        'limits': ['Approval fingerprint algorithm and detailed release-validation log are not packaged; per-record approval fingerprints were not recomputed.',
                   'Native parsed-page alignment does not certify PDF layout, table meaning, or RA semantics.',
                   'Preview exposure does not by itself establish which questions were used for tuning.',
                   'No model execution, generation-quality score, or independent generalization claim.'],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, default=ROOT / 'validation_report')
    parser.add_argument('--release', type=Path, default=ROOT / 'data/versions/01_pilot_baseline_2026-10-10/embedded/rag_finalization_2026-10-10_v2')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = audit(args.package, args.release)
    body = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        target = args.output.resolve()
        package = args.package.resolve()
        release = args.release.resolve()
        if target.is_relative_to(package) or target.is_relative_to(release):
            raise ValueError('Audit output must not modify the evaluation package or release')
        with target.open('x', encoding='utf-8') as stream:
            stream.write(body)
    print(body)


if __name__ == '__main__':
    main()
