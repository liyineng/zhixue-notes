"""Provider-declared context windows and conservative local input budgets."""
import hashlib
import time
from urllib.parse import urlparse

import httpx

from . import db

_cache = {}
FALLBACK_WINDOW = 32768


def estimate_tokens(text: str) -> int:
    # No compatible tokenizer is bundled for arbitrary providers. UTF-8 bytes
    # are a conservative BPE upper estimate, never an exact billed token count.
    return len(text.encode('utf-8'))


def _key(settings):
    return (str(settings.get('base_url', '')).rstrip('/'), str(settings.get('model', '')),
            hashlib.sha256(str(settings.get('api_key', '')).encode()).hexdigest())


def context_info(settings):
    manual = int(settings.get('context_window') or 0)
    if manual:
        return {'context_window': manual, 'source': 'manual'}
    cached = _cache.get(_key(settings))
    if cached and cached[0] > time.monotonic():
        return dict(cached[1])
    # Official DeepSeek models, verified against the provider's /models on
    # 2026-10-19; https://api-docs.deepseek.com/api/list-models/
    if urlparse(str(settings.get('base_url', ''))).hostname == 'api.deepseek.com' and settings.get('model') in {'deepseek-flash', 'deepseek-v4-pro'}:
        return {'context_window': 1048576, 'max_output_tokens': 393216, 'source': 'provider_documentation'}
    return {'context_window': FALLBACK_WINDOW, 'source': 'fallback'}


async def resolve_context(settings, *, force=False):
    if settings.get('context_window') or not settings.get('base_url') or not settings.get('model'):
        return context_info(settings)
    cached = _cache.get(_key(settings))
    if not force and cached and cached[0] > time.monotonic():
        return dict(cached[1])
    base = str(settings['base_url']).rstrip('/')
    if base.endswith('/chat/completions'):
        base = base[:-len('/chat/completions')]
    url = base + '/models' if base.endswith('/v1') or urlparse(base).hostname == 'api.deepseek.com' else base + '/v1/models'
    headers = {'Authorization': 'Bearer ' + str(settings['api_key'])} if settings.get('api_key') else {}
    info = context_info(settings)
    try:
        async with httpx.AsyncClient(timeout=3, follow_redirects=False) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            for model in response.json().get('data', []):
                if model.get('id') != settings['model']:
                    continue
                window = next((model[k] for k in ('context_window', 'context_length', 'max_context_length')
                               if type(model.get(k)) is int and model[k] > 0), None)
                if window:
                    info = {'context_window': window, 'source': 'provider_api'}
                    if type(model.get('max_output_tokens')) is int:
                        info['max_output_tokens'] = model['max_output_tokens']
                break
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        pass  # An unsupported models endpoint must not break ordinary questions.
    _cache[_key(settings)] = (time.monotonic() + (3600 if info['source'] == 'provider_api' else 60), info)
    return dict(info)


def source_reserve(documents):
    if not documents:
        return 0
    ids = [item['id'] for item in documents]
    with db.connection() as conn:
        rows = conn.execute('SELECT text FROM chunks WHERE document_id IN (' + ','.join('?' for _ in ids) +
                            ') ORDER BY length(text) DESC LIMIT 20', ids).fetchall()
    return sum(estimate_tokens(row['text'][:4000]) for row in rows) + estimate_tokens(str([d['name'] for d in documents])) + 4096


def history_budget(info, settings, current_text, evidence_reserve=0):
    deepseek = urlparse(str(settings.get('base_url', ''))).hostname == 'api.deepseek.com'
    output = max(16384 if deepseek else 1800, int(settings.get('max_tokens') or 1400))
    if info.get('max_output_tokens'):
        output = min(output, info['max_output_tokens'])
    # Leave room for system instructions, message framing, retrieval candidates
    # and the current question; only history is dropped when space is exhausted.
    return max(0, info['context_window'] - output - evidence_reserve - estimate_tokens(current_text) - 8192)
