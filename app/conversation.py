"""Bounded recent dialogue for resolving follow-up questions, never source evidence."""
import re

MAX_TURNS = 3
MAX_TURN_CHARS = 2000
MAX_CONTEXT_CHARS = MAX_TURNS * MAX_TURN_CHARS


def referenced_sources(question: str, exchanges: list[dict], allowed_documents: set[str]) -> list[dict]:
    """Resolve answer-local numbers from the nearest matching turn, never as page numbers."""
    numbers = set(re.findall(r'(?:\[|【)(\d+)(?:\]|】)', question))
    for match in re.finditer(r'(?:引用(?:编号)?|编号|这里)\s*((?:\d+)(?:(?:\s*[,，、和及与]\s*)\d+)*)', question):
        numbers.update(re.findall(r'\d+', match[1]))
    wanted = {int(n) for n in numbers}
    if not wanted or len(wanted) > 6:
        return []
    for exchange in reversed(exchanges):
        citations = exchange['result'].get('citations', [])
        if wanted <= {c['index'] for c in citations}:
            # Do not reinterpret the same numbers against an older, differently scoped answer.
            return [c for c in citations if c['index'] in wanted and c['document_id'] in allowed_documents]
    return []


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    marker = '\n…（上下文已截短）…\n'
    remaining = limit - len(marker)
    return text[:remaining * 2 // 3] + marker + text[-(remaining - remaining * 2 // 3):]


def recent_context(exchanges: list[dict]) -> tuple[str, int]:
    blocks = []
    for exchange in exchanges[-MAX_TURNS:]:
        result = exchange['result']
        # Old citation numbers must not masquerade as fresh source evidence.
        answer = re.sub(r'\[(\d+)\]', r'【历史引用\1】', result.get('answer', ''))
        block = f"原问题：{_clip(exchange['question'], 500)}\n"
        image_text = result.get('image_question_text', '')
        if image_text:
            block += f"原题目图片识别内容：{_clip(image_text, 400)}\n"
        block += '原回答：\n' + _clip(answer, MAX_TURN_CHARS - len(block) - len('原回答：\n\n\n'))
        blocks.append(block)
    return '\n\n'.join(blocks), len(blocks)
