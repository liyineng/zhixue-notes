from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

ASCII_WORD = re.compile(r"[a-zA-Z0-9_]+")
CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
CHAPTER_NUMBER = r'[0-9零〇一二两三四五六七八九十百]+'


def _chapter_number(text: str) -> int:
    if text.isdigit():
        return int(text)
    digits = dict(zip('零〇一二两三四五六七八九', (0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9)))
    number, digit = 0, 0
    for char in text:
        if char in '十百':
            number += (digit or 1) * (10 if char == '十' else 100)
            digit = 0
        else:
            digit = digit * 10 + digits[char]
    return number + digit


def _ordinal_documents(question: str, documents: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    pattern = rf'第\s*({CHAPTER_NUMBER})\s*(章|讲|节|部分)'
    requested = {(_chapter_number(number), unit) for number, unit in re.findall(pattern, question)}
    for first, last, unit in re.findall(rf'第\s*({CHAPTER_NUMBER})\s*(?:章|讲|节)?\s*(?:到|至|[-—~～])\s*第?\s*({CHAPTER_NUMBER})\s*(章|讲|节)', question):
        start, end = _chapter_number(first), _chapter_number(last)
        if not 0 < start <= end < 1000:
            return []
        requested.update((number, unit) for number in range(start, end + 1))
    for count, unit in re.findall(rf'前\s*({CHAPTER_NUMBER})\s*(章|讲)', question):
        number = _chapter_number(count)
        if not 0 < number < 1000:
            return []
        requested.update((n, unit) for n in range(1, number + 1))
    if not requested:
        if re.search(r'第\s*[^，。！？\n]{1,8}?(?:章|讲|节|部分)', question):
            return []
        return None
    selected = set()
    for number, unit in requested:
        matched = [doc for doc in documents if (number, unit) in {
            (_chapter_number(value), kind) for value, kind in re.findall(pattern, doc['name'])}]
        if not matched and unit in {'章', '讲'}:
            matched = [doc for doc in documents if number in {
                int(value) for value in re.findall(r'(?<![A-Za-z0-9])[Ll]\s*0*(\d{1,3})(?!\d)', doc['name'])}]
        if not matched:
            return []  # An unresolved explicit scope must never mean all selected sources.
        selected.update(doc['id'] for doc in matched)
    return [doc for doc in documents if doc['id'] in selected]


def summary_documents(question: str, documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Resolve named lesson files only within the user's selected sources."""
    named = named_question_documents(question, documents)
    return documents if named is None else named


def named_question_documents(question: str, documents: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """Recognize explicit course filenames without confusing diagram labels with lessons."""
    ordinal = _ordinal_documents(question, documents)
    pattern = r'(?<![A-Za-z0-9])([A-Za-z]{1,5})\s*0*(\d{1,3})(?!\d)'
    prefixes = {'l'} | {prefix.casefold() for doc in documents for prefix, _ in re.findall(pattern, doc['name'])}
    codes = {(prefix.casefold(), int(number)) for prefix, number in re.findall(pattern, question) if prefix.casefold() in prefixes}
    if codes:
        return [doc for doc in documents if (ordinal is None or doc in ordinal) and codes.intersection(
            (prefix.casefold(), int(number)) for prefix, number in re.findall(pattern, doc['name']))]
    if ordinal is not None:
        return ordinal
    matched = [doc for doc in documents if doc['name'] in question or any(
        title in question for title in re.findall(r'[\u3400-\u9fff]{3,}', doc['name']))]
    return matched or None


def is_exercise_question(question: str) -> bool:
    return bool(re.search(r'(?:做|解|算)', question) and re.search(r'(?:题|练习)', question))


def is_formula_inventory(question: str) -> bool:
    """Classify the current request only; history must not change its output format."""
    if not re.search(r'公式|表达式', question):
        return False
    return bool(re.search(r'所有|全部|清单|汇总|整理|总结|列出|罗列|归纳|大全', question)
                or re.search(r'(?:有哪些|有哪几|有什么)[^，。！？\n]{0,12}(?:公式|表达式)', question)
                or re.search(r'(?:公式|表达式)(?:都|主要|分别)?有哪些[？?。\s]*$', question))


def exercise_sources(question: str, contexts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find actual labelled problem statements before keyword ranking can lose their images."""
    if not is_exercise_question(question):
        return []
    pages = {int(number) for number in re.findall(r'第\s*(\d+)\s*页', question)}
    eligible = [context for context in contexts if not pages or context.get('page') in pages]
    problems = [context for context in eligible if re.search(r'[〔【]\s*题目\s*[〕】]|(?:^|\n)\s*(?:题目|习题|练习题)\s*[:：]', context['text'])]
    if not problems:
        problems = [context for context in eligible if re.search(r'例题|练习题|习题|(?:例\s*[一二三四五六七八九\d]+)', context['text'])]
    return problems[:12]


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    lowered = text.lower()
    for match in ASCII_WORD.finditer(lowered):
        word = match.group(0)
        if len(word) >= 2:
            tokens.append(word)
    for match in CJK_RUN.finditer(lowered):
        run = match.group(0)
        if len(run) == 1:
            tokens.append(run)
            continue
        for index in range(len(run) - 1):
            tokens.append(run[index : index + 2])
    return tokens


def excerpt(text: str, query: str, limit: int = 280) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    query = re.sub(r"\s+", " ", query).strip().lower()
    terms = sorted({query, *tokenize(query)} - {""}, key=len, reverse=True)
    matches: set[int] = set()
    for term in terms:
        for match in re.finditer(re.escape(term), text.lower()):
            matches.add(max(0, match.start() - limit // 3))
    if not matches:
        return text[:limit].rstrip() + "..."
    def relevance(start: int) -> tuple[int, int]:
        window = text[start:start + limit].lower()
        # Prefer the complete query and then windows containing more distinct terms.
        return (sum(len(term) for term in terms if term in window), -start)

    start = max(matches, key=relevance)
    end = min(len(text), start + limit)
    snippet = text[start:end].strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet += "..."
    return snippet


@dataclass(slots=True)
class SearchResult:
    chunk: dict[str, Any]
    score: float


def page_sources(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reassemble page evidence from legacy chunks without changing stored uploads."""
    sources: dict[tuple[str, int], dict[str, Any]] = {}
    seen: dict[tuple[str, int], set[tuple[Any, ...]]] = {}
    unpaged = []
    for chunk in chunks:
        lines = chunk.get("lines") or []
        if chunk.get("document_kind") not in {"pdf", "pptx"} or not lines:
            unpaged.append(chunk)
            continue
        for line in lines:
            page = line.get("page")
            if page is None:
                continue
            key = (chunk["document_id"], page)
            if key not in sources:
                sources[key] = {**chunk, "page": page, "lines": []}
                seen[key] = set()
            identity = (line["text"], tuple(line.get("bbox") or []))
            if identity not in seen[key]:
                sources[key]["lines"].append(line)
                seen[key].add(identity)
    for source in sources.values():
        source["text"] = "\n".join(line["text"] for line in source["lines"])
    return [*unpaged, *sources.values()]


def quoted_evidence(context: dict[str, Any], quote: str) -> dict[str, Any] | None:
    """Accept only a literal source quote and retain the matching line coordinates."""
    quote = " ".join(quote.split())
    if not quote:
        return None
    lines = context.get("lines") or []
    text = " ".join(" ".join(line["text"].split()) for line in lines) if lines else " ".join(context["text"].split())
    start = text.find(quote)
    if start < 0:
        return None
    end = start + len(quote)
    offset = 0
    matched = []
    for line in lines:
        length = len(" ".join(line["text"].split()))
        if offset < end and offset + length > start:
            matched.append(line)
        offset += length + 1
    return {**context, "text": quote, "lines": matched}


class BM25Index:
    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self.chunks = page_sources(chunks)
        self.term_frequencies: list[Counter[str]] = []
        self.lengths: list[int] = []
        document_frequency: Counter[str] = Counter()

        for chunk in self.chunks:
            tokens = tokenize(chunk["text"])
            frequencies = Counter(tokens)
            self.term_frequencies.append(frequencies)
            self.lengths.append(max(len(tokens), 1))
            document_frequency.update(frequencies.keys())

        self.document_frequency = document_frequency
        self.average_length = (
            sum(self.lengths) / len(self.lengths) if self.lengths else 1.0
        )

    def search(
        self,
        query: str,
        top_k: int = 6,
        document_ids: set[str] | None = None,
    ) -> list[SearchResult]:
        query_tokens = set(tokenize(query))
        if not query_tokens or not self.chunks:
            return []

        total_documents = len(self.chunks)
        scores: list[SearchResult] = []
        k1 = 1.5
        b = 0.75

        for index, chunk in enumerate(self.chunks):
            if document_ids is not None and chunk["document_id"] not in document_ids:
                continue
            frequencies = self.term_frequencies[index]
            length = self.lengths[index]
            score = 0.0
            for term in query_tokens:
                frequency = frequencies.get(term, 0)
                if frequency <= 0:
                    continue
                document_frequency = self.document_frequency.get(term, 0)
                idf = math.log(
                    1.0
                    + (total_documents - document_frequency + 0.5)
                    / (document_frequency + 0.5)
                )
                denominator = frequency + k1 * (
                    1.0 - b + b * length / self.average_length
                )
                score += idf * frequency * (k1 + 1.0) / denominator
            if score > 0:
                scores.append(SearchResult(chunk=chunk, score=score))

        scores.sort(
            key=lambda item: (
                -item.score,
                item.chunk.get("document_name", ""),
                item.chunk.get("ordinal", 0),
            )
        )
        return scores if top_k == 0 else scores[: max(1, min(top_k, 20))]


def focus_source(context: dict[str, Any], query: str) -> dict[str, Any]:
    """Show the page that actually matches a source search, including old multi-page chunks."""
    pages: dict[int, list[dict[str, Any]]] = {}
    for line in context.get("lines", []):
        if line.get("page") is not None:
            pages.setdefault(line["page"], []).append(line)
    if len(pages) < 2:
        return context
    terms = set(tokenize(query))
    phrase = re.sub(r"\s+", "", query).casefold()

    def relevance(page: int) -> tuple[int, int, int]:
        text = "\n".join(line["text"] for line in pages[page]).casefold()
        exact = bool(phrase) and phrase in re.sub(r"\s+", "", text)
        return (int(exact), sum(term in text for term in terms), -page)

    page = max(pages, key=relevance)
    return {**context, "page": page, "lines": pages[page],
            "text": "\n".join(line["text"] for line in pages[page])}
