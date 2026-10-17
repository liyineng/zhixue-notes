import unittest

from app import db, main
import test_workspace


class ChatDeleteTests(unittest.TestCase):
    setUp = test_workspace.WorkspaceTests.setUp
    tearDown = test_workspace.WorkspaceTests.tearDown
    notebook = test_workspace.WorkspaceTests.notebook
    upload = test_workspace.WorkspaceTests.upload
    def exchange(self, notebook, identity, images=None, error=False):
        db.start_exchange(identity, notebook, identity, images)
        db.finish_exchange(identity, {'detail': '失败示例'} if error else {'answer': '回答', 'citations': []}, error=error)

    def test_delete_and_restore_preserve_other_questions_notes_and_sources(self):
        notebook = self.notebook('删除验收')
        other = self.notebook('另一个笔记本')
        document = self.upload(notebook, '来源.md', '网络协议')
        note = self.client.post('/api/notes', json={'notebook_id': notebook, 'title': '已保存', 'content': '保留正文'}).json()['note']
        self.exchange(notebook, 'first')
        self.exchange(notebook, 'second', error=True)
        url = f'/api/notebooks/{notebook}/chat'
        self.assertEqual(self.client.delete(f'/api/notebooks/{other}/chat/first').status_code, 404)
        self.assertEqual(self.client.delete(url + '/first').status_code, 200)
        self.assertEqual(self.client.delete(url + '/first').status_code, 404)
        self.assertEqual([e['id'] for e in db.list_exchanges(notebook)], ['second'])
        self.assertEqual(self.client.get(url).json()['deleted_count'], 1)
        self.assertIsNotNone(db.get_note(note['id']))
        self.assertIsNotNone(db.get_document(document))
        db.init_db()
        self.assertEqual(self.client.post(f'/api/notebooks/{other}/chat/undo').status_code, 404)
        restored = self.client.post(url + '/undo')
        self.assertEqual(restored.json()['chat_id'], 'first')
        self.assertEqual([e['id'] for e in db.list_exchanges(notebook)], ['first', 'second'])
        self.assertEqual(self.client.post(url + '/undo').status_code, 404)
        self.assertEqual(self.client.delete(url + '/second').status_code, 200)
        self.assertEqual(self.client.post(url + '/undo').json()['chat_id'], 'second')
        self.assertEqual(db.list_exchanges(notebook)[1]['status'], 'error')

    def test_only_five_deletions_retained_and_shared_images_survive_purge(self):
        notebook = self.notebook('五条删除记录')
        shared = main.QUESTION_IMAGE_DIR / 'shared.png'
        unique = main.QUESTION_IMAGE_DIR / 'unique.png'
        for identity, path in [('shared', shared), ('unique', unique)]:
            path.write_bytes(b'image-fixture')
            db.insert_question_image({'id': identity, 'notebook_id': notebook, 'name': path.name,
                                      'path': str(path), 'ocr_text': '题目'})
        for i in range(1, 8):
            images = [{'id': 'shared'}] if i in [1, 6] else [{'id': 'unique'}] if i == 2 else []
            self.exchange(notebook, f'exchange-{i}', images)
        url = f'/api/notebooks/{notebook}/chat'
        for i in range(1, 8):
            result = self.client.delete(f'{url}/exchange-{i}')
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()['deleted_count'], min(i, 5))
        self.assertEqual(db.list_exchanges(notebook), [])
        self.assertEqual(len(db.list_exchanges(notebook, include_deleted=True)), 5)
        self.assertTrue(shared.exists())
        self.assertFalse(unique.exists())
        self.assertIsNone(db.get_question_image('unique'))
        for i in range(7, 2, -1):
            self.assertEqual(self.client.post(url + '/undo').json()['chat_id'], f'exchange-{i}')
        self.assertEqual(self.client.post(url + '/undo').status_code, 404)
        self.assertEqual(len(db.list_exchanges(notebook)), 5)
        self.assertIsNotNone(db.get_question_image('shared'))
        self.client.delete(url + '/exchange-6')
        # A draft removal may not break an image in retained deleted history.
        self.assertTrue(self.client.delete('/api/question-images/shared').json()['retained_in_history'])
        self.client.delete(url)
        self.assertFalse(shared.exists())
        self.assertEqual(db.deleted_exchange_count(notebook), 0)

    def test_pending_question_cannot_be_deleted(self):
        notebook = self.notebook('正在生成')
        db.start_exchange('pending-delete-test', notebook, '问题')
        result = self.client.delete(f'/api/notebooks/{notebook}/chat/pending-delete-test')
        self.assertEqual(result.status_code, 409)
        self.assertEqual(db.list_exchanges(notebook)[0]['status'], 'pending')
