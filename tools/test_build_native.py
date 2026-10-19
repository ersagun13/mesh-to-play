import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('build_native', Path(__file__).with_name('build_native.py'))


class BuildTests(unittest.TestCase):
    def load(self):
        module = importlib.util.module_from_spec(SPEC)
        SPEC.loader.exec_module(module)
        return module

    def test_windows_compiler_prefers_msvc_when_available(self):
        module = self.load()
        compilers = {'cl': 'cl.exe', 'c++': 'mingw-c++.exe'}
        with mock.patch.object(module.shutil, 'which', side_effect=lambda name: compilers.get(name)):
            self.assertEqual(module.select_compiler('Windows'), compilers['cl'])

    def test_source_integrity_rejects_changed_vendor_file(self):
        module = self.load()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'src').mkdir()
            (root / 'src' / 'part.cpp').write_text('changed')
            (root / 'UPSTREAM.json').write_text(json.dumps({'files': [
                {'path': 'src/part.cpp', 'sha256': '0' * 64}]}))
            with self.assertRaisesRegex(module.BuildError, 'checksum'):
                module.verify_sources(root)

    def test_platform_names_and_unsupported_platform(self):
        module = self.load()
        self.assertEqual(module.library_name('Darwin'), 'libmeshoptimizer.dylib')
        self.assertEqual(module.library_name('Linux'), 'libmeshoptimizer.so')
        self.assertEqual(module.library_name('Windows'), 'meshoptimizer.dll')
        with self.assertRaises(module.BuildError):
            module.library_name('Other')

    def test_invalid_library_check_fails_without_loading_parent_process(self):
        module = self.load()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'not-a-library.so'
            path.write_text('not a dynamic library')
            with self.assertRaisesRegex(module.BuildError, 'validation'):
                module.validate_in_subprocess(path)


if __name__ == '__main__':
    unittest.main()
