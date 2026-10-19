"""Frontend updates must replace stale browser entry modules after ordinary refresh."""
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import main


class FrontendCacheTests(unittest.TestCase):
    def test_entry_pages_and_modules_revalidate(self):
        client = TestClient(main.app)
        for path in ['/', '/static/app.js', '/static/panel-resize.js', '/static/styles.css']:
            response = client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers.get('cache-control'), 'no-cache')
        response = client.get('/')
        self.assertRegex(response.text, r'/static/app\.js\?v=[0-9a-f]{16}')
        self.assertRegex(response.text, r'/static/styles\.css\?v=[0-9a-f]{16}')
        module = client.get('/static/app.js')
        cached = client.get('/static/app.js', headers={'If-None-Match': module.headers['etag']})
        self.assertEqual(cached.status_code, 304)
        self.assertEqual(cached.headers.get('cache-control'), 'no-cache')
        with patch.object(main, 'get_quiz', return_value={}):
            quiz = client.get('/quiz/fixture')
        self.assertEqual(quiz.headers.get('cache-control'), 'no-cache')
        self.assertRegex(quiz.text, r'/static/quiz-page\.js\?v=[0-9a-f]{16}')

    def test_imported_module_changes_update_entry_url(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'index.html').write_text('<script src="/static/app.js"></script><link href="/static/styles.css">')
            (root / 'app.js').write_text('import "/static/helper.js";')
            (root / 'helper.js').write_text('export const value = 1;')
            with patch.object(main, 'STATIC_DIR', root):
                first = main._frontend_page('index.html').body
                self.assertEqual(main._frontend_page('index.html').body, first)
                (root / 'helper.js').write_text('export const value = 2;')
                second = main._frontend_page('index.html').body
                self.assertNotEqual(first, second)
                versions = re.findall(rb'v=([0-9a-f]{16})', second)
                self.assertEqual(len(versions), 2)
                self.assertEqual(versions[0], versions[1])
