"""Tracer bullet 1: persistent event store (§5.4 持久事件表 + 幂等 + 断线续传)."""
import pytest
from botteam_kernel.events import EventStore


@pytest.fixture()
def store(tmp_path):
    return EventStore(str(tmp_path / "events.db"))


def test_append_event_assigns_monotonic_sequence(store):
    e1 = store.append("message.created", {"group_id": "g1", "text": "hi"}, idempotency_key="k1")
    e2 = store.append("message.created", {"group_id": "g1", "text": "yo"}, idempotency_key="k2")
    assert e1["seq"] == 1
    assert e2["seq"] == 2
    assert e1["event_id"] != e2["event_id"]


def test_duplicate_idempotency_key_returns_original_without_new_row(store):
    first = store.append("task.started", {"task_id": "t1"}, idempotency_key="task-t1-start")
    dup = store.append("task.started", {"task_id": "t1"}, idempotency_key="task-t1-start")
    assert dup["event_id"] == first["event_id"]
    assert dup["seq"] == first["seq"]
    assert store.count() == 1


def test_replay_since_returns_events_after_cursor(store):
    for i in range(5):
        store.append("message.created", {"group_id": "g1", "text": f"m{i}"}, idempotency_key=f"k{i}")
    missed = store.since(cursor_seq=2)
    assert [e["seq"] for e in missed] == [3, 4, 5]


def test_events_survive_reopen(store, tmp_path):
    store.append("group.created", {"group_id": "g1"}, idempotency_key="g1")
    reopened = EventStore(str(tmp_path / "events.db"))
    events = reopened.since(cursor_seq=0)
    assert len(events) == 1
    assert events[0]["payload"]["group_id"] == "g1"


def test_counterexample_naive_append_without_idempotency_stores_duplicates(tmp_path):
    """反例：无幂等键的朴素存储在重复投递下必然重复入库（不变量被破坏的证明）。"""
    naive = EventStore(str(tmp_path / "naive.db"))
    naive.append("task.started", {"task_id": "t1"})  # no idempotency key
    naive.append("task.started", {"task_id": "t1"})  # redelivery
    assert naive.count() == 2  # invariant violated without a key
