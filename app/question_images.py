"""Decode question attachments and recognize text locally or using a vision model."""
from __future__ import annotations

import base64
import io
import shutil
import subprocess
import warnings
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
QUESTION_TRANSCRIPTION_INSTRUCTION = (
    "逐张识别题目，保留完整题干、所有已知条件、问题、选项和子图编号，公式转为 LaTeX。"
    "图表请描述坐标、数值与几何条件。"
    "电路图须逐个子图记录器件标号、端子和连接节点：准确区分晶体管 b/c/e 与场效应管 g/d/s，"
    "按原图符号、引脚及标注辨认，不能按上下左右位置猜测。"
    "为同一根连续导线连接的端子列出同一节点，并逐项说明每个电感、电容的两端分别接哪个节点；"
    "保留原图节点名称，若自行编号须说明编号仅用于描述。不要只写某个引脚接一段导线。"
    "遇到带抽头的电感或电容分压支路，分别写明两侧端点、抽头/中点及所接引脚，不能漏掉中间节点或把它短接。"
    "核对交叉导线与连接点的区别，交叉不等于相连；连接点、公共端或引脚看不清时标为[看不清]，不要补猜。"
    "不要把未画出的器件、寄生支路、接地或数值补成原图已知条件。"
    "只转写可见条件，不判断是否振荡等结论，不要解题。"
)


def normalize_image(content: bytes, target: Path) -> None:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as original:
                if original.format not in {"PNG", "JPEG", "WEBP"}:
                    raise ValueError("请上传 PNG、JPG 或 WebP 图片。")
                if original.width * original.height > MAX_IMAGE_PIXELS:
                    raise ValueError("图片超过 2000 万像素，请缩小后上传。")
                image = ImageOps.exif_transpose(original).convert("RGBA")
                background = Image.new("RGBA", image.size, "white")
                background.alpha_composite(image)
                image = background.convert("RGB")
                image.thumbnail((2400, 2400))
                image.save(target, format="PNG")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise ValueError("图片无法读取或尺寸过大，请重新选择清晰的题目图片。") from exc


def local_ocr(path: Path) -> str:
    executable = shutil.which("tesseract")
    if not executable:
        return ""
    try:
        langs = subprocess.run([executable, "--list-langs"], capture_output=True, text=True,
                               timeout=10, check=True).stdout.splitlines()
        language = "+".join(lang for lang in ("chi_sim", "eng") if lang in langs)
        if not language:
            return ""
        result = subprocess.run([executable, str(path), "stdout", "-l", language, "--psm", "3"],
                                capture_output=True, text=True, timeout=40, check=True)
        return result.stdout.strip()[:20000]
    except (OSError, subprocess.SubprocessError):
        return ""


async def read_question(images: list[dict[str, Any]], settings: dict[str, Any],
                        override: str | None = None, *, instruction: str | None = None) -> tuple[str, str]:
    if override is not None:
        if not override.strip():
            raise ValueError("请填写校对后的题目文字，或恢复自动识别。")
        return override.strip(), "manual"
    documents = [item for item in images if Path(item['path']).suffix.lower() != '.png']
    if documents:
        pictures = [item for item in images if item not in documents]
        parts = [f"附件《{item['name']}》：\n{item['ocr_text']}" for item in documents]
        mode = 'document'
        if pictures:
            text, _ = await read_question(pictures, settings, instruction=instruction)
            parts.append('题目图片：\n' + text)
            mode = 'mixed'
        text = '\n\n'.join(parts)
        if len(text) > 20000:
            raise ValueError('本次附件文字合计超过 20000 字，请减少附件或拆分文件后提问。')
        return text, mode
    if settings.get("vision_model"):
        from .llm import chat_completion

        base_url = str(settings.get("vision_base_url") or settings.get("base_url") or "")
        if not base_url:
            raise ValueError("请在模型设置中填写图片识别接口地址。")
        # A separate provider must never implicitly receive the chat provider's secret.
        key = settings.get("vision_api_key", "") if settings.get("vision_base_url") else (
            settings.get("vision_api_key") or settings.get("api_key", ""))
        content: list[dict[str, Any]] = [{"type": "text", "text": instruction or QUESTION_TRANSCRIPTION_INSTRUCTION}]
        for image in images:
            encoded = base64.b64encode(Path(image["path"]).read_bytes()).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + encoded}})
        text = await chat_completion(base_url=base_url, api_key=str(key), model=str(settings["vision_model"]),
                                     messages=[{"role": "user", "content": content}], temperature=0,
                                     max_tokens=2600, thinking=False)
        if not text:
            raise ValueError("图片识别模型没有返回题目文字，请校对后重试。")
        return text[:20000], "vision"
    text = "\n\n".join(image["ocr_text"] for image in images if image["ocr_text"].strip())
    if not text or any(not image["ocr_text"].strip() for image in images):
        raise ValueError("未识别到题目文字。请在图片下填写题目文字，或在模型设置中配置图片识别模型。")
    return text[:20000], "ocr"
