"""Bounded recent dialogue for resolving follow-up questions, never source evidence."""
import re
from .model_context import estimate_tokens



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
    if limit <= len(marker):
        return text[:max(0, limit)]
    remaining = limit - len(marker)
    return text[:remaining * 2 // 3] + marker + text[-(remaining - remaining * 2 // 3):]


def recent_context(exchanges: list[dict], token_budget: int = 8000) -> tuple[str, int]:
    blocks = []
    used = 0
    for exchange in reversed(exchanges):
        result = exchange['result']
        # Old citation numbers must not masquerade as fresh source evidence.
        answer = re.sub(r'\[(\d+)\]', r'【历史引用\1】', result.get('answer', ''))
        block = f"原问题：{exchange['question']}\n"
        image_text = result.get('image_question_text', '')
        if image_text:
            block += f"原问题附件内容：{image_text}\n"
        block += '原回答：\n' + answer
        remaining = token_budget - used - (2 if blocks else 0)
        if estimate_tokens(block) > remaining:
            if blocks or remaining < 100:
                break
            # Keep the latest turn's beginning and conclusion when it alone
            # exceeds the model budget. Do not shorten otherwise fitting turns.
            low, high = 0, len(block)
            while low < high:
                middle = (low + high + 1) // 2
                if estimate_tokens(_clip(block, middle)) <= remaining:
                    low = middle
                else:
                    high = middle - 1
            block = _clip(block, low)
        used += estimate_tokens(block) + (2 if blocks else 0)
        blocks.append(block)
    return '\n\n'.join(reversed(blocks)), len(blocks)
