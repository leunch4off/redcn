import base64
import io
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'cli'))
from PIL import Image
import card_api
import local_server
import photo_instance_api
import photo_store


class PhotoInstanceApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.card = 'a'*32
        self.urls = ['https://images.cdn-cian.ru/images/instance-a.jpg',
                     'https://images.cdn-cian.ru/images/instance-b.jpg']
        self.photos = []
        for index, url in enumerate(self.urls):
            stream = io.BytesIO()
            Image.new('RGB', (64, 64), ['red', 'blue'][index]).save(stream, format='PNG')
            uri = 'data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode()
            self.photos.append(photo_store.attach_photo(self.folder, url, uri))
        self.result = {'titles': ['合成测试'], 'body': '合成家具实例测试。', 'tags': '',
                       'fields': {'photos': self.urls}, 'listing': {'images': self.urls}}
        local_server.save_result(self.folder, self.result, self.card)

    def dispatch(self, data=None, namespace='local'):
        return card_api.dispatch(self.folder, 'photo-instances/'+self.card,
                                 data, namespace=namespace)

    def observation(self, identity, index):
        return {'id': identity, 'photoId': self.photos[index]['id'], 'feature': 'chairs',
                'region': [.1, .1, .2, .4], 'status': 'confirmed',
                'instanceId': 'chair-one', 'linkConfirmed': True}

    def test_get_returns_only_originals_of_this_card_in_correct_namespace(self):
        status, value, _, _ = self.dispatch(namespace='phone')
        self.assertEqual(status, 200)
        self.assertEqual([p['id'] for p in value['photos']], [p['id'] for p in self.photos])
        self.assertTrue(all(p['url'].startswith('/api/phone/media/') for p in value['photos']))
        self.assertFalse(value['automaticMatching'])
        self.assertFalse(value['automaticPublication'])
        self.assertNotIn('roomFurniture', value['categories'])

    def test_two_angles_save_as_one_visible_instance_with_manual_confirmation(self):
        document = {'listingId': self.card, 'observations': [self.observation('a', 0), self.observation('b', 1)],
                    'countConfirmations': {'chairs': 1}}
        status, value, _, _ = card_api.dispatch(self.folder, 'photo-instances/'+self.card,
            {'document': document, 'expectedRevision': 0}, namespace='phone', actor='trusted-paired-device')
        self.assertEqual(status, 200)
        self.assertEqual(self.dispatch()[1]['actor'], 'trusted-paired-device')
        self.assertEqual(value['summary']['manuallyConfirmedVisibleCounts'], {'chairs': 1})
        self.assertFalse(value['summary']['apartmentTotalExact'])
        self.assertEqual(self.dispatch()[1]['revision'], 1)
        self.assertEqual(local_server.read_result(self.folder, self.card)['body'], self.result['body'])

    def test_conflict_is_409_and_does_not_overwrite_latest_review(self):
        data = {'document': {'observations': [self.observation('a', 0)]}, 'expectedRevision': 0}
        self.dispatch(data)
        status, value, _, _ = self.dispatch(data)
        self.assertEqual(status, 409)
        self.assertEqual(value['code'], 'PHOTO_INSTANCE_REVISION_CONFLICT')
        self.assertEqual(self.dispatch()[1]['revision'], 1)

    def test_foreign_card_photo_and_client_actor_are_rejected(self):
        forged = self.observation('x', 0)
        forged['photoId'] = 'f'*64
        with self.assertRaises(ValueError):
            self.dispatch({'document': {'observations': [forged]}, 'expectedRevision': 0})
        with self.assertRaises(ValueError):
            self.dispatch({'document': {}, 'expectedRevision': 0, 'actor': 'owner'})

    def test_removed_source_photo_invalidates_existing_link_on_read(self):
        self.dispatch({'document': {'observations': [self.observation('a', 0)]}, 'expectedRevision': 0})
        self.result['fields']['photos'] = self.urls[1:]
        self.result['listing']['images'] = self.urls[1:]
        local_server.save_result(self.folder, self.result, self.card)
        summary = self.dispatch()[1]['summary']
        self.assertEqual(summary['confirmedVisibleCounts'], {})
        self.assertEqual(summary['issues'][0]['code'], 'photo-not-in-listing')

    def test_only_instance_route_and_bounded_request_body_are_claimed(self):
        self.assertIsNone(photo_instance_api.dispatch(self.folder, 'design'))
        self.assertEqual(card_api.request_limit('photo-instances/'+self.card), 400000)
        self.assertIsNone(card_api.request_limit('photo-instances/invalid'))


if __name__ == '__main__':
    unittest.main()
