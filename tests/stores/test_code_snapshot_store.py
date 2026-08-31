"""Focused persistence tests for per-code-block snapshot metadata."""

from __future__ import annotations

import pytest

from omnigent.stores.code_snapshot_store.sqlalchemy_store import (
    SqlAlchemyCodeSnapshotStore,
)


@pytest.fixture()
def store(db_uri: str) -> SqlAlchemyCodeSnapshotStore:
    return SqlAlchemyCodeSnapshotStore(db_uri)


def _add(
    store: SqlAlchemyCodeSnapshotStore,
    *,
    conversation_id: str,
    item_id: str,
    block: int,
    capture_type: str = "region_capture",
    page_index: int = 0,
    artifact_suffix: str | None = None,
):
    suffix = artifact_suffix if artifact_suffix is not None else f"{capture_type}-{page_index}"
    return store.add(
        conversation_id=conversation_id,
        response_id="resp_1",
        item_id=item_id,
        code_block_start_offset=block,
        language="python",
        created_by="alice@example.com",
        capture_type=capture_type,  # type: ignore[arg-type]
        artifact_key=f"code_snapshots/{conversation_id}/{item_id}-{block}-{suffix}",
        content_type="image/png",
        bytes=42,
        page_index=page_index,
    )


def test_multiple_snapshots_round_trip_for_one_block(store: SqlAlchemyCodeSnapshotStore) -> None:
    conversation_id = "3ec0ebac633041d7b8d2875dfa9c5f36"
    item_id = "ad90eebf872d401091068efac7668eb0"
    first = _add(store, conversation_id=conversation_id, item_id=item_id, block=2)
    second = _add(
        store,
        conversation_id=conversation_id,
        item_id=item_id,
        block=2,
        capture_type="uploaded_image",
    )

    snapshots = store.list_for_block(conversation_id, "resp_1", item_id, 2)

    assert {snapshot.id for snapshot in snapshots} == {first.id, second.id}
    assert all(snapshot.language == "python" for snapshot in snapshots)
    uploaded = next(snapshot for snapshot in snapshots if snapshot.id == second.id)
    assert uploaded.capture_type == "uploaded_image"
    assert uploaded.created_by == "alice@example.com"
    assert first.page_index == 0
    assert second.page_index == 0


def test_list_for_block_orders_by_page_index_despite_tied_created_at(
    store: SqlAlchemyCodeSnapshotStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test: created_at has 1-second resolution, so pages of one
    auto code card block that are generated within the same second must
    still come back in page_index order rather than insertion/id order."""
    conversation_id = "6d8d9c0d1f5b4d6f9c7f3a2b1e0d4c5b"
    item_id = "1a2b3c4d5e6f47a8b9c0d1e2f3a4b5c6"

    monkeypatch.setattr(
        "omnigent.stores.code_snapshot_store.sqlalchemy_store.now_epoch",
        lambda: 1_000_000,
    )

    # Insert pages out of order to prove ordering doesn't fall back to
    # insertion order (id/uuid) when created_at ties.
    page_2 = _add(store, conversation_id=conversation_id, item_id=item_id, block=5, page_index=2)
    page_0 = _add(store, conversation_id=conversation_id, item_id=item_id, block=5, page_index=0)
    page_1 = _add(store, conversation_id=conversation_id, item_id=item_id, block=5, page_index=1)

    assert page_2.created_at == page_0.created_at == page_1.created_at

    listed = store.list_for_block(conversation_id, "resp_1", item_id, 5)

    assert [snapshot.id for snapshot in listed] == [page_0.id, page_1.id, page_2.id]


def test_snapshots_are_isolated_by_conversation_item_and_block(
    store: SqlAlchemyCodeSnapshotStore,
) -> None:
    conversation_a = "8a65c683864347aeb1e7b12be7254247"
    conversation_b = "23715293194e4d79908bd368805b4443"
    item_a = "2ef2333e69f8476ab885874180276e9c"
    item_b = "686683dc5f36418d97406b906451007d"
    wanted = _add(store, conversation_id=conversation_a, item_id=item_a, block=1)
    _add(store, conversation_id=conversation_a, item_id=item_a, block=3)
    _add(store, conversation_id=conversation_a, item_id=item_b, block=1)
    _add(store, conversation_id=conversation_b, item_id=item_a, block=1)

    listed = store.list_for_block(conversation_a, "resp_1", item_a, 1)

    assert [snapshot.id for snapshot in listed] == [wanted.id]
    assert store.get(wanted.id, conversation_b) is None


def test_delete_and_conversation_cleanup_return_artifact_references(
    store: SqlAlchemyCodeSnapshotStore,
) -> None:
    conversation_id = "297560306aed4c398142f06743ec3e86"
    item_id = "13533e5366204d2cb6fb1c434f7536ab"
    first = _add(store, conversation_id=conversation_id, item_id=item_id, block=0)
    second = _add(store, conversation_id=conversation_id, item_id=item_id, block=1)

    assert store.delete(first.id, conversation_id) == first
    removed = store.remove_conversation(conversation_id)

    assert removed == [second]
    assert store.list_for_block(conversation_id, "resp_1", item_id, 1) == []
