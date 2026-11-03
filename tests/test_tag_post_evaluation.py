from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'cli'))
from tag_post_evaluation import normalize_post, compare_posts
from learning_memory import LearningMemory
import learning_service


def post(identity, views, tags, **changes):
    return {'postId': identity, 'views': views, 'tags': tags,
            'observedAt': '2026-10-30', 'approximate': False,
            'facts': {'metro': '示例站', 'rooms': 2, 'area': 50, 'rent': 80000}, **changes}


class PostStatisticsTests(unittest.TestCase):
    def test_empty_real_observation_set_does_not_fabricate_popularity(self):
        value = compare_posts([], ['莫斯科租房'])
        self.assertEqual(value['state'], 'needs-comparable-observations')
        self.assertIsNone(value['tags'][0]['medianPairedViewDifference'])

    def test_matched_difference_is_descriptive_small_and_approximate(self):
        values = [post('a', 230, ['目标标签'], approximate=True), post('b', 120, ['对照标签'])]
        result = compare_posts(values, ['目标标签'])
        row = result['tags'][0]
        self.assertEqual(row['matchedPairs'], 1)
        self.assertEqual(row['medianPairedViewDifference'], 110)
        self.assertEqual(row['approximatePairs'], 1)
        self.assertEqual(row['exposureUnmatchedPairs'], 1)
        self.assertTrue(row['smallSample'])
        self.assertFalse(row['causalEffectKnown'])
        self.assertFalse(result['postViewsAreTagPopularity'])

    def test_incomparable_properties_are_excluded_before_view_comparison(self):
        for field, value in [('metro', '另一个站'), ('rooms', 3), ('area', 100), ('rent', 160000)]:
            control = post('b', 100000, ['control'])
            control['facts'][field] = value
            result = compare_posts([post('a', 100, ['target']), control], ['target'])
            self.assertEqual(result['tags'][0]['matchedPairs'], 0)

    def test_matching_never_chooses_control_by_most_favorable_views(self):
        a, b, c = post('a', 100, ['target']), post('b', 1, ['control']), post('c', 10000, ['control'])
        result = compare_posts([a, b, c], ['target'])
        b['views'], c['views'] = c['views'], b['views']
        changed = compare_posts([a, b, c], ['target'])
        self.assertEqual(result['tags'][0]['pairs'][0]['control'], changed['tags'][0]['pairs'][0]['control'])

    def test_repeated_same_post_is_one_post_not_two_independent_samples(self):
        values = [post('a', 100, ['target']), post('a', 150, ['target'], observedAt='2026-10-31'), post('b', 90, ['control'])]
        result = compare_posts(values, ['target'])
        self.assertEqual(result['uniquePosts'], 2)
        self.assertEqual(result['repeatedObservationsExcluded'], 1)
        self.assertEqual(result['tags'][0]['pairs'][0]['postViews'], 150)

    def test_conflicting_same_day_and_invalid_numbers_reject(self):
        with self.assertRaises(ValueError):
            compare_posts([post('a', 100, ['target']), post('a', 200, ['target'])], ['target'])
        for value in (True, -1, float('nan')):
            with self.assertRaises(ValueError): normalize_post(post('a', value, ['target']))

    def test_post_urls_drop_tokens_and_hash_identity_idempotently(self):
        first = normalize_post(post('https://example.org/post/abc?token=SECRET#fragment', 1, ['target']))
        second = normalize_post(post('https://example.org/post/abc?tracking=other', 1, ['target']))
        self.assertEqual(first['postId'], second['postId'])
        self.assertNotIn('SECRET', str(first))
        self.assertEqual(normalize_post(first), first)

    def test_unknown_eligibility_never_adds_tags_from_statistics(self):
        result = compare_posts([post('a', 9999, ['不合适大学']), post('b', 1, ['control'])], ['莫斯科租房'])
        self.assertEqual([row['tag'] for row in result['tags']], ['#莫斯科租房'])

    def test_different_known_exposure_windows_do_not_form_a_control(self):
        a = post('a', 100, ['target'], observedAt='2026-10-30T12:00:00Z', publishedAt='2026-10-30T00:00:00Z')
        b = post('b', 1000, ['control'], observedAt='2026-10-30T12:00:00Z', publishedAt='2026-10-20T00:00:00Z')
        self.assertEqual(compare_posts([a, b], ['target'])['tags'][0]['matchedPairs'], 0)

    def test_memory_keeps_one_post_row_for_all_tags_and_excludes_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = LearningMemory(directory)
            value = post('a', 230, ['target', 'other'])
            event = memory.record_post_observation(value)
            self.assertEqual(event['status'], 'pending')
            self.assertEqual(memory.compare_post_observations(['target'])['uniquePosts'], 0)
            memory.review(event['id'], 'approved')
            memory.record_post_observation(post('b', 120, ['control']), approved=True)
            self.assertEqual(memory.compare_post_observations(['target'])['tags'][0]['matchedPairs'], 1)
            self.assertEqual(memory.rank_tags(['other'])[0]['observations'], 1)
            memory.record_post_observation(post('a', 500, ['target', 'other'], observedAt='2026-10-31'), approved=True)
            self.assertEqual(memory.rank_tags(['other'])[0]['observations'], 1)
            self.assertEqual(memory.rank_tags(['other'])[0]['views'], 500)
            self.assertEqual(memory.status()['approvedExamples'], 0)

    def test_runtime_actions_are_integrated_and_do_not_accept_client_actor(self):
        with tempfile.TemporaryDirectory() as directory:
            response = learning_service.dispatch(directory, 'learning', {'action': 'record-post-stat', 'post': post('a', 1, ['target'])})
            self.assertEqual(response[0], 200)
            response = learning_service.dispatch(directory, 'learning', {'action': 'compare-post-stats', 'eligibleTags': ['target']})
            self.assertEqual(response[1]['uniquePosts'], 0)
            with self.assertRaises(ValueError):
                learning_service.dispatch(directory, 'learning', {'action': 'record-post-stat', 'post': {}, 'actor': 'owner'})


if __name__ == '__main__':
    unittest.main()
