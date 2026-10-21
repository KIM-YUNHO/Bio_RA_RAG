import unittest
from rag.evidence.source_selection import NUMBER,catalog,schema,condition_ids,resolve,value_ids
from rag.evidence.source_context import complete,count_arm_slots,enforce_arm_coverage


class SourceValueRoles(unittest.TestCase):
    def block(self,text):
        return {'E1':{'text':text,'product_id':'unseen','structure':{'section_id':'s','heading_anchors':[]},'source_spans':[]}}

    def test_compound_bmi_unit_is_one_unchanged_literal(self):
        text='Body mass index between 21.5-28.7 kg/m2 (inclusive).'
        found=list(NUMBER.finditer(text))
        self.assertEqual(found[0][0],'21.5-28.7 kg/m2')
        self.assertEqual(text[found[0].start():found[0].end()],found[0][0])

    def test_numeric_results_cannot_select_qualifier_headers_or_ci_bounds(self):
        evidence=self.block('<table><tr><th>Endpoint</th><th>Ratio of Test/Reference</th><th>90% CI</th></tr>'
            '<tr><td>AUCinf</td><td>1.08</td><td>0.94, 1.22</td></tr></table>')
        candidates=catalog(evidence,'GMR and 90% CI')
        header=next(c for c in candidates.values() if c['value']=='90% CI')
        self.assertIn(header['id'],condition_ids(candidates)['statistic'])
        self.assertNotIn(header['id'],value_ids(candidates))
        allowed=schema(candidates)['schema']['properties']['items']['items']['properties']['value_id']['enum']
        self.assertIn(next(c['id'] for c in candidates.values() if c['value']=='1.08'),allowed)
        self.assertIn(next(c['id'] for c in candidates.values() if c['value']=='0.94, 1.22'),allowed)
        self.assertNotIn(next(c['id'] for c in candidates.values() if c['value']=='0.94'),allowed)
        item={'study':'none','item':'CI','source_label':'CI','value_id':header['id'],'exact':True,'conditions':[]}
        with self.assertRaises(ValueError):resolve({'status':'answered','items':[item]},candidates,evidence)

    def test_per_arm_counts_keep_requested_row_and_printed_column_roles(self):
        evidence=self.block('<table><tr><td>Treatment</td><td>unseen</td><td>EU Reference</td></tr>'
            '<tr><td>Randomized Set</td><td>40</td><td>41</td></tr>'
            '<tr><td>Pharmacokinetic Analysis Set (PKS)</td><td>37</td><td>38</td></tr></table>')
        candidates=catalog(evidence,'군별 PK set 인원은?')
        self.assertNotIn(next(c['id'] for c in candidates.values() if c['value']=='40'),value_ids(candidates))
        value=next(c for c in candidates.values() if c['value']=='38')
        item={'study':'none','conditions':[]}
        complete(item,value,candidates,evidence,'군별 PK set 인원은?')
        roles={c['dimension']:c['value'] for c in item['conditions']}
        self.assertEqual(roles['arm'],'EU Reference')
        self.assertEqual(roles['analysis'],'Pharmacokinetic Analysis Set (PKS)')

    def test_equal_per_arm_counts_require_every_printed_group(self):
        evidence=self.block('<table><tr><td>Treatment</td><td>Group X</td><td>Group Y</td><td>Group Z</td><td>Total</td></tr>'
            '<tr><td>Pharmacokinetic Analysis Set (PKS)</td><td>37 (100.0)</td><td>37 (100.0)</td><td>37 (100.0)</td><td>111</td></tr></table>')
        question='각 군별 PK set 인원은?'
        candidates=catalog(evidence,question)
        slots=count_arm_slots(candidates,question)
        self.assertEqual([s['arm'] for s in slots],['Group X','Group Y','Group Z'])
        self.assertTrue(all(candidates[cid]['value']!='(100.0)' for s in slots for cid in s['value_ids']))
        selected=[{'source_selection':{'value_id':s['value_ids'][0]}} for s in slots]
        out={'items':selected[:1],'status':'answered','validation':{},'answer_ko':'37'}
        enforce_arm_coverage(out,candidates,question)
        self.assertEqual(out['status'],'partial')
        self.assertEqual(out['validation']['requested_arm_coverage']['covered_count'],1)
        self.assertEqual(len(out['validation']['requested_arm_coverage']['missing']),2)
        complete_out={'items':selected,'status':'answered','validation':{},'answer_ko':'37 each'}
        enforce_arm_coverage(complete_out,candidates,question)
        self.assertEqual(complete_out['status'],'answered')
        self.assertEqual(complete_out['validation']['requested_arm_coverage']['missing'],[])


if __name__=='__main__':unittest.main()
