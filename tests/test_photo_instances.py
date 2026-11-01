"""Manual instance links are not automatic cross-view or total-count accuracy."""
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import photo_instances as instances

LISTING = 'a'*32
PHOTO_A, PHOTO_B = 'b'*64, 'c'*64


def observation(identity, photo=PHOTO_A, instance='chair-1', feature='chairs', region=None, **changes):
    return {'id':identity,'photoId':photo,'feature':feature,'region':region or [.1,.1,.2,.4],
            'status':'confirmed','instanceId':instance,'linkConfirmed':True,**changes}


def document(*rows, counts=None):
    return {'version':1,'listingId':LISTING,'observations':list(rows),'countConfirmations':counts or {}}


class PhotoInstanceTests(unittest.TestCase):
    def summary(self, value, photos=(PHOTO_A,PHOTO_B)):
        return instances.summarize_review(value,LISTING,photo_ids=photos)

    def test_same_reviewed_chair_across_two_angles_counts_one_not_two(self):
        result=self.summary(document(observation('angle-a'),observation('angle-b',PHOTO_B),counts={'chairs':1}))
        self.assertEqual(result['confirmedVisibleCounts'],{'chairs':1})
        self.assertEqual(result['manuallyConfirmedVisibleCounts'],{'chairs':1})
        self.assertEqual(result['instances'][0]['photoIds'],[PHOTO_A,PHOTO_B])
        self.assertFalse(result['apartmentTotalExact']);self.assertFalse(result['automaticCrossViewMatchingAccepted'])

    def test_two_distinct_reviewed_chairs_in_one_photo_count_two(self):
        result=self.summary(document(observation('left'),observation('right',instance='chair-2',region=[.6,.1,.2,.4]),counts={'chairs':2}))
        self.assertEqual(result['confirmedVisibleCounts'],{'chairs':2});self.assertEqual(result['issues'],[])

    def test_wrong_merge_of_two_disjoint_boxes_in_same_photo_is_a_visible_conflict(self):
        result=self.summary(document(observation('left'),observation('right',region=[.6,.1,.2,.4]),counts={'chairs':1}))
        self.assertEqual(result['confirmedVisibleCounts'],{})
        self.assertEqual(result['manuallyConfirmedVisibleCounts'],{})
        self.assertEqual([row['code'] for row in result['issues']],['same-frame-distinct-regions','count-confirmation-conflict'])

    def test_overlap_duplicate_box_does_not_create_a_second_instance(self):
        result=self.summary(document(observation('first'),observation('duplicate',region=[.105,.1,.2,.4])))
        self.assertEqual(result['confirmedVisibleCounts'],{'chairs':1});self.assertEqual(result['issues'],[])

    def test_same_visible_box_assigned_two_instance_ids_does_not_count_twice(self):
        result=self.summary(document(observation('first'),observation('duplicate',instance='chair-2'),counts={'chairs':2}))
        self.assertEqual(result['confirmedVisibleCounts'],{});self.assertEqual(result['manuallyConfirmedVisibleCounts'],{})
        self.assertEqual(result['issues'][0]['code'],'overlapping-distinct-instances')

    def test_presence_confirmation_without_identity_link_does_not_establish_count(self):
        result=self.summary(document(observation('unlinked',linkConfirmed=False),counts={'chairs':1}))
        self.assertEqual(result['confirmedVisibleCounts'],{});self.assertEqual(result['unlinkedObservationIds'],['unlinked'])
        self.assertEqual(result['state'],'review-required')

    def test_contradictory_feature_identity_never_counts_an_instance(self):
        result=self.summary(document(observation('chair'),observation('desk',PHOTO_B,feature='desk')))
        self.assertEqual(result['confirmedVisibleCounts'],{})
        self.assertEqual(result['issues'][0]['code'],'instance-feature-conflict')

    def test_rejected_observations_do_not_block_other_reviewed_links(self):
        result=self.summary(document(observation('chair'),observation('rejected',PHOTO_B,status='rejected'),counts={'chairs':1}))
        self.assertEqual(result['manuallyConfirmedVisibleCounts'],{'chairs':1});self.assertEqual(result['unlinkedObservationIds'],[])

    def test_removed_photo_invalidates_its_count_and_reports_stale_evidence(self):
        result=self.summary(document(observation('old',PHOTO_B),counts={'chairs':1}),photos=[PHOTO_A])
        self.assertEqual(result['manuallyConfirmedVisibleCounts'],{})
        self.assertEqual(result['issues'][0]['code'],'photo-not-in-listing')

    def test_malformed_regions_duplicate_ids_and_room_properties_are_rejected(self):
        for rows in [(observation('x',region=[.9,.1,.2,.4]),),(observation('x',region=[.1,.1,0,.4]),),(observation('x',region=[.1,.1,.00000001,.4]),),(observation('x',region=[.1,.1,float('nan'),.4]),),
                     (observation('x',feature='roomFurniture'),),(observation('x'),observation('x',PHOTO_B))]:
            with self.subTest(rows=rows),self.assertRaises(ValueError):self.summary(document(*rows))

    def test_privacy_allowlist_never_retains_images_notes_urls_or_model_metadata(self):
        raw=document(observation('x',sourceUrl='PRIVATE_URL',image='PRIVATE_IMAGE',notes='PRIVATE_NOTES',model='PRIVATE_MODEL'))
        raw['notes']='PRIVATE_OTHER';normalized=instances.normalize_review(raw,LISTING)
        self.assertNotIn('PRIVATE',str(normalized));self.assertEqual(normalized['observations'][0]['photoId'],PHOTO_A)

    def test_save_load_revision_conflict_and_explicit_old_revision_are_auditable(self):
        with tempfile.TemporaryDirectory() as folder:
            first=instances.save_review(folder,LISTING,document(observation('a')),photo_ids=[PHOTO_A],expected_revision=0)
            self.assertEqual(first['revision'],1)
            with self.assertRaises(instances.InstanceReviewConflict):instances.save_review(folder,LISTING,document(),photo_ids=[PHOTO_A],expected_revision=0)
            second=instances.save_review(folder,LISTING,document(observation('a'),observation('b',PHOTO_B),counts={'chairs':1}),photo_ids=[PHOTO_A,PHOTO_B],expected_revision=1,actor='operator:review')
            self.assertEqual(second['revision'],2)
            latest=instances.load_review(folder,LISTING,photo_ids=[PHOTO_A,PHOTO_B])
            self.assertEqual(latest['revision'],2);self.assertEqual(latest['summary']['manuallyConfirmedVisibleCounts'],{'chairs':1})
            old=instances.load_review(folder,LISTING,photo_ids=[PHOTO_A],revision=1)
            self.assertEqual(old['revision'],1);self.assertNotEqual(old['sha256'],latest['sha256'])
            self.assertEqual((Path(folder)/'photo-instance-reviews'/'revisions.sqlite3').stat().st_mode&0o777,0o600)

    def test_other_listing_photo_cannot_be_saved_and_corrupt_revision_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):instances.save_review(folder,LISTING,document(observation('a',PHOTO_B)),photo_ids=[PHOTO_A],expected_revision=0)
            instances.save_review(folder,LISTING,document(observation('a')),photo_ids=[PHOTO_A],expected_revision=0)
            with sqlite3.connect(Path(folder)/'photo-instance-reviews'/'revisions.sqlite3') as db:db.execute('UPDATE revisions SET document=?',('{"modified":true}',))
            with self.assertRaisesRegex(ValueError,'сумма'):instances.load_review(folder,LISTING,photo_ids=[PHOTO_A])


if __name__=='__main__':unittest.main()
