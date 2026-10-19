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

    def test_deleted_panel_lists_only_own_records_and_restores_selected_snapshot(self):
        notebook = self.notebook('选择恢复')
        other = self.notebook('隔离')
        image = main.QUESTION_IMAGE_DIR / 'restore-panel.png'
        image.write_bytes(b'image-fixture')
        db.insert_question_image({'id':'restore-file', 'notebook_id':notebook, 'name':'题目.png',
                                  'path':str(image), 'ocr_text':'完整题干'})
        snapshot = {'id':'restore-file', 'name':'题目.png', 'url':'/api/question-images/restore-file/file'}
        db.start_exchange('selected-older', notebook, '较早删除的问题', [snapshot], {'answer':'引用的回答快照'})
        original = {'answer':'详细答案 [1]', 'citations':[{'index':1, 'document_id':'source', 'page':4, 'text':'原文'}], 'image_question_text':'完整题干'}
        db.finish_exchange('selected-older', original)
        self.exchange(notebook, 'newer')
        self.exchange(notebook, 'active')
        self.exchange(other, 'foreign-deleted')
        url = f'/api/notebooks/{notebook}/chat'
        self.client.delete(url + '/selected-older')
        self.client.delete(url + '/newer')
        self.client.delete(f'/api/notebooks/{other}/chat/foreign-deleted')
        db.init_db()
        response = self.client.get(url + '/deleted')
        self.assertEqual(response.status_code, 200, response.text)
        deleted = response.json()['exchanges']
        self.assertEqual(response.json()['retention_limit'], 5)
        self.assertEqual([item['id'] for item in deleted], ['newer', 'selected-older'])
        self.assertEqual(deleted[1]['result'], original)
        self.assertEqual(deleted[1]['images'], [snapshot])
        self.assertEqual(deleted[1]['reply_to']['answer'], '引用的回答快照')
        self.assertEqual(self.client.post(f'/api/notebooks/{other}/chat/selected-older/restore').status_code, 404)
        self.assertEqual(self.client.post(url + '/active/restore').status_code, 404)
        restored = self.client.post(url + '/selected-older/restore')
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()['deleted_count'], 1)
        self.assertEqual(restored.json()['chat_id'], 'selected-older')
        self.assertEqual(self.client.post(url + '/selected-older/restore').status_code, 404)
        self.assertEqual(db.get_exchange(notebook, 'selected-older')['result'], original)
        self.assertEqual(db.get_exchange(notebook, 'selected-older')['reply_to']['answer'], '引用的回答快照')
        self.assertTrue(image.exists())
        self.assertEqual([item['id'] for item in self.client.get(url + '/deleted').json()['exchanges']], ['newer'])
        self.assertEqual(self.client.post(url + '/undo').json()['chat_id'], 'newer')
        self.assertEqual(self.client.get(url + '/deleted').json()['exchanges'], [])

    def test_deleted_panel_never_exposes_expired_sixth_record(self):
        notebook = self.notebook('删除记录上限')
        url = f'/api/notebooks/{notebook}/chat'
        for index in range(7):
            self.exchange(notebook, f'record-{index}')
            self.client.delete(url + f'/record-{index}')
        self.assertEqual([item['id'] for item in self.client.get(url + '/deleted').json()['exchanges']],
                         [f'record-{index}' for index in range(6, 1, -1)])
        self.assertEqual(self.client.post(url + '/record-0/restore').status_code, 404)
        self.client.delete(url)
        self.assertEqual(self.client.get(url + '/deleted').json()['exchanges'], [])

    def test_permanent_delete_preserves_shared_attachments_and_other_records(self):
        notebook = self.notebook('永久删除')
        other = self.notebook('其他笔记本')
        images = {}
        for identity in ('unique-purge', 'shared-active', 'shared-deleted', 'unrelated-file'):
            path = main.QUESTION_IMAGE_DIR / f'{identity}.png'
            path.write_bytes(b'local-file')
            db.insert_question_image({'id':identity, 'notebook_id':notebook, 'name':path.name,
                                      'path':str(path), 'ocr_text':'题目'})
            images[identity] = path
        self.exchange(notebook, 'purge-target', [{'id':key} for key in ('unique-purge', 'shared-active', 'shared-deleted')])
        self.exchange(notebook, 'keep-active', [{'id':'shared-active'}])
        self.exchange(notebook, 'keep-deleted', [{'id':'shared-deleted'}])
        url = f'/api/notebooks/{notebook}/chat'
        self.client.delete(url + '/purge-target')
        self.client.delete(url + '/keep-deleted')
        self.assertEqual(self.client.delete(f'/api/notebooks/{other}/chat/purge-target/permanent').status_code, 404)
        self.assertEqual(self.client.delete(url + '/keep-active/permanent').status_code, 404)
        response = self.client.delete(url + '/purge-target/permanent')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['permanent'])
        self.assertEqual(response.json()['deleted_image_ids'], ['unique-purge'])
        self.assertEqual(response.json()['deleted_count'], 1)
        self.assertFalse(images['unique-purge'].exists())
        self.assertIsNone(db.get_question_image('unique-purge'))
        for identity in ('shared-active', 'shared-deleted', 'unrelated-file'):
            self.assertTrue(images[identity].exists())
            self.assertIsNotNone(db.get_question_image(identity))
        self.assertEqual([item['id'] for item in self.client.get(url + '/deleted').json()['exchanges']], ['keep-deleted'])
        self.assertEqual(self.client.post(url + '/purge-target/restore').status_code, 404)
        self.assertEqual(self.client.delete(url + '/purge-target/permanent').status_code, 404)
        db.init_db()
        self.assertEqual(self.client.post(url + '/undo').json()['chat_id'], 'keep-deleted')
        self.assertIsNotNone(db.get_exchange(notebook, 'keep-active'))
        self.assertFalse(db.exchange_exists('purge-target'))

    def test_permanent_delete_rejects_pending_active_and_restored_records(self):
        notebook = self.notebook('拒绝误删')
        url = f'/api/notebooks/{notebook}/chat'
        db.start_exchange('still-pending', notebook, '生成中')
        self.exchange(notebook, 'restored-record')
        self.client.delete(url + '/restored-record')
        self.client.post(url + '/restored-record/restore')
        for identity in ('still-pending', 'restored-record', 'missing'):
            self.assertEqual(self.client.delete(url + f'/{identity}/permanent').status_code, 404)
        self.assertIsNotNone(db.get_exchange(notebook, 'still-pending'))
        self.assertIsNotNone(db.get_exchange(notebook, 'restored-record'))

    def test_clear_deleted_records_is_atomic_scoped_and_keeps_active_files(self):
        notebook = self.notebook('清空删除记录')
        other = self.notebook('保留其他笔记本')
        document = self.upload(notebook, '课件.md', '保留来源正文')
        note = self.client.post('/api/notes', json={'notebook_id':notebook,'title':'保留笔记','content':'正文'}).json()['note']
        paths = {}
        for identity in ('trash-only-file', 'shared-with-active', 'unused-file'):
            path = main.QUESTION_IMAGE_DIR / f'{identity}.png'; path.write_bytes(b'local-attachment')
            db.insert_question_image({'id':identity,'notebook_id':notebook,'name':path.name,'path':str(path),'ocr_text':'题干'})
            paths[identity] = path
        self.exchange(notebook, 'trash-a', [{'id':'trash-only-file'},{'id':'shared-with-active'}])
        self.exchange(notebook, 'trash-b', [{'id':'trash-only-file'}])
        self.exchange(notebook, 'active-chat', [{'id':'shared-with-active'}])
        db.start_exchange('pending-chat', notebook, '正在生成的问题')
        self.exchange(other, 'foreign-trash')
        url = f'/api/notebooks/{notebook}/chat'
        self.client.delete(url + '/trash-a'); self.client.delete(url + '/trash-b')
        self.client.delete(f'/api/notebooks/{other}/chat/foreign-trash')
        response = self.client.delete(url + '/deleted/all')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['purged_count'], 2)
        self.assertEqual(response.json()['deleted_count'], 0)
        self.assertEqual(response.json()['deleted_image_ids'], ['trash-only-file'])
        self.assertFalse(paths['trash-only-file'].exists())
        self.assertTrue(paths['shared-with-active'].exists())
        self.assertTrue(paths['unused-file'].exists())
        self.assertIsNotNone(db.get_exchange(notebook,'active-chat'))
        self.assertIsNotNone(db.get_exchange(notebook,'pending-chat'))
        self.assertIsNotNone(db.get_document(document))
        self.assertIsNotNone(db.get_note(note['id']))
        self.assertEqual(len(db.list_deleted_exchanges(other)), 1)
        self.assertEqual(self.client.get(url + '/deleted').json()['exchanges'], [])
        self.assertEqual(self.client.post(url + '/undo').status_code, 404)
        self.assertEqual(self.client.post(url + '/trash-a/restore').status_code, 404)
        db.init_db()
        self.assertEqual(self.client.delete(url + '/deleted/all').json()['purged_count'], 0)
        self.assertEqual(self.client.delete('/api/notebooks/missing-notebook/chat/deleted/all').status_code, 404)
