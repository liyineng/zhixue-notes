"""Small, source-scoped descriptions of inputs actually used by each stage."""
from typing import Any


def source_activity(items: list[dict[str, Any]], query: str = '') -> dict[str, Any]:
    grouped = {}
    for item in items:
        identity = item.get('document_id') or item['id']
        source = grouped.setdefault(identity, {'name': item.get('document_name') or item['name'], 'pages': set(), 'fragments': 0})
        if item.get('page') is not None:
            source['pages'].add(item['page'])
        if 'text' in item:
            source['fragments'] += 1
    sources = []
    for source in list(grouped.values())[:12]:
        pages = sorted(source['pages'])
        sources.append({**source, 'pages': pages[:12], 'page_count': len(pages)})
    return {'sources': sources, 'source_count': len(grouped), 'query': query[:240] + ('…' if len(query) > 240 else '')}
