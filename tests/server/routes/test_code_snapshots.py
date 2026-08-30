"""Route, authorization, association, and artifact tests for code snapshots."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from omnigent.db.utils import generate_agent_id
from omnigent.entities import MessageData, NewConversationItem
from omnigent.errors import OmnigentError
from omnigent.server.auth import LEVEL_EDIT, LEVEL_OWNER, LEVEL_READ, UnifiedAuthProvider
from omnigent.server.routes.sessions.routes_code_snapshots import (
    register_code_snapshot_routes,
)
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.artifact_store.local import LocalArtifactStore
from omnigent.stores.code_snapshot_store.sqlalchemy_store import (
    SqlAlchemyCodeSnapshotStore,
)
from omnigent.stores.conversation_store.sqlalchemy_store import (
    SqlAlchemyConversationStore,
)
from omnigent.stores.permission_store.sqlalchemy_store import (
    SqlAlchemyPermissionStore,
)

ALICE = "alice@example.com"
BOB = "bob@example.com"
MALLORY = "mallory@example.com"
PNG = b"\x89PNG\r\n\x1a\n" + b"snapshot-bytes"


def _app(db_uri: str, artifact_root: Path) -> tuple[FastAPI, str, LocalArtifactStore, str]:
    agent_store = SqlAlchemyAgentStore(db_uri)
    conversation_store = SqlAlchemyConversationStore(db_uri)
    permission_store = SqlAlchemyPermissionStore(db_uri)
    snapshot_store = SqlAlchemyCodeSnapshotStore(db_uri)
    artifact_store = LocalArtifactStore(str(artifact_root))
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="snapshot-agent", bundle_location="test:///bundle")
    conversation = conversation_store.create_conversation(agent_id=agent_id)
    [origin_item] = conversation_store.append(
        conversation.id,
        [
            NewConversationItem(
                type="message",
                response_id="resp_answer",
                data=MessageData(
                    role="assistant",
                    agent="snapshot-agent",
                    content=[{"type": "output_text", "text": "```python\nprint(1)\n```"}],
                ),
            )
        ],
    )
    for user, level in ((ALICE, LEVEL_OWNER), (BOB, LEVEL_READ), (MALLORY, LEVEL_EDIT)):
        permission_store.ensure_user(user)
        permission_store.grant(user, conversation.id, level)

    app = FastAPI()

    @app.exception_handler(OmnigentError)
    async def _handle_omnigent_error(request: Request, exc: OmnigentError) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    router = APIRouter()
    register_code_snapshot_routes(
        router,
        conversation_store=conversation_store,
        snapshot_store=snapshot_store,
        artifact_store=artifact_store,
        auth_provider=UnifiedAuthProvider(source="header"),
        permission_store=permission_store,
    )
    app.include_router(router, prefix="/v1")
    return app, conversation.id, artifact_store, origin_item.id


def _create(client: TestClient, session_id: str, item_id: str, *, block: int = 2):
    return client.post(
        f"/v1/sessions/{session_id}/code-snapshots",
        headers={"X-Forwarded-Email": ALICE},
        data={
            "response_id": "resp_answer",
            "item_id": item_id,
            "code_block_start_offset": str(block),
            "capture_type": "region_capture",
            "language": "python",
        },
        files={"file": ("snapshot.png", PNG, "image/png")},
    )


def test_create_list_content_and_delete_use_artifact_store(db_uri: str, tmp_path: Path) -> None:
    app, session_id, artifacts, item_id = _app(db_uri, tmp_path / "artifacts")
    client = TestClient(app)

    created = _create(client, session_id, item_id)

    assert created.status_code == 201
    body = created.json()
    assert body["response_id"] == "resp_answer"
    assert body["item_id"] == item_id
    assert body["code_block_start_offset"] == 2
    assert body["capture_type"] == "region_capture"
    assert body["language"] == "python"
    assert body["created_by"] == ALICE
    assert "artifact_key" not in body

    listed = client.get(
        f"/v1/sessions/{session_id}/code-snapshots",
        headers={"X-Forwarded-Email": BOB},
        params={
            "response_id": "resp_answer",
            "item_id": item_id,
            "code_block_start_offset": 2,
        },
    )
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["data"]] == [body["id"]]

    content = client.get(body["content_url"], headers={"X-Forwarded-Email": BOB})
    assert content.status_code == 200
    assert content.content == PNG
    assert content.headers["content-type"] == "image/png"

    stored_paths = [path for path in (tmp_path / "artifacts").rglob("*") if path.is_file()]
    assert len(stored_paths) == 1
    deleted = client.delete(
        f"/v1/sessions/{session_id}/code-snapshots/{body['id']}",
        headers={"X-Forwarded-Email": ALICE},
    )
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert not artifacts.exists(f"code_snapshots/{session_id}/{stored_paths[0].name}")


def test_multiple_snapshots_increment_only_their_own_block(db_uri: str, tmp_path: Path) -> None:
    app, session_id, _, item_id = _app(db_uri, tmp_path / "artifacts")
    client = TestClient(app)
    assert _create(client, session_id, item_id, block=2).status_code == 201
    assert _create(client, session_id, item_id, block=2).status_code == 201
    assert _create(client, session_id, item_id, block=4).status_code == 201

    def listed(block: int) -> list[dict[str, object]]:
        response = client.get(
            f"/v1/sessions/{session_id}/code-snapshots",
            headers={"X-Forwarded-Email": BOB},
            params={
                "response_id": "resp_answer",
                "item_id": item_id,
                "code_block_start_offset": block,
            },
        )
        assert response.status_code == 200
        return response.json()["data"]

    assert len(listed(2)) == 2
    assert len(listed(4)) == 1
    assert listed(3) == []


def test_read_only_can_view_but_cannot_create_or_delete(db_uri: str, tmp_path: Path) -> None:
    app, session_id, _, item_id = _app(db_uri, tmp_path / "artifacts")
    client = TestClient(app)
    created = _create(client, session_id, item_id).json()
    denied_create = client.post(
        f"/v1/sessions/{session_id}/code-snapshots",
        headers={"X-Forwarded-Email": BOB},
        data={
            "response_id": "resp_answer",
            "item_id": item_id,
            "code_block_start_offset": "2",
            "capture_type": "uploaded_image",
        },
        files={"file": ("snapshot.png", PNG, "image/png")},
    )
    denied_delete = client.delete(
        f"/v1/sessions/{session_id}/code-snapshots/{created['id']}",
        headers={"X-Forwarded-Email": BOB},
    )

    assert denied_create.status_code == 403
    assert denied_delete.status_code == 403
    assert client.get(
        created["content_url"], headers={"X-Forwarded-Email": BOB}
    ).status_code == 200


def test_user_without_conversation_access_cannot_list_or_view(db_uri: str, tmp_path: Path) -> None:
    app, session_id, _, item_id = _app(db_uri, tmp_path / "artifacts")
    client = TestClient(app)
    created = _create(client, session_id, item_id).json()
    headers = {"X-Forwarded-Email": "stranger@example.com"}

    listed = client.get(
        f"/v1/sessions/{session_id}/code-snapshots",
        headers=headers,
        params={
            "response_id": "resp_answer",
            "item_id": item_id,
            "code_block_start_offset": 2,
        },
    )
    content = client.get(created["content_url"], headers=headers)

    assert listed.status_code == 404
    assert content.status_code == 404


def test_rejects_item_not_owned_by_the_response(db_uri: str, tmp_path: Path) -> None:
    app, session_id, _, item_id = _app(db_uri, tmp_path / "artifacts")
    client = TestClient(app)

    response = client.post(
        f"/v1/sessions/{session_id}/code-snapshots",
        headers={"X-Forwarded-Email": ALICE},
        data={
            "response_id": "resp_other",
            "item_id": item_id,
            "code_block_start_offset": "2",
            "capture_type": "uploaded_image",
        },
        files={"file": ("snapshot.png", PNG, "image/png")},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "invalid originating assistant item"


def test_rejects_active_image_formats(db_uri: str, tmp_path: Path) -> None:
    app, session_id, _, item_id = _app(db_uri, tmp_path / "artifacts")
    client = TestClient(app)
    response = client.post(
        f"/v1/sessions/{session_id}/code-snapshots",
        headers={"X-Forwarded-Email": ALICE},
        data={
            "response_id": "resp_answer",
            "item_id": item_id,
            "code_block_start_offset": "2",
            "capture_type": "uploaded_image",
        },
        files={"file": ("active.svg", b"<svg><script /></svg>", "image/svg+xml")},
    )

    assert response.status_code == 415
