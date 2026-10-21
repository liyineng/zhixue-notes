import asyncio
import unittest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.question_images import read_question


class VisionPayloadTests(unittest.TestCase):
    def recognize(self, instruction=None):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / 'question.png'
            image.write_bytes(b'fixture-image-bytes')
            completion = AsyncMock(return_value='题干及各子图的节点连接表')
            with patch('app.llm.chat_completion', completion):
                asyncio.run(read_question([{'path': str(image), 'ocr_text': ''}], {
                    'base_url': 'https://vision.example/v1', 'vision_model': 'vision-test'}, instruction=instruction))
            return completion.call_args.kwargs

    def test_default_transcription_requests_visible_circuit_topology_without_solving(self):
        prompt = self.recognize()['messages'][0]['content'][0]['text']
        for condition in ['子图编号', '器件标号', 'b/c/e', 'g/d/s', '同一节点', '两端',
                          '抽头/中点', '交叉不等于相连', '[看不清]', '未画出的器件', '寄生支路', '不要解题']:
            self.assertIn(condition, prompt)
        self.assertIn('编号仅用于描述', prompt)
        self.assertIn('不能按上下左右位置猜测', prompt)

    def test_custom_document_recognition_instruction_is_preserved(self):
        instruction = '只提取本页公式与原有解答，逐字保留，不采用题目转写模板。'
        kwargs = self.recognize(instruction)
        self.assertEqual(kwargs['messages'][0]['content'][0]['text'], instruction)
        self.assertEqual(kwargs['temperature'], 0)
        self.assertFalse(kwargs['thinking'])

    def test_vision_uses_image_payload_and_does_not_leak_chat_key_to_another_provider(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "image.png"
            image.write_bytes(b"fixture-image-bytes")
            completion = AsyncMock(return_value="识别出的题目")
            with patch("app.llm.chat_completion", completion):
                text, mode = asyncio.run(read_question([{"path": str(image), "ocr_text": ""}], {
                    "base_url": "https://chat.example/v1", "api_key": "chat-provider-secret",
                    "vision_model": "vision-test", "vision_base_url": "https://vision.example/v1"}))
            self.assertEqual((text, mode), ("识别出的题目", "vision"))
            kwargs = completion.call_args.kwargs
            self.assertEqual(kwargs["api_key"], "")
            self.assertEqual(kwargs["messages"][0]["content"][1]["type"], "image_url")
            self.assertTrue(kwargs["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))


if __name__ == "__main__":
    unittest.main()
