import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from rag.ra import ra_retrieval
from rag.ra.ra_sources import GuidelineIndex

ROOT=Path(__file__).resolve().parents[1]

class GuidelineTopicRetrieval(unittest.TestCase):
    def test_requested_ces_topic_reaches_actual_conditional_guideline_paragraph(self):
        report=json.loads((ROOT/'data/runs/ra_today_completion_2026-10-12_v3/C04.json').read_text(encoding='utf-8'))
        index=GuidelineIndex(ROOT/'data/guidelines/ra_guidelines_native_2026-10-12_v2')
        u=report['understanding'];u['tasks'][0]['source_roles']=['guideline']
        corpus=SimpleNamespace(chunks=[])
        try:
            groups=ra_retrieval._retrieve(corpus,u,index,None)
            self.assertEqual(len(groups[0]['query_expansion']),1)
            texts='\n'.join(h['chunk']['text'] for h in groups[0]['hits'])
            self.assertIn('Comparative clinical efficacy studies are not typically required',texts)
            self.assertIn('the role of the studies and the value',texts)
            self.assertLessEqual(len(groups[0]['queries']),3)
            u['tasks'][0]['request']='바이오시밀러 일반 품질 원칙을 확인해줘.'
            self.assertEqual(ra_retrieval._retrieve(corpus,u,index,None)[0]['query_expansion'],[])
        finally:index.close()

if __name__=='__main__':unittest.main()
