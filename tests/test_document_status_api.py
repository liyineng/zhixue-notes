import unittest
from unittest.mock import AsyncMock, patch

import test_workspace
from app import main


class DocumentStatusApiTests(unittest.TestCase):
    setUp = test_workspace.WorkspaceTests.setUp
    tearDown = test_workspace.WorkspaceTests.tearDown
    notebook = test_workspace.WorkspaceTests.notebook
    upload = test_workspace.WorkspaceTests.upload

    def test_status_and_retry_are_notebook_scoped_and_only_requested_pages_reach_recognizer(self):
        notebook = self.notebook('状态验收')
        other = self.notebook('其他课程')
        identity = self.upload(notebook, '原始资料.md', '保留原文')
        endpoint = f'/api/documents/{identity}/status'
        with patch.object(main.document_status, 'inspect_document', return_value={'pages': []}) as inspect:
            self.assertEqual(self.client.get(endpoint, params={'notebook_id': other}).status_code, 404)
            inspect.assert_not_called()
            self.assertEqual(self.client.get(endpoint, params={'notebook_id': notebook}).status_code, 200)
            self.assertEqual(inspect.call_args.args[0]['id'], identity)
        with patch.object(main.document_status, 'retry_document_pages', AsyncMock(return_value={'pages': [], 'retry': {'requested_pages': [2]}})) as retry:
            self.assertEqual(self.client.post(endpoint + '/retry', json={'notebook_id': other, 'pages': [2]}).status_code, 404)
            self.assertEqual(self.client.post(endpoint + '/retry', json={'notebook_id': notebook, 'pages': []}).status_code, 422)
            retry.assert_not_awaited()
            result = self.client.post(endpoint + '/retry', json={'notebook_id': notebook, 'pages': [2]})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(retry.call_args.args[0]['notebook_id'], notebook)
            self.assertEqual(retry.call_args.args[2], [2])

    def test_invalid_retry_is_reported_without_source_fallback(self):
        notebook = self.notebook('状态验收')
        identity = self.upload(notebook, '原始资料.md', '保留原文')
        with patch.object(main.document_status, 'retry_document_pages', AsyncMock(side_effect=ValueError('没有待识别页'))):
            result = self.client.post(f'/api/documents/{identity}/status/retry', json={'notebook_id': notebook, 'pages': [8]})
        self.assertEqual(result.status_code, 400)
        self.assertIn('没有待识别页', result.json()['detail'])
