"""Partition summaries by source and bounded contiguous page ranges without sampling."""
from typing import Any

SUMMARY_BATCH_CHARS = 24000


def summary_batches(contexts: list[dict[str, Any]], *, character_limit: int = SUMMARY_BATCH_CHARS) -> list[list[dict[str, Any]]]:
    documents = {}
    for context in contexts:
        documents.setdefault(context['document_id'], []).append(context)
    batches = []
    for pages in documents.values():
        batch, size = [], 0
        for page in pages:
            length = len(page['text'])
            if batch and size + length > character_limit:
                batches.append(batch)
                batch, size = [], 0
            batch.append(page)
            size += length
        if batch:
            batches.append(batch)
    return batches
