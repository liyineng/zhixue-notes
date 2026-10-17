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
                        override: str | None = None) -> tuple[str, str]:
    if override is not None:
        if not override.strip():
            raise ValueError("请填写校对后的题目文字，或恢复自动识别。")
        return override.strip(), "manual"
    if settings.get("vision_model"):
        from .llm import chat_completion

        base_url = str(settings.get("vision_base_url") or settings.get("base_url") or "")
        if not base_url:
            raise ValueError("请在模型设置中填写图片识别接口地址。")
        # A separate provider must never implicitly receive the chat provider's secret.
        key = settings.get("vision_api_key", "") if settings.get("vision_base_url") else (
            settings.get("vision_api_key") or settings.get("api_key", ""))
        content: list[dict[str, Any]] = [{"type": "text", "text":
            "逐张识别题目，保留所有已知条件、问题和选项。公式转为 LaTeX。"
            "图表请描述坐标、连接关系、数值与几何条件。看不清的位置标为[看不清]，不要补猜，也不要解题。"}]
        for image in images:
            encoded = base64.b64encode(Path(image["path"]).read_bytes()).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + encoded}})
        text = await chat_completion(base_url=base_url, api_key=str(key), model=str(settings["vision_model"]),
                                     messages=[{"role": "user", "content": content}], temperature=0,
                                     max_tokens=2600)
        if not text:
            raise ValueError("图片识别模型没有返回题目文字，请校对后重试。")
        return text[:20000], "vision"
    text = "\n\n".join(image["ocr_text"] for image in images if image["ocr_text"].strip())
    if not text or any(not image["ocr_text"].strip() for image in images):
        raise ValueError("未识别到题目文字。请在图片下填写题目文字，或在模型设置中配置图片识别模型。")
    return text[:20000], "ocr"
