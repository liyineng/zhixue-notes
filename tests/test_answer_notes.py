import unittest
import test_workspace
from app import db


class AnswerNoteTests(unittest.TestCase):
    setUp = test_workspace.WorkspaceTests.setUp
    tearDown = test_workspace.WorkspaceTests.tearDown
    notebook = test_workspace.WorkspaceTests.notebook
    upload = test_workspace.WorkspaceTests.upload

    def test_saved_answer_is_idempotent_exact_and_independent_of_deleted_chat_source(self):
        notebook = self.notebook('回答笔记')
        other = self.notebook('另一笔记本')
        document = self.upload(notebook,'TCP.md','TCP 握手与挥手。')
        answer = '# TCP 时间计算\n\n**结论**：\\(T=3RTT+2MSL\\)。\n\n| 阶段 | 时间 |\n|---|---|\n| 建连 | RTT [1] |\n\n`array[1]`'
        citations = [{'index':1,'document_id':document,'document_name':'TCP.md','page':2,'chunk_id':'stable',
                      'text':'原始来源文字','lines':[{'page':2,'text':'原始来源文字','bbox':[0,0,1,1]}]}]
        db.start_exchange('save-answer','%s' % notebook,'请详细解释 TCP')
        db.finish_exchange('save-answer',{'answer':answer,'citations':citations})
        endpoint = f'/api/notebooks/{notebook}/chat/save-answer/note'
        self.assertEqual(self.client.post(f'/api/notebooks/{other}/chat/save-answer/note').status_code,404)
        response = self.client.post(endpoint)
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()['created'])
        note = response.json()['note']
        self.assertEqual(note['source_exchange_id'],'save-answer')
        self.assertEqual(note['title'],'TCP 时间计算')
        self.assertEqual(note['content'],answer)
        self.assertEqual(note['citations'],citations)
        self.assertFalse(self.client.post(endpoint).json()['created'])
        self.assertEqual(len(db.list_notes(notebook)),1)
        self.client.patch('/api/notes/'+note['id'],json={'title':'自己的标题','content':'我修改后的笔记'})
        self.assertEqual(self.client.post(endpoint).json()['note']['content'],'我修改后的笔记')
        self.client.delete(f'/api/notebooks/{notebook}/chat/save-answer')
        self.client.delete(f'/api/notebooks/{notebook}/chat/save-answer/permanent')
        self.client.delete('/api/documents/'+document)
        db.init_db()
        self.assertEqual(db.get_note(note['id'])['citations'],citations)
        exported = self.client.get('/api/notes/'+note['id']+'/export')
        self.assertIn('我修改后的笔记',exported.text)
        self.assertIn('原始来源文字',exported.text)
        self.assertEqual(len(db.list_notes(notebook)),1)
        self.assertEqual(db.list_notes(other),[])

    def test_only_completed_active_answers_can_create_notes(self):
        notebook = self.notebook('有效回答')
        db.start_exchange('pending-note',notebook,'未完成')
        for identity,result,status in [('failed-note',{'detail':'失败'},True),
                ('cancelled-note',{'answer':'部分回答','cancelled':True},False),
                ('empty-note',{'answer':'  '},False),('quiz-note',{'answer':'测验','quiz':{'questions':[]}},False),
                ('deleted-note',{'answer':'已删除'},False)]:
            db.start_exchange(identity,notebook,identity)
            db.finish_exchange(identity,result,error=status)
        self.client.delete(f'/api/notebooks/{notebook}/chat/deleted-note')
        for identity in ('pending-note','failed-note','cancelled-note','empty-note','quiz-note'):
            self.assertEqual(self.client.post(f'/api/notebooks/{notebook}/chat/{identity}/note').status_code,409)
        for identity in ('missing','deleted-note'):
            self.assertEqual(self.client.post(f'/api/notebooks/{notebook}/chat/{identity}/note').status_code,404)
        self.assertEqual(db.list_notes(notebook),[])

    def test_saving_does_not_require_source_to_remain_selected_or_present(self):
        notebook = self.notebook('历史来源')
        db.start_exchange('old-answer',notebook,'历史问题')
        citations = [{'index':101,'document_id':'previous-source','document_name':'原课件.pdf','page':7,'text':'保存的引用快照'}]
        db.finish_exchange('old-answer',{'answer':'历史回答 [101]','citations':citations})
        response = self.client.post(f'/api/notebooks/{notebook}/chat/old-answer/note')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['note']['citations'],citations)
        self.assertEqual(db.list_documents(notebook),[])
