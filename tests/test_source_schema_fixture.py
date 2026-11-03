import hashlib,json,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'cli'))
from source_schema_fixture import sanitize_html,browser_capture_script

class SourceSchemaFixtureTests(unittest.TestCase):
 def test_sensitive_text_scripts_original_urls_and_contacts_are_dropped(self):
  raw='''<html><head><meta name="token" content="PRIVATE_TOKEN"><script>PRIVATE_SCRIPT</script></head><body><h1 data-name="OfferTitleNew">PRIVATE_ADDRESS</h1><div data-name="Price" onclick="PRIVATE_ONCLICK">PRIVATE_PHONE</div><div data-name="Description" data-secret="PRIVATE_SECRET"><a href="https://private.invalid/secret">PRIVATE_PERSON</a><img src="https://private.invalid/photo.jpg" alt="PRIVATE_ALT"></div><section data-name="AgentInfo"><div data-name="Description">PRIVATE_AGENT</div></section></body></html>'''
  fixture,m=sanitize_html(raw)
  self.assertNotIn('PRIVATE_',fixture);self.assertNotIn('private.invalid',fixture);self.assertNotIn('<script',fixture);self.assertNotIn('onclick',fixture);self.assertNotIn('AgentInfo',fixture)
  self.assertIn('OfferTitleNew',fixture);self.assertIn('fixture.invalid/photo-001.jpg',fixture);self.assertTrue(m['valuesAreFictional'])
 def test_supplied_real_derivatives_are_inert_and_match_manifest(self):
  for platform in ('cian','avito'):
   path=ROOT/'tests/fixtures'/f'{platform}-user-observed-v26.html';raw=path.read_bytes();html=raw.decode();m=json.loads(path.with_suffix('.manifest.json').read_text())
   self.assertEqual(m['platform'],platform);self.assertEqual(hashlib.sha256(raw).hexdigest(),m['sanitizedSha256']);self.assertGreater(m['retainedTags'],100);self.assertTrue(m['valuesAreFictional'])
   self.assertNotIn('<script',html);self.assertNotIn('onclick=',html);self.assertNotIn('data:',html)
   import re
   urls=re.findall(r'https?://[^"<>\s]+',html);self.assertTrue(all(url.startswith('https://fixture.invalid/') for url in urls))
 def test_unrecognized_or_challenge_markup_does_not_become_real_corpus(self):
  for raw in ('<html>verify you are human</html>','<div data-name="AgentInfo"><h1 data-name="OfferTitleNew">private</h1></div>'):
   with self.assertRaises(ValueError):sanitize_html(raw)
 def test_browser_helper_contains_only_structural_return_and_no_network_or_evaluation(self):
  script=browser_capture_script();self.assertIn('sanitizedSha256',script);self.assertIn('crypto.subtle.digest',script)
  for unsafe in ('fetch(', 'XMLHttpRequest', 'document.cookie', 'localStorage', 'eval('):self.assertNotIn(unsafe,script)

if __name__=='__main__':unittest.main()
