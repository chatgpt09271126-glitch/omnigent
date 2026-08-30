"""Regression coverage for Codex ephemeral thread events during normal turns."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from omnigent import codex_native_forwarder as fwd
from omnigent.codex_native_bridge import (
    CodexNativeBridgeState,
    read_bridge_state,
    write_bridge_state,
)

PARENT_SESSION = "conv_parent"
PARENT_THREAD = "thread_persistent_parent"
REPLACEMENT_SESSION = "conv_replacement"
APP_SERVER_URL = "ws://127.0.0.1:9876"


class _RecordingAP:
    """Answer the parent snapshot request and record rotation writes."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, dict]] = []
        self.patches: list[tuple[str, dict]] = []

    async def get(self, url: str) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": PARENT_SESSION,
                "agent_id": "ag_codex_native",
                "runner_id": "runner_1",
                "labels": {},
            },
            request=httpx.Request("GET", url),
        )

    async def post(self, url: str, *, json: dict) -> httpx.Response:
        self.posts.append((url, json))
        body: dict = {"id": REPLACEMENT_SESSION} if url == "/v1/sessions" else {}
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    async def patch(self, url: str, *, json: dict) -> httpx.Response:
        self.patches.append((url, json))
        return httpx.Response(200, json={}, request=httpx.Request("PATCH", url))

    def created_sessions(self) -> list[dict]:
        return [body for url, body in self.posts if url == "/v1/sessions"]


def _make_target(ap: _RecordingAP) -> fwd._ForwarderTarget:
    return fwd._ForwarderTarget(
        session_id=PARENT_SESSION,
        thread_id=PARENT_THREAD,
        delta_coalescer=fwd._OutputTextDeltaCoalescer(ap, PARENT_SESSION),
        usage_coalescer=fwd._SessionUsageCoalescer(ap, PARENT_SESSION),
        elicitation_tracker=fwd._CodexElicitationTaskTracker(),
    )


def _seed_bridge(tmp_path: Path) -> Path:
    write_bridge_state(
        tmp_path,
        CodexNativeBridgeState(
            session_id=PARENT_SESSION,
            socket_path=APP_SERVER_URL,
            thread_id=PARENT_THREAD,
            codex_home=str(tmp_path / "codex_home"),
        ),
    )
    return tmp_path


def _thread_started(thread: dict) -> dict:
    return {"method": "thread/started", "params": {"thread": thread}}


def _ephemeral_system_thread() -> dict:
    return {
        "id": "0195aaaa-ephemeral-system-thread",
        "ephemeral": True,
        "path": None,
        "threadSource": "system",
        "source": "vscode",
        "parentThreadId": None,
        "forkedFromId": None,
    }


async def _rotate(
    ap: _RecordingAP,
    target: fwd._ForwarderTarget,
    bridge_dir: Path,
    event: dict,
) -> bool:
    return await fwd._maybe_rotate_session_on_thread_started(
        ap_client=ap,
        target=target,
        bridge_dir=bridge_dir,
        app_server_url=APP_SERVER_URL,
        event=event,
    )


async def test_ephemeral_system_thread_does_not_rotate_parent(tmp_path: Path) -> None:
    ap = _RecordingAP()
    bridge_dir = _seed_bridge(tmp_path)
    target = _make_target(ap)

    rotated = await _rotate(ap, target, bridge_dir, _thread_started(_ephemeral_system_thread()))

    assert rotated is False
    assert ap.created_sessions() == []
    assert target.session_id == PARENT_SESSION
    assert target.thread_id == PARENT_THREAD
    state = read_bridge_state(bridge_dir)
    assert state.session_id == PARENT_SESSION
    assert state.thread_id == PARENT_THREAD


async def test_real_user_clear_thread_still_rotates(tmp_path: Path) -> None:
    ap = _RecordingAP()
    target = _make_target(ap)
    clear_thread = {
        "id": "0195bbbb-real-clear-thread",
        "ephemeral": False,
        "path": "/rollout/0195bbbb.jsonl",
        "threadSource": "user",
    }

    rotated = await _rotate(ap, target, _seed_bridge(tmp_path), _thread_started(clear_thread))

    assert rotated is True
    assert len(ap.created_sessions()) == 1
    assert target.session_id == REPLACEMENT_SESSION
    assert target.thread_id == clear_thread["id"]


async def test_subagent_thread_still_ignored(tmp_path: Path) -> None:
    ap = _RecordingAP()
    target = _make_target(ap)
    subagent_thread = {
        "id": "0195cccc-subagent-thread",
        "ephemeral": False,
        "path": "/rollout/0195cccc.jsonl",
        "source": {
            "subAgent": {
                "thread_spawn": {
                    "parent_thread_id": PARENT_THREAD,
                    "turn_id": "turn_abc",
                }
            }
        },
    }

    rotated = await _rotate(ap, target, _seed_bridge(tmp_path), _thread_started(subagent_thread))

    assert rotated is False
    assert ap.created_sessions() == []


@pytest.mark.parametrize("thread_source", ["system", "vscode"])
async def test_ephemeral_thread_variants_do_not_rotate(
    tmp_path: Path,
    thread_source: str,
) -> None:
    ap = _RecordingAP()
    target = _make_target(ap)
    thread = _ephemeral_system_thread()
    thread["threadSource"] = thread_source

    rotated = await _rotate(ap, target, _seed_bridge(tmp_path), _thread_started(thread))

    assert rotated is False
    assert ap.created_sessions() == []
