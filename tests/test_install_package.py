import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile


ROOT=Path(__file__).resolve().parents[1]
def module(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/f'{name}.py')
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


class InstallPackageTests(unittest.TestCase):
    def test_existing_venv_launcher_does_not_invoke_broken_global_python(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); binary = root/'bin'; binary.mkdir()
            (root/'start-redcn.command').write_bytes((ROOT/'start-redcn.command').read_bytes())
            venv = root/'.venv/bin'; venv.mkdir(parents=True)
            (venv/'python').write_text('#!/bin/sh\nif [ "$1" = "-c" ]; then exit 0; fi\nprintf "%s\\n" "$@" > invoked.txt\n')
            (venv/'python').chmod(0o755)
            (binary/'python3').write_text('#!/bin/sh\nprintf broken > global-python-invoked.txt\nexit 91\n')
            (binary/'python3').chmod(0o755)
            (binary/'node').write_text('#!/bin/sh\nexit 0\n'); (binary/'node').chmod(0o755)
            result = subprocess.run(['/bin/bash', str(root/'start-redcn.command'), '--port', '8765'],
                                    env={**os.environ, 'PATH': str(binary)+os.pathsep+'/usr/bin:/bin'},
                                    cwd=root, input='', capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr+result.stdout)
            self.assertFalse((root/'global-python-invoked.txt').exists())
            self.assertEqual((root/'invoked.txt').read_text().splitlines(), ['cli/redcn.py', '--serve', '--port', '8765'])

    def test_versions_are_checked_without_running_server_or_installing_dependencies(self):
        installer=module('install-redcn')
        with patch.object(installer.subprocess,'check_output',return_value='v21.9.0'),patch.object(installer.shutil,'which',return_value='/test/node'):
            self.assertFalse(installer.install(check=True)['ready'])
        with patch.object(installer.subprocess,'check_output',return_value='v24.4.0'),patch.object(installer.shutil,'which',return_value='/test/node'),patch.object(installer.venv,'EnvBuilder') as builder:
            self.assertTrue(installer.install(check=True)['ready']);builder.assert_not_called()

    def test_package_excludes_untracked_runtime_data_and_keeps_crossplatform_launchers(self):
        pack=module('package-installer')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'repo';root.mkdir()
            subprocess.run(['git','init','-q',str(root)],check=True)
            for name in ['package.json','install-redcn.command','install-redcn.cmd','cli/redcn.py','scripts/install-redcn.py']:
                target=root/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text('fixture')
            (root/'phone-access.json').write_text('UNTRACKED_SECRET')
            (root/'install-redcn.command').chmod(0o755)
            subprocess.run(['git','add','.'],cwd=root,check=True)
            subprocess.run(['git','reset','--','phone-access.json'],cwd=root,check=True,stdout=subprocess.DEVNULL)
            destination=Path(temp)/'setup.zip';result=pack.package(root,destination)
            with zipfile.ZipFile(destination) as archive:
                self.assertIn('redcn/install-redcn.cmd',archive.namelist())
                self.assertEqual((archive.getinfo('redcn/install-redcn.command').external_attr >> 16) & 0o777, 0o755)
                self.assertNotIn('redcn/phone-access.json',archive.namelist())
                manifest=json.loads(archive.read('redcn/PACKAGE-MANIFEST.json'))
                self.assertEqual(len(manifest['files']),result['files'])
            with self.assertRaises(ValueError):pack.package(root,destination)
