"""Track cancellable generation jobs independently of a browser connection."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException
from . import db

_active: dict[tuple[str, str], asyncio.Task] = {}
_pre_cancelled: dict[tuple[str, str], float] = {}
CANCELLED = {'detail': '已取消生成，可修改问题后重新发送。', 'cancelled': True}


def cancelled_error(exchange_id: str) -> HTTPException:
    return HTTPException(status_code=409, detail=CANCELLED['detail'],
                         headers={'X-Chat-Id': exchange_id, 'X-Generation-Cancelled': '1'})


async def run(notebook_id: str, exchange_id: str,
              operation: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
    key = (notebook_id, exchange_id)
    for stale, timestamp in list(_pre_cancelled.items()):
        if time.monotonic() - timestamp > 60:
            _pre_cancelled.pop(stale, None)
    if key in _pre_cancelled:
        _pre_cancelled.pop(key)
        raise cancelled_error(exchange_id)
    if key in _active or db.exchange_exists(exchange_id):
        raise HTTPException(status_code=409, detail='这个请求已提交，请刷新查看结果。')

    async def worker():
        try:
            return await operation()
        except asyncio.CancelledError:
            db.finish_pending_exchange(exchange_id, CANCELLED, error=True)
            raise cancelled_error(exchange_id) from None

    task = asyncio.create_task(worker())
    _active[key] = task

    def done(completed):
        if _active.get(key) is completed:
            _active.pop(key, None)
        if completed.cancelled():
            db.finish_pending_exchange(exchange_id, CANCELLED, error=True)
        else:
            completed.exception()  # Consume errors even if the original browser disconnected.

    task.add_done_callback(done)
    # Refreshing the browser should not interrupt generation; the cancel endpoint does.
    return await asyncio.shield(task)


def cancel(notebook_id: str, exchange_id: str) -> dict[str, Any]:
    key = (notebook_id, exchange_id)
    exchange = db.get_exchange(notebook_id, exchange_id)
    if exchange and exchange['status'] != 'pending':
        return {'cancelled': bool(exchange['result'].get('cancelled')), 'status': exchange['status']}
    if exchange:
        db.finish_pending_exchange(exchange_id, CANCELLED, error=True)
    task = _active.get(key)
    if task:
        task.cancel()
    elif not exchange:
        if db.exchange_exists(exchange_id):
            raise HTTPException(status_code=404, detail='这个请求不属于当前笔记本或已删除。')
        if len(_pre_cancelled) >= 1000:
            _pre_cancelled.pop(next(iter(_pre_cancelled)))
        _pre_cancelled[key] = time.monotonic()
    return {'cancelled': True, 'chat_id': exchange_id}
