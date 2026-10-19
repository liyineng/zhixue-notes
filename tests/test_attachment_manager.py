"""Manage question files without touching notebook sources or historical snapshots."""
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
import test_question_attachments
from app import db, main


class AttachmentManagerTests(unittest.TestCase):
    setUp = test_question_attachments.QuestionAttachmentTests.setUp
    tearDown = test_question_attachments.QuestionAttachmentTests.tearDown
    upload = test_question_attachments.QuestionAttachmentTests.upload

    def test_listing_detail_rename_and_delete_unused_are_notebook_scoped(self):
        item = self.upload('题目.md', b'Original question body').json()['image']
        other = self.client.post('/api/notebooks', json={'name': '另一课程'}).json()['notebook']['id']
        endpoint = '/api/notebooks/default/attachments'
        listed = self.client.get(endpoint).json()['attachments']
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]['id'], item['id'])
        self.assertTrue(listed[0]['available'])
        self.assertEqual(listed[0]['size_bytes'], len(b'Original question body'))
        self.assertEqual(listed[0]['exchanges'], [])
        self.assertNotIn('ocr_text', listed[0])
        self.assertNotIn('path', listed[0])
        self.assertEqual(self.client.get(f'/api/notebooks/{other}/attachments').json()['attachments'], [])
        foreign = f'/api/notebooks/{other}/attachments/' + item['id']
        self.assertEqual(self.client.get(foreign).status_code, 404)
        self.assertEqual(self.client.patch(foreign, json={'name': '改名.md'}).status_code, 404)
        self.assertEqual(self.client.delete(foreign).status_code, 404)
        endpoint += '/' + item['id']
        for name in ['', ' ', '../bad.md', 'bad\\path.md', 'wrong.exe', 'line\nbreak.md']:
            self.assertIn(self.client.patch(endpoint, json={'name':name}).status_code, (400, 422))
        renamed = self.client.patch(endpoint, json={'name':'作业第一周.md'})
        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.assertEqual(self.client.get(endpoint).json()['attachment']['name'], '作业第一周.md')
        self.assertEqual(self.client.get(endpoint).json()['attachment']['ocr_text'], 'Original question body')
        self.assertEqual(self.client.get(item['url']).content, b'Original question body')
        path = Path(db.get_question_image(item['id'])['path'])
        self.assertTrue(self.client.delete(endpoint).json()['deleted'])
        self.assertFalse(path.exists())
        self.assertEqual(self.client.get(endpoint).status_code, 404)
        self.assertEqual(self.client.get('/api/notebooks/default/attachments').json()['attachments'], [])
        self.assertEqual(db.list_documents('default'), [])

    def test_used_files_can_be_reused_but_cannot_be_deleted_even_after_undo_delete(self):
        item = self.upload('习题.txt', b'TCP exercise complete conditions').json()['image']
        db.start_exchange('original', 'default', '做这道题', [item])
        db.finish_exchange('original', {'answer': '原回答'})
        endpoint = '/api/notebooks/default/attachments/' + item['id']
        self.assertEqual(self.client.delete(endpoint).status_code, 409)
        self.client.patch(endpoint, json={'name':'TCP题目.txt'})
        self.assertEqual(db.get_exchange('default', 'original')['images'][0]['name'], '习题.txt')
        planner = AsyncMock(return_value={'intent':'question', 'needs_retrieval':False, 'search_query':'TCP', 'keywords':[]})
        answer = AsyncMock(return_value='新回答')
        with patch.object(main, 'analyze_question', planner), patch.object(main, 'answer_question', answer):
            response = self.client.post('/api/ask', json={'question':'再做一次', 'document_ids':[], 'image_ids':[item['id']]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('TCP exercise complete conditions', answer.call_args.args[0])
        self.assertEqual(answer.call_args.args[1], [])
        new_id = response.json()['chat_id']
        listed = self.client.get('/api/notebooks/default/attachments').json()['attachments'][0]
        self.assertEqual(len(listed['exchanges']), 2)
        self.client.delete('/api/notebooks/default/chat/original')
        self.client.delete('/api/notebooks/default/chat/' + new_id)
        self.assertEqual(self.client.delete(endpoint).status_code, 409)
        listed = self.client.get('/api/notebooks/default/attachments').json()['attachments'][0]
        self.assertTrue(all(exchange['deleted_at'] for exchange in listed['exchanges']))
        self.assertEqual(self.client.get(item['url']).status_code, 200)
        self.client.post('/api/notebooks/default/chat/undo')
        self.assertIsNotNone(db.get_exchange('default', new_id))

    def test_missing_original_is_reported_and_unused_metadata_can_be_removed(self):
        item = self.upload('旧题.txt', b'Old question').json()['image']
        Path(db.get_question_image(item['id'])['path']).unlink()
        listed = self.client.get('/api/notebooks/default/attachments').json()['attachments'][0]
        self.assertFalse(listed['available'])
        self.assertEqual(listed['size_bytes'], 0)
        self.assertEqual(self.client.get('/api/notebooks/default/attachments/' + item['id']).json()['attachment']['ocr_text'], 'Old question')
        self.assertTrue(self.client.delete('/api/notebooks/default/attachments/' + item['id']).json()['deleted'])
