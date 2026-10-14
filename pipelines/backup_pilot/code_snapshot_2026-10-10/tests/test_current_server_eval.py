"""Evaluation denominators and provenance must not hide RAG failures."""
import unittest
from eval.current_server_eval import score, summarize, build_cases, read, ROOT


class LiveEvaluationTests(unittest.TestCase):
    def case(self):
        return {'id':'q','suite':'test','topic':'pk','expected':'answerable',
                'document_id':'doc','version_id':'v1','gold_pages':[43]}

    def report(self, items=None, version='v1', pages=None):
        return {'status':'completed','seconds':10,'usage':{'input_tokens':5,'output_tokens':2},
            'results':[{'status':'answered' if items else 'not_found','items':items or [],
                'retrieved_pages':pages if pages is not None else [{'document_id':'doc','version_id':version,'pdf_page':43}],
                'evidence':{'E1':{'document_id':'doc','version_id':version,'pdf_page':43,
                    'text':'Cmax ratio 0.90 (0.82 to 0.98)','review_links':[{'url':'local'}]}}}]}

    def item(self):
        return {'value':'0.90','quote':'Cmax ratio 0.90 (0.82 to 0.98)','evidence':['E1']}

    def test_correct_page_in_wrong_document_version_is_not_a_hit(self):
        result = score(self.case(), self.report([self.item()],version='v2'))
        self.assertIsNone(result['first_gold_page_rank'])
        self.assertFalse(result['gold_in_answer_context'])
        self.assertEqual(result['items_with_expected_source_version'],0)

    def test_empty_answer_has_zero_verified_items_and_null_precision(self):
        result = score(self.case(),self.report())
        self.assertEqual(result['items_with_review_links'],0)
        self.assertIsNone(summarize([result])['literal_precision'])

    def test_valid_quote_does_not_automatically_pass_semantic_review(self):
        result = score(self.case(),self.report([self.item()]))
        self.assertEqual(result['literal_valid_items'],1)
        self.assertIn('pending',result['semantic_verdict'])

    def test_invalid_second_citation_cannot_be_hidden_by_first(self):
        item = self.item(); item['evidence'].append('E2')
        result = score(self.case(),self.report([item]))
        self.assertEqual(result['literal_valid_items'],0)
        self.assertEqual(result['items_with_review_links'],0)

    def test_numeric_substring_false_positive_is_not_a_literal_match(self):
        item = self.item(); item['value']='90'
        self.assertEqual(score(self.case(),self.report([item]))['literal_valid_items'],0)

    def test_missing_gold_remains_in_denominator(self):
        hit = score(self.case(),self.report())
        miss = score(self.case(),self.report(pages=[]))
        result = summarize([hit,miss])
        self.assertEqual(result['answerable_n'],2)
        self.assertEqual(result['page_hit@5'],.5)
        self.assertEqual(result['mrr@5'],.5)

    def test_exclusion_control_does_not_raise_answerable_retrieval_score(self):
        case = self.case(); case.update(expected='scope_excluded',gold_pages=[])
        control = score(case,self.report(pages=[]))
        result = summarize([score(self.case(),self.report(pages=[])),control])
        self.assertEqual(result['n'],2)
        self.assertEqual(result['answerable_n'],1)
        self.assertEqual(result['page_hit@5'],0)

    def test_composed_index_preserves_same_sb17_comparison_cases(self):
        summary = read(ROOT/'data/runs/rag_updates_2026-10-09/stage1_scope/frozen.json')['server_summary']
        expected = build_cases(summary)
        summary['documents'].extend([
            {'document_id':'EMA:64196','product_id':'ABP654','version_id':'other','key':'d6'},
            {'document_id':'FDA:other','product_id':'BMAB1200','version_id':'other','key':'d7'}])
        self.assertEqual(build_cases(summary),expected)
        self.assertEqual(len(expected),22)

    def test_single_cell_normalization_is_independently_verified(self):
        report=self.report([{'value':'5.095 ±1.498','quote':'<td>5.095<br>±1.498</td>',
            'evidence':['E1'],'value_validation':{'method':'within_single_html_cell'}}])
        report['results'][0]['evidence']['E1']['text']='<td>5.095<br>±1.498</td>'
        self.assertEqual(score(self.case(),report)['literal_valid_items'],1)
        report['results'][0]['items'][0]['value']='5.095 ±1.877'
        self.assertEqual(score(self.case(),report)['literal_valid_items'],0)


if __name__ == '__main__': unittest.main()
