"""Validated batch checkpoints stored with their notebook-scoped exchange."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from . import db


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def begin(exchange_id: str, kind: str, payload: dict, previous: dict | None = None) -> None:
    job = {'kind': kind, 'payload': payload, 'batches': (previous or {}).get('batches', {}), 'total': 0}
    with db.connection() as conn:
        row = conn.execute("SELECT result_json FROM chat_exchanges WHERE id=? AND status='pending'", (exchange_id,)).fetchone()
        if row:
            result = json.loads(row[0]); result['generation_job'] = job
            conn.execute('UPDATE chat_exchanges SET result_json=? WHERE id=? AND status=\'pending\'',
                         (json.dumps(result, ensure_ascii=False), exchange_id))


class Checkpoints:
    def __init__(self, exchange_id: str, settings: dict, requirement: Any, batches: list[list[dict]]):
        self.exchange_id = exchange_id
        # Keys never contain credentials. Model/context changes invalidate reuse.
        config = {key: settings.get(key) for key in ('base_url', 'model', 'temperature', 'max_tokens')}
        self.keys = [fingerprint([2, config, requirement, batch]) for batch in batches]
        self.saved: dict = {}
        with db.connection() as conn:
            row = conn.execute("SELECT result_json FROM chat_exchanges WHERE id=? AND status='pending'", (exchange_id,)).fetchone()
            if not row:
                return
            result = json.loads(row[0]); job = result.get('generation_job')
            if not job:
                return
            self.saved = {key: value for key, value in job.get('batches', {}).items() if key in self.keys}
            job.update(batches=self.saved, total=len(batches))
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
            job['batches'][self.keys[index]] = value
            conn.execute('UPDATE chat_exchanges SET result_json=? WHERE id=? AND status=\'pending\'',
                         (json.dumps(result, ensure_ascii=False), self.exchange_id))
        self.saved[self.keys[index]] = value
