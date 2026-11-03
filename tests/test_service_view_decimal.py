"""A removed service promise must not cut the rent at its decimal point."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'cli'))
from service_claims import safe_service_view


class ServiceDecimalTests(unittest.TestCase):
    def test_decimal_title_keeps_rent_layout_and_station_on_one_line(self):
        original = {'fields': {'price': '87000'},
                    'titles': ['✈️莫斯科8.7W独立两居｜近示例地铁站｜免费协助登记'],
                    'title': '✈️莫斯科8.7W独立两居｜近示例地铁站｜免费协助登记'}
        unchanged = copy.deepcopy(original)
        result = safe_service_view(original)
        self.assertEqual(result['titles'][0], '✈️莫斯科8.7W独立两居｜近示例地铁站')
        self.assertEqual(result['title'], result['titles'][0])
        self.assertNotIn('\n', result['title'])
        self.assertEqual(original, unchanged)

    def test_decimal_area_and_price_survive_sentence_cleanup(self):
        result = safe_service_view({'fields': {}, 'body': '月租8.7万卢布，面积52.4㎡。免费抵达登记。'})
        self.assertIn('8.7万卢布', result['body'])
        self.assertIn('52.4㎡', result['body'])
        self.assertNotIn('免费', result['body'])

    def test_safe_title_is_preserved_byte_for_byte(self):
        title = '🏡 52.4㎡｜8.7W/月｜近示例站'
        self.assertEqual(safe_service_view({'fields': {}, 'titles': [title]})['titles'], [title])

    def test_invalid_free_claim_inside_one_clause_is_not_partially_published(self):
        result = safe_service_view({'fields': {}, 'titles': ['8.7W免费抵达登记']})
        self.assertNotEqual(result['titles'][0], '8.')
        self.assertNotIn('免费', result['titles'][0])


if __name__ == '__main__':
    unittest.main()
