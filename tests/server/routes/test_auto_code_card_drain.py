"""Tests for ``drain_auto_code_card_tasks`` — the graceful-shutdown drain
for detached auto-code-card render tasks.

Regression coverage for a production data-loss bug: these tasks are
fire-and-forget ``asyncio.create_task`` calls fired right after an
assistant message finishes streaming. Under Railway scale-to-zero, SIGTERM
can land while they're still mid-render, killing the process before they
persist. The drain must wait for genuinely in-flight work to finish
(never cancel-first), but still bound that wait so shutdown can't hang.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from omnigent.server.routes._sessions import common as common_module


@pytest.fixture(autouse=True)
def _clean_task_set():
    common_module._auto_code_card_tasks.clear()
    yield
    common_module._auto_code_card_tasks.clear()


async def _tracked_task(coro):
    task = asyncio.ensure_future(coro)
    common_module._auto_code_card_tasks.add(task)
    task.add_done_callback(common_module._auto_code_card_tasks.discard)
    return task


@pytest.mark.asyncio
async def test_drain_waits_for_in_flight_task_to_complete():
    side_effect: list[str] = []

    async def render():
        await asyncio.sleep(0.05)
        side_effect.append("persisted")

    task = await _tracked_task(render())

    await common_module.drain_auto_code_card_tasks(timeout_seconds=5.0)

    assert task.done()
    assert not task.cancelled()
    assert side_effect == ["persisted"]


@pytest.mark.asyncio
async def test_drain_cancels_task_that_exceeds_timeout(caplog):
    async def never_finishes():
        await asyncio.sleep(10)

    task = await _tracked_task(never_finishes())

    with caplog.at_level("WARNING"):
        await common_module.drain_auto_code_card_tasks(timeout_seconds=0.05)

    # allow the cancellation to propagate into the task
    await asyncio.sleep(0)
    assert task.cancelled() or task.done()
    for _ in range(10):
        if task.cancelled():
            break
        await asyncio.sleep(0.01)
    assert task.cancelled()
    assert any("timed out" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_drain_returns_immediately_when_no_pending_tasks():
    assert not common_module._auto_code_card_tasks

    start = time.monotonic()
    await common_module.drain_auto_code_card_tasks(timeout_seconds=5.0)
    elapsed = time.monotonic() - start

    assert elapsed < 0.5
