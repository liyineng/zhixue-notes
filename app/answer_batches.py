"""Conservatively identify complete, explicitly requested attachment exercises.

This parser uses the text already supplied by read_question. It never reads a file,
recognizes an image, guesses missing exercises, or cuts a question at a page break.
"""
from __future__ import annotations

import re

_MARKERS = re.compile(
    r'(?im)^[ \t]*(?:#{1,6}[ \t]+)?(?:\[|【)?[ \t]*'
    r'(?:(?P<decimal_major>[1-9]\d{0,2})\.(?P<decimal_minor>\d{1,3})'
    r'|W[ \t]*(?P<week>[1-9]\d?)[ \t]*[-－–—][ \t]*(?P<exercise>\d{1,2}))'
    r'(?![\d.])(?:[ \t]*(?:\]|】)|(?=[ \t\r\n、:：.)）]|$))'
)
_EXPLICIT_IDENTIFIER = re.compile(r'(?i)(?:\bW\s*\d{1,2}\s*[-－–—]\s*\d{1,2}|(?<!\d)\d+\.\d+(?!\d))')
_RESTRICTED = re.compile(
    r'只|仅|单独|单题|部分|除外|除了|排除|跳过|略过|不做|不要做|不要解答|不用做|范围|'
    r'(?:前|后|末)\s*[一二三四五六七八九十百\d]+\s*(?:道|个|题)|'
    r'第\s*[一二三四五六七八九十百\d]+\s*(?:道|个)?\s*(?:题|问|页|段|节|章|部分)|'
    r'奇数题|偶数题|单数题|双数题|标星|带星|圈出|划线|'
    r'\d+\s*(?:到|至|[-~～—])\s*\d+'
)
_SHARED_OR_DEPENDENT = re.compile(
    r'(?:共用|共享|共同)(?:的)?(?:条件|题设|数据|参数|图|题图)|'
    r'(?:以下|下列|下述|所有|全部|各)\s*(?:各)?题.{0,30}(?:相同|同一|均采用|均使用|均设|条件)|'
    r'同上|条件同|(?<![当目现本])(?:上|前|后)(?:一|两|二)?(?:道)?题|'
    r'(?:第|习题|例题|题)\s*(?:\d+\.\d+|W\s*\d+[-－–—]\d+)|'
    r'(?:\d+\.\d+|W\s*\d+[-－–—]\d+)\s*(?:题|中的图)'
)
_HISTORY = re.compile(r'(?im)^\s*(?:近期对话|历史对话|历史回答|原回答|上次回答|上一条回答)\s*[（(:：]')


def _mask(text: str) -> str | None:
    """Keep source offsets while masking code and math; reject unmatched markup."""
    chars = list(text)

    def hide(start: int, end: int) -> None:
        for index in range(start, end):
            if chars[index] not in '\r\n':
                chars[index] = ' '

    fence = None
    opening = 0
    offset = 0
    for line in text.splitlines(keepends=True):
        match = re.match(r'^[ \t]*(`{3,}|~{3,})(.*)$', line.rstrip('\r\n'))
        if match:
            token = match[1]
            if fence is None:
                fence = token
                opening = offset
            elif token[0] == fence[0] and len(token) >= len(fence) and not match[2].strip():
                hide(opening, offset + len(line))
                fence = None
        offset += len(line)
    if fence is not None:
        return None
    masked = ''.join(chars)
    for match in re.finditer(r'(`+)[^\n]*?\1', masked):
        hide(match.start(), match.end())
    masked = ''.join(chars)
    current = None
    start = 0
    endings = {'\\[': '\\]', '\\(': '\\)', '$$': '$$', '$': '$'}
    for match in re.finditer(r'\\[\[\]()]|\$\$|(?<!\\)\$', masked):
        token = match[0]
        if current is None:
            if token not in endings:
                return None
            current, start = endings[token], match.start()
        elif token == current:
            hide(start, match.end())
            current = None
    return None if current is not None else ''.join(chars)


def attachment_exercises(question: str, text: str) -> list[dict[str, str]]:
    """Return safe full-attachment exercise blocks, otherwise leave the normal flow.

    Only an explicit request to solve the supplied attachment as a whole qualifies.
    At least four unique, increasing top-level IDs in one chapter/week are needed.
    Gaps are allowed: selected homework exercises need not be consecutive. The
    complete attachment prefix is repeated as shared context for every exercise.
    Ambiguous shared conditions after the first exercise decline batching rather
    than assigning them to the wrong problem or losing a dependency.
    """
    if not isinstance(question, str) or not isinstance(text, str):
        return []
    request = question.strip()
    if (not request or not text.strip() or _RESTRICTED.search(request)
            or _EXPLICIT_IDENTIFIER.search(request)):
        return []
    if not re.search(r'解答|解题|完成|做(?:完|一下|题)?|回答|讲解|解析|逐题|逐一', request):
        return []
    if not re.search(r'附件|上传|整份|这份|全部|所有|每(?:一)?道|各题|逐题|逐一', request):
        return []
    if _HISTORY.search(text):
        return []
    # Different attachments can repeat numbering or have different shared setup.
    if len(re.findall(r'(?m)^\s*附件《[^》]+》\s*[：:]', text)) > 1:
        return []
    masked = _mask(text)
    if masked is None:
        return []
    markers = list(_MARKERS.finditer(masked))
    if len(markers) < 4:
        return []
    identifiers = []
    labels = []
    for marker in markers:
        if marker['decimal_major']:
            style, major, minor = 'decimal', int(marker['decimal_major']), int(marker['decimal_minor'])
            label = f'{major}.{minor}'
        else:
            style, major, minor = 'week', int(marker['week']), int(marker['exercise'])
            label = f'W{major}-{minor:02d}'
        if minor < 1:
            return []
        identifiers.append((style, major, minor))
        labels.append(label)
    if len({(style, major) for style, major, _ in identifiers}) != 1:
        return []
    if any(previous[2] >= current[2] for previous, current in zip(identifiers, identifiers[1:])):
        return []
    # Math-only decimal lists are measurements or calculations, not clear tasks.
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(masked)
        prose = masked[marker.end():end]
        if not re.search(r'[\u3400-\u9fff]|\b(?:given|find|calculate|consider|assume|show|prove|determine|explain)\b', prose, re.I):
            return []
    # Only prose *after* each top-level ID can establish a dependency. A
    # perfectly ordinary heading such as "7.1 题目正文" is not a reference to
    # another question; neither are examples inside code or math literals.
    prose_chars = list(masked)
    for marker in markers:
        for position in range(marker.start(), marker.end()):
            if prose_chars[position] not in '\r\n':
                prose_chars[position] = ' '
    if _SHARED_OR_DEPENDENT.search(''.join(prose_chars)[markers[0].start():]):
        return []
    prefix = text[:markers[0].start()]
    result = []
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        original = text[marker.start():end]
        # Do not strip page headers, footers, equations, diagrams' text, or a
        # continuation on the following page. Every original character survives.
        result.append({'label': labels[index], 'text': prefix + original})
    return result
