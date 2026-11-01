from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cli'))
import device_delivery
class DeviceDeliveryTests(unittest.TestCase):
 def test_readonly_context_is_nonsecret_stable_and_separates_workspaces(self):
  with tempfile.TemporaryDirectory() as root:
   a=Path(root)/'a';b=Path(root)/'b';a.mkdir();b.mkdir()
   one=device_delivery.dispatch(a,'device-delivery/context')[1];two=device_delivery.dispatch(b,'device-delivery/context')[1]
   self.assertEqual(one['workspaceId'],device_delivery.dispatch(a,'device-delivery/context')[1]['workspaceId'])
   self.assertNotEqual(one['workspaceId'],two['workspaceId']);self.assertTrue(one['requiresExplicitOptIn']);self.assertFalse(one['serverPush'])
   self.assertNotIn(str(a),str(one));self.assertEqual(list(a.iterdir()),[])
   with self.assertRaises(ValueError):device_delivery.dispatch(a,'device-delivery/context',{'enabled':True})
 def test_unrelated_path_does_not_open_storage(self):self.assertIsNone(device_delivery.dispatch('/nonexistent','other'))
