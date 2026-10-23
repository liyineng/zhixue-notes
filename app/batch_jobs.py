"""Validated batch checkpoints stored with their notebook-scoped exchange."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from . import db


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def begin(exchange_id: str, kind: str, payload: dict, previous: dict | None = None) -> None:
    with db.connection() as conn:
        row = conn.execute("SELECT result_json FROM chat_exchanges WHERE id=? AND status='pending'", (exchange_id,)).fetchone()
        if row:
            result = json.loads(row[0])
            retained = result.get('generation_job') or previous or {}
            job = {'kind': kind, 'payload': payload, 'batches': retained.get('batches', {}),
                   'total': retained.get('total', 0), 'phases': retained.get('phases', {})}
            result['generation_job'] = job
            conn.execute('UPDATE chat_exchanges SET result_json=? WHERE id=? AND status=\'pending\'',
                         (json.dumps(result, ensure_ascii=False), exchange_id))


class Checkpoints:
    def __init__(self, exchange_id: str, settings: dict, requirement: Any, batches: list[list[dict]],
                 *, phase: str | None = None):
        self.exchange_id = exchange_id
        self.phase = phase
        # Keys never contain credentials. Model/context changes invalidate reuse.
        config = {key: settings.get(key) for key in ('base_url', 'model', 'temperature', 'max_tokens', 'context_window')}
        self.keys = [fingerprint([2, config, requirement, batch]) for batch in batches]
        self.saved: dict = {}
        with db.connection() as conn:
            row = conn.execute("SELECT result_json FROM chat_exchanges WHERE id=? AND status='pending'", (exchange_id,)).fetchone()
            if not row:
                return
            result = json.loads(row[0]); job = result.get('generation_job')
            if not job:
                return
            store = job if phase is None else job.setdefault('phases', {}).setdefault(phase, {})
            self.saved = {key: value for key, value in store.get('batches', {}).items() if key in self.keys}
            store.update(batches=self.saved, total=len(batches))
            conn.execute('UPDATE chat_exchanges SET result_json=? WHERE id=? AND status=\'pending\'',
                         (json.dumps(result, ensure_ascii=False), exchange_id))

    def get(self, index: int) -> Any:
        return self.saved.get(self.keys[index])

    def save(self, index: int, value: Any) -> None:
        with db.connection() as conn:
            row = conn.execute("SELECT result_json FROM chat_exchanges WHERE id=? AND status='pending'", (self.exchange_id,)).fetchone()
            if not row:
                return  # Cancellation/deletion cannot be overwritten by a late batch.
            result = json.loads(row[0]); job = result.get('generation_job')
            if not job:
                return
            store = job if self.phase is None else job.get('phases', {}).get(self.phase)
            if store is None:
                return
            store['batches'][self.keys[index]] = value
            conn.execute('UPDATE chat_exchanges SET result_json=? WHERE id=? AND status=\'pending\'',
                         (json.dumps(result, ensure_ascii=False), self.exchange_id))
        self.saved[self.keys[index]] = value
