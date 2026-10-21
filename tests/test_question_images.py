import asyncio
import unittest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.question_images import read_question


class VisionPayloadTests(unittest.TestCase):
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
