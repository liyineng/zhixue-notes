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
    terms = sorted(set(tokenize(query)), key=len, reverse=True)
    matches: list[tuple[str, int]] = []
    for term in terms:
        position = text.lower().find(term.lower())
        if position >= 0:
            matches.append((term, position))
    if not matches:
        return text[:limit].rstrip() + "..."
    matches.sort(
        key=lambda item: (
            -len(item[0]),
            item[1] < 80,
            -item[1],
        )
    )
    position = matches[0][1]
    start = max(0, position - limit // 3)
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
            if document_ids and chunk["document_id"] not in document_ids:
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
