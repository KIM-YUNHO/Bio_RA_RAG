from copy import deepcopy
from types import SimpleNamespace
import unittest

from rag.index import document_structure as ds
from rag.evidence.source_study_scope import heading_conflict, link_conflict, without_conflicting_heading
from rag.ra.ra_requirements import study_identity, verified_study_key
from rag.ra.ra_retrieval import packet
from rag.evidence.budgeted_evidence import BudgetedEvidence


def block(key='a', study='20190230', text='', kind='text', caption=None, product='ABP654'):
    return {'chunk_id':key,'document_id':'d','version_id':'v','product_id':product,
            'text':text,'structure':{'kind':kind,'caption':caption,'section_id':'s',
                'section_path':['Clinical safety',f'Study {study}'],
                'heading_anchors':[{'kind':'study','title':f'Study {study}','section_id':'s',
                    'study_keys':[], 'heading_location':{'block_id':'heading'}}]}}


class ContextStudyGuardTests(unittest.TestCase):
    def test_new_standalone_condition_heading_closes_previous_study(self):
        for title in ('Clinical Ps study 20190232', 'Clinical psoriasis study 20190232',
                      'Clinical psoriatic arthritis study 20190232'):
            self.assertEqual(ds.identify(title,'ABP654',version=ds.VERSION)['study_keys'],['ST_ABP_20190232'])
        self.assertIsNone(ds.identify('Clinical Ps study 20190232','ABP654',version=ds.SECTIONS_8))
        for text in ('Clinical results from study 20190232','Clinical Ps study 20190232 was completed.',
                     'Clinical Ps study 20190232 .... 84'):
            self.assertIsNone(ds.identify(text,'ABP654',version=ds.VERSION))
        tracker=ds.Tracker('d','v','ABP654',version=ds.VERSION)
        def refs():return [{'block_id':'x','line_start':0,'line_end':0}]
        tracker.scan(['Single dose PK study 20190230'],refs(),83)
        _,_,_,states=tracker.scan(['Clinical Ps study 20190232'],refs(),84)
        self.assertEqual(states[0]['heading_studies'],['ST_ABP_20190232'])

    def test_wrong_printed_table_reference_cannot_cross_trials(self):
        source=block(study='20190232',text='Week 28 trough concentrations (see Table 5).')
        target=block(kind='table_candidate',caption='Table 5. Study 20190230 PK results')
        self.assertTrue(link_conflict(source,target,source['text']))
        self.assertFalse(link_conflict(source,block(kind='table_candidate',caption='Table 7. Study 20190232')))
        # A stale carried title cannot veto an explicit, matching caption.
        target['structure']['caption']='Table 49. Study 20190232 ADA results'
        self.assertTrue(heading_conflict(target))
        self.assertFalse(link_conflict(source,target))
        target['structure']['caption']='Table 10. Description of the clinical study'
        target['text']='<table><tr><td>Study 20190230</td></tr></table>'
        self.assertTrue(link_conflict(source,target))

    def test_parts_and_versions_remain_separate_but_parent_is_compatible(self):
        source=block(study='CT-P43 1.1 Part 1',product='CTP43')
        target=block(study='CT-P43 1.1 Part 2',product='CTP43',kind='table_candidate',
                     caption='Table 3. CT-P43 1.1 Part 2')
        self.assertTrue(link_conflict(source,target))
        source['structure']['heading_anchors'][0]['title']='Study CT-P43 1.1'
        self.assertFalse(link_conflict(source,target))
        target['version_id']='other'
        self.assertTrue(link_conflict(source,target))

    def test_conflicted_heading_is_not_supplied_or_used_for_expansion(self):
        original=block(kind='table_candidate',caption='Table 49. Study 20190232',text='Literal table body')
        before=deepcopy(original)
        self.assertIsNone(study_identity(original))
        self.assertIsNone(verified_study_key(original))
        clean=without_conflicting_heading(original)
        self.assertFalse(clean['structure']['heading_anchors'])
        self.assertEqual(clean['text'],original['text'])
        self.assertEqual(original,before)
        corpus=SimpleNamespace(evidence_adapter=SimpleNamespace())
        parts=packet(corpus,original,'retrieved')
        self.assertEqual(len(parts),1)
        self.assertEqual(parts[0][0]['text'],'Literal table body')

    def test_adapter_filters_actual_link_targets(self):
        source=block(study='20190232',text='Study 20190232. See Table 5.')
        source['structure']['related_visual_link_ids']=['l']
        target=block(key='t',kind='table_candidate',caption='Table 5. Study 20190230')
        # Exercise the real adapter method with a valid source-bound reference.
        source['provenance']={'source_spans':[{'unit_id':'u','representation_start':0,
            'representation_end':len(source['text']),'separator_before':''}]}
        link={'link_id':'l','visual_id':'visual','context_unit_id':'ctx','context_range':[0,len(source['text'])],
              'document_id':'d','version_id':'v','source_unit_id':'u',
              'link_status':'linked_candidate','context_role':'referring_sentence'}
        adapter=BudgetedEvidence.__new__(BudgetedEvidence)
        adapter.visual_bundle={'version':'visual-context-3','units':[{'context_unit_id':'ctx','text':source['text']}],
                               'links':[link]}
        adapter.visual_links={'l':link};adapter.by_visual={('d','v','visual'):[target]}
        self.assertEqual(adapter.linked_candidates(source,{'t'}),[])


if __name__=='__main__':unittest.main()
