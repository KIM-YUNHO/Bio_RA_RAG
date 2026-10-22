import unittest
from rag.evidence.table_evidence import cells, contains_cell_value


class TableEvidence(unittest.TestCase):
    def test_mean_sd_with_line_break_is_one_source_cell(self):
        text = '<tr><td>本剤</td><td>5.095<br>±1.498</td></tr>'
        self.assertTrue(contains_cell_value(text,'5.095 ±1.498'))
        cell = cells(text)[1]
        self.assertEqual(text[cell['content_start']:cell['content_end']],'5.095<br>±1.498')
        self.assertEqual((cell['row'],cell['column']),(0,1))

    def test_adjacent_cells_cannot_be_joined_and_numeric_boundaries_survive(self):
        text = '<tr><td>5.095</td><td>±1.498</td><td>145</td></tr>'
        self.assertFalse(contains_cell_value(text,'5.095 ±1.498'))
        self.assertFalse(contains_cell_value(text,'45'))

    def test_nested_or_unclosed_cells_fail_and_entities_stay_in_one_cell(self):
        self.assertEqual(cells('<td>A<td>B</td></td>'),[])
        self.assertEqual(cells('<td>5.095'),[])
        self.assertTrue(contains_cell_value('<td>5.095&nbsp;&plusmn;1.498</td>','5.095 ±1.498'))


if __name__ == '__main__': unittest.main()
