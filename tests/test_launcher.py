import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import launch


class LauncherTests(unittest.TestCase):
    def test_python_locations_match_platform_in_paths_with_spaces(self):
        root = Path(tempfile.gettempdir()) / 'Zhixue 中文 workspace'
        self.assertEqual(launch.environment_python(root, True), root / '.venv/Scripts/python.exe')
        self.assertEqual(launch.environment_python(root, False), root / '.venv/bin/python')

    def test_dependency_failure_does_not_write_success_stamp_or_touch_data(self):
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            python = launch.environment_python(root)
            python.parent.mkdir(parents=True)
            python.touch()
            (root / 'requirements.txt').write_text('example-package')
            (root / 'data').mkdir()
            (root / 'data/keep.db').write_bytes(b'keep')
            with patch.object(launch.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'pip')):
                with self.assertRaises(subprocess.CalledProcessError):
                    launch.prepare(root)
            self.assertFalse((root / '.venv/.requirements-sha256').exists())
            self.assertEqual((root / 'data/keep.db').read_bytes(), b'keep')

    def test_occupied_port_is_skipped(self):
        import socket
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0))
            port = occupied.getsockname()[1]
            if port < 65535:
                self.assertNotEqual(launch.free_port(port), port)
