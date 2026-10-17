from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

ASCII_WORD = re.compile(r"[a-zA-Z0-9_]+")
CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")


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


class BM25Index:
    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self.chunks = chunks
        self.term_frequencies: list[Counter[str]] = []
        self.lengths: list[int] = []
        document_frequency: Counter[str] = Counter()

        for chunk in chunks:
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
        query_tokens = tokenize(query)
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
        return scores[: max(1, min(top_k, 20))]


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
