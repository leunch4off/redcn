"""Memory guard accounting, pressure hysteresis and process-group ownership."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import training_resources as resources


class ResourceTests(unittest.TestCase):
    def test_localized_macos_swap_and_page_size_are_not_confused_with_bytes(self):
        vm='Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free: 100.\nPages inactive: 200.\nPages speculative: 300.\n'
        row=resources.parse_macos(vm,'total = 2048,00M  used = 787,38M  free = 1260,62M','8589934592')
        self.assertEqual(row['availableEstimateBytes'],600*16384)
        self.assertEqual(row['swapUsedBytes'],int(787.38*1024**2))
        self.assertEqual(row['physicalBytes'],8589934592)

    def test_group_rss_never_includes_unowned_shared_shell_group(self):
        rows='100 100 20\n101 100 30\n102 102 80\n103 102 90'
        self.assertEqual(resources.process_group_rss(rows,100),50*1024)
        self.assertEqual(resources.process_group_rss(rows,103),90*1024)
        self.assertIsNone(resources.process_group_rss(rows,999))

    def test_single_pressure_spike_recovers_but_two_samples_stop(self):
        values=iter([2_000_000_000,100,2_000_000_000,100,100])
        guard=resources.ResourceGuard(lambda pid:{'supported':True,'platform':'Darwin',
            'availableEstimateBytes':next(values),'swapUsedBytes':0,'processGroupRssBytes':1})
        self.assertIsNone(guard.check(admission=True))
        self.assertIsNone(guard.check(100))
        self.assertIsNone(guard.check(100))
        self.assertIsNone(guard.check(100))
        self.assertEqual(guard.check(100),'available-memory-low')
        self.assertEqual(guard.receipt()['minimumAvailableEstimateBytes'],100)

    def test_failed_macos_measurement_blocks_background_admission(self):
        guard=resources.ResourceGuard(lambda pid:{'supported':False,'platform':'Darwin','measurementError':True})
        self.assertEqual(guard.check(admission=True),'measurement-unavailable')

    def test_swap_growth_measured_from_own_run_and_rss_limit_independent(self):
        state={'supported':True,'platform':'Darwin','availableEstimateBytes':2_000_000_000,
               'swapUsedBytes':4_000_000_000,'processGroupRssBytes':100}
        guard=resources.ResourceGuard(lambda pid:dict(state))
        self.assertIsNone(guard.check(admission=True))
        state['swapUsedBytes']+=1024**3
        self.assertIsNone(guard.check(100))
        self.assertEqual(guard.check(100),'swap-growth-limit')
        state['processGroupRssBytes']=4*1024**3
        self.assertEqual(guard.check(100),'process-rss-limit')


if __name__=='__main__':unittest.main()
