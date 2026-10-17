from __future__ import annotations

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
APP_DIR = BASE_DIR / "app"
STATIC_DIR = APP_DIR / "static"
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
QUESTION_IMAGE_DIR = DATA_DIR / "question-images"
DB_PATH = DATA_DIR / "app.db"

SUPPORTED_SUFFIXES = {".pdf", ".md", ".markdown", ".txt", ".docx", ".pptx"}
MAX_UPLOAD_BYTES = 120 * 1024 * 1024
CHUNK_SIZE = 900
CHUNK_OVERLAP = 140


def ensure_directories() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    QUESTION_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
