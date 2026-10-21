import tempfile
import unittest
import json
import os
import socket
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch, Mock
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

    def test_background_options_detach_from_parent_on_both_platforms(self):
        self.assertEqual(launch.background_options(False), {'start_new_session': True, 'close_fds': True})
        self.assertEqual(launch.background_options(True), {'creationflags': 0x208, 'close_fds': True})

    @unittest.skipIf(os.name == 'nt', 'POSIX socket reuse behavior')
    def test_recent_shutdown_keeps_preferred_url_while_tcp_connections_expire(self):
        with socket.socket() as server, socket.socket() as client:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(('127.0.0.1', 0)); server.listen(1)
            port = server.getsockname()[1]
            client.settimeout(2); client.connect(('127.0.0.1', port))
            connection, _ = server.accept()
            connection.close()
            self.assertEqual(client.recv(1), b'')
        self.assertEqual(launch.free_port(port), port)

    def test_recorded_fallback_port_is_reused_without_trusting_urls_or_pids(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            (runtime / 'server.json').write_text(json.dumps({'port': 8768, 'url': 'https://example.invalid', 'pid': 1}))
            with patch.object(launch, 'healthy', side_effect=lambda port: port == 8768) as health:
                self.assertEqual(launch.running_port(runtime, 8765), 8768)
                self.assertEqual([call.args[0] for call in health.call_args_list], [8765, 8768])
            (runtime / 'server.json').write_text(json.dumps({'port': 'https://example.invalid'}))
            with patch.object(launch, 'healthy', return_value=False) as health:
                self.assertIsNone(launch.running_port(runtime, 8765))
                health.assert_called_once_with(8765)

    def test_repeated_launch_reuses_service_without_installing_or_spawning(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            with (patch.object(launch, 'startup_lock', return_value=nullcontext(runtime)),
                  patch.object(launch, 'running_port', return_value=8768),
                  patch.object(launch, 'prepare') as prepare,
                  patch.object(launch.subprocess, 'Popen') as popen,
                  patch.object(launch.webbrowser, 'open') as browser):
                self.assertEqual(launch.main([]), 0)
                prepare.assert_not_called()
                popen.assert_not_called()
                browser.assert_called_once_with('http://127.0.0.1:8768')

    def test_startup_lock_serializes_launchers_and_releases_after_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with launch.startup_lock(root):
                with self.assertRaises(RuntimeError):
                    with launch.startup_lock(root, timeout=0):
                        self.fail('Second launcher entered a locked startup')
            with self.assertRaisesRegex(ValueError, 'test interruption'):
                with launch.startup_lock(root, timeout=0):
                    raise ValueError('test interruption')
            with launch.startup_lock(root, timeout=0) as runtime:
                self.assertEqual(runtime, root / 'data/runtime')

    def test_failed_child_keeps_log_and_does_not_publish_success(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            process = Mock()
            process.poll.return_value = 1
            with patch.object(launch.subprocess, 'Popen', return_value=process) as popen:
                with self.assertRaisesRegex(RuntimeError, 'server.log'):
                    launch.start_background(['python', '-m', 'example'], 8765, runtime)
                options = popen.call_args.kwargs
                self.assertEqual(options['stdin'], subprocess.DEVNULL)
                self.assertEqual(options['stderr'], subprocess.STDOUT)
                self.assertTrue(options['close_fds'])
                self.assertFalse((runtime / 'server.json').exists())
                self.assertTrue((runtime / 'server.log').exists())

    def test_detached_service_runs_with_log_after_launcher_handles_close(self):
        script = '''
from http.server import BaseHTTPRequestHandler, HTTPServer
import sys
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok":true,"service":"zhixue-notes"}')
HTTPServer(('127.0.0.1', int(sys.argv[1])), Handler).serve_forever()
'''
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
            process = launch.start_background([sys.executable, '-u', '-c', script, str(port)], port, runtime)
            try:
                self.assertTrue(launch.healthy(port))
                self.assertEqual(json.loads((runtime / 'server.json').read_text())['pid'], process.pid)
                self.assertIn(b'GET /api/health', (runtime / 'server.log').read_bytes())
                if os.name != 'nt':
                    self.assertEqual(os.getsid(process.pid), process.pid)
                    self.assertNotEqual(os.getsid(process.pid), os.getsid(0))
            finally:
                process.terminate()
                process.wait(timeout=10)
