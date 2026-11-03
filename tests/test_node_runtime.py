"""Node preflight with launchd's PATH and real executable identity changes."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'cli'))
import node_runtime
import redcn


class NodeRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        node_runtime._version.cache_clear()
        self.environment = patch.dict(node_runtime.os.environ, {'PATH':'/usr/bin:/bin','REDCN_NODE':''})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        node_runtime._version.cache_clear()
        self.temp.cleanup()

    def executable(self, name):
        path = self.folder/name
        path.write_text('fixture')
        path.chmod(0o700)
        return path

    def version(self, value, code=0):
        return subprocess.CompletedProcess([],code,value+'\n','')

    def test_homebrew_absolute_path_works_when_launchd_path_has_no_node(self):
        binary = self.executable('brew-node')
        with patch.object(node_runtime.shutil,'which',return_value=None), \
                patch.object(node_runtime,'COMMON_NODE_PATHS',(str(binary),)), \
                patch.object(node_runtime.subprocess,'run',return_value=self.version('v24.4.0')) as run:
            self.assertEqual(node_runtime.resolve_node(),str(binary.resolve()))
            self.assertEqual(node_runtime.require_node(),str(binary.resolve()))
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0],[str(binary.resolve()),'--version'])
        self.assertEqual(run.call_args.kwargs['stdin'],subprocess.DEVNULL)
        self.assertLessEqual(run.call_args.kwargs['timeout'],1.5)

    def test_old_path_node_does_not_hide_newer_homebrew_runtime(self):
        old,new = self.executable('old-node'),self.executable('brew-node')
        with patch.object(node_runtime.shutil,'which',return_value=str(old)), \
                patch.object(node_runtime,'COMMON_NODE_PATHS',(str(new),)), \
                patch.object(node_runtime.subprocess,'run',side_effect=[self.version('v18.20.1'),self.version('v22.16.0')]) as run:
            self.assertEqual(node_runtime.resolve_node(),str(new.resolve()))
        self.assertEqual(run.call_count,2)

    def test_explicit_configuration_is_respected_and_old_version_is_actionable(self):
        old,new = self.executable('configured'),self.executable('other')
        with patch.dict(node_runtime.os.environ,{'REDCN_NODE':str(old)}), \
                patch.object(node_runtime,'COMMON_NODE_PATHS',(str(new),)), \
                patch.object(node_runtime.subprocess,'run',return_value=self.version('v20.19.0')) as run:
            with self.assertRaisesRegex(node_runtime.NodeRuntimeError,'20.19.0.*22'):
                node_runtime.resolve_node()
        self.assertEqual(run.call_count,1)

    def test_missing_runtime_and_failed_probe_have_different_errors(self):
        with patch.object(node_runtime.shutil,'which',return_value=None), \
                patch.object(node_runtime,'COMMON_NODE_PATHS',()):
            with self.assertRaisesRegex(node_runtime.NodeRuntimeError,'не нашёл.*REDCN_NODE'):
                node_runtime.resolve_node()
        broken = self.executable('broken-node')
        with patch.object(node_runtime.subprocess,'run',side_effect=subprocess.TimeoutExpired('node',1.5)):
            with self.assertRaisesRegex(node_runtime.NodeRuntimeError,'не запускается или не отвечает'):
                node_runtime.resolve_node(broken)

    def test_replaced_executable_invalidates_preflight_cache(self):
        binary = self.executable('node')
        with patch.object(node_runtime.subprocess,'run',side_effect=[self.version('v22.0.0'),self.version('v20.0.0')]) as run:
            self.assertEqual(node_runtime.resolve_node(binary),str(binary.resolve()))
            self.assertEqual(node_runtime.resolve_node(binary),str(binary.resolve()))
            binary.write_text('replacement with a different size')
            with self.assertRaisesRegex(node_runtime.NodeRuntimeError,'20.0.0'):
                node_runtime.resolve_node(binary)
        self.assertEqual(run.call_count,2)

    def test_resolved_brew_aliases_are_probed_only_once(self):
        binary=self.executable('node');alias=self.folder/'alias';alias.symlink_to(binary)
        with patch.object(node_runtime.shutil,'which',return_value=str(alias)), \
                patch.object(node_runtime,'COMMON_NODE_PATHS',(str(binary),)), \
                patch.object(node_runtime.subprocess,'run',return_value=self.version('v18.1.0')) as run:
            with self.assertRaises(node_runtime.NodeRuntimeError):node_runtime.resolve_node()
        self.assertEqual(run.call_count,1)


class GeneratorRuntimeTests(unittest.TestCase):
    def test_generator_uses_the_resolved_binary_and_keeps_shared_js(self):
        completed=subprocess.CompletedProcess([],0,'{"titles":["中文"],"body":"正文"}','')
        with patch.object(node_runtime,'resolve_node',return_value='/absolute/node'), \
                patch.object(redcn.subprocess,'run',return_value=completed) as run:
            result=redcn.generate('Квартира')
        self.assertEqual(result['body'],'正文')
        self.assertEqual(run.call_args.args[0],['/absolute/node',str(redcn.ROOT/'cli/generate.mjs')])
        self.assertEqual(run.call_args.kwargs['timeout'],30)

    def test_generator_timeout_is_not_misreported_as_missing_node(self):
        with patch.object(node_runtime,'resolve_node',return_value='/absolute/node'), \
                patch.object(redcn.subprocess,'run',side_effect=subprocess.TimeoutExpired('node',30)):
            with self.assertRaisesRegex(ValueError,'Генератор не ответил за 30'):
                redcn.generate('Квартира')

    def test_listing_analyzer_uses_absolute_runtime_under_minimal_path(self):
        completed=subprocess.CompletedProcess([],0,'{"text":"Квартира 52 м²","images":[]}', '')
        with patch.object(node_runtime,'resolve_node',return_value='/absolute/node'), \
                patch.object(redcn.subprocess,'run',return_value=completed) as run:
            result=redcn.parse_html('<div data-name="Description">Квартира</div>','https://cian.ru/1')
        self.assertEqual(result['text'],'Квартира 52 м²')
        self.assertEqual(run.call_args.args[0],['/absolute/node',str(redcn.ROOT/'cli/analyze-source.mjs'),'https://cian.ru/1'])


if __name__ == '__main__':unittest.main()
