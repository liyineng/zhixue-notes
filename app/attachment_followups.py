"""Resolve explicit exercise follow-ups against recent notebook attachments."""
import re
from pathlib import Path

from . import db

CODE = r'W\s*(\d{1,2})\s*[-－–—]\s*(\d{1,2})'
MARKER = re.compile(r'(?im)^[ \t]*(?:\[|【)?\s*' + CODE + r'\s*(?:\]|】)?')
MAX_PROBLEM_CHARS = 5000


def exercise_ids(question: str) -> list[str]:
    return list(dict.fromkeys(f'W{int(a)}-{int(b):02d}' for a, b in re.findall(CODE, question, re.I)))


def problem_blocks(text: str, wanted: list[str]) -> dict[str, dict]:
    markers = list(MARKER.finditer(text))
    found = {}
    for i, marker in enumerate(markers):
        key = f'W{int(marker[1])}-{int(marker[2]):02d}'
        if key not in wanted:
            continue
        end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
        block = text[marker.start():end].strip()
        # Do not include the following week/page heading in the last exercise.
        block = re.split(r'\n\s*第\s*\d+\s*(?:页|周)', block, maxsplit=1)[0].strip()
        pages = re.findall(r'第\s*(\d+)\s*页', text[:marker.start()])
        found[key] = {'exercise_id': key, 'page': int(pages[-1]) if pages else None, 'text': block}
    return found


def recover_attachment_problem(question: str, exchanges: list[dict], notebook_id: str) -> dict | None:
    wanted = exercise_ids(question)
    if not wanted:
        return None
    if len(wanted) > 6:
        raise ValueError('请一次指定不超过 6 道题，避免遗漏题目条件。')
    saw_document = False
    for exchange in reversed(exchanges):
        identities = [item['id'] for item in exchange.get('images', [])]
        identities += [item['attachment_id'] for item in exchange['result'].get('question_attachment_refs', [])]
        matches = {}
        for identity in dict.fromkeys(identities):
            attachment = db.get_question_image(identity)
            if not attachment or attachment['notebook_id'] != notebook_id or Path(attachment['path']).suffix.lower() == '.png':
                continue
            saw_document = True
            source_text = attachment['ocr_text']
            if exchange['result'].get('image_recognition_mode') == 'manual':
                source_text = exchange['result'].get('image_question_text') or source_text
            for key, block in problem_blocks(source_text, wanted).items():
                if key not in matches:
                    if not Path(attachment['path']).is_file():
                        raise ValueError('原问题附件暂不可用，请重新上传后提问。')
                    matches[key] = {**block, 'attachment_id': identity, 'name': attachment['name']}
        if all(key in matches for key in wanted):
            references = [matches[key] for key in wanted]
            text = '\n\n'.join(f"附件《{item['name']}》" + (f"第 {item['page']} 页" if item['page'] else '') +
                               f"，原始题干：\n{item['text']}" for item in references)
            if len(text) > MAX_PROBLEM_CHARS:
                raise ValueError('所指题干较长，请减少题号或重新上传对应题目页。')
            return {'text': text, 'references': [{k: v for k, v in item.items() if k != 'text'} for item in references]}
    if saw_document:
        raise ValueError('最近问题的附件中未找到完整的 ' + '、'.join(wanted) + ' 题干，请重新上传对应题目或明确附件。')
    return None
