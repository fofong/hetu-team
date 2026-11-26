"""TDD 切片7: 轮询认领模式 — 防火墙后适配器收不到推送, 改为主动 claim."""
import pytest

from botteam_kernel.events import EventStore
from botteam_kernel.gateway import A2AGateway


def _boom_transport(endpoint, payload, token):
    raise AssertionError("poll-mode agent must NOT be pushed via transport")


@pytest.fixture()
def gw(tmp_path):
    store = EventStore(str(tmp_path / "poll.db"))
    return A2AGateway(store, transport=_boom_transport)


def test_poll_mode_delegation_queued_not_pushed(gw):
    gw.register_agent("hetu-worker", endpoint="", capabilities=["general"], token="t")
    m = gw.delegate("bt-p1", "hetu-worker", "do it")
    pending = gw.pending_for("hetu-worker")
    assert [p["a2a_task_id"] for p in pending] == [m["a2a_task_id"]]
    assert pending[0]["prompt"] == "do it"


def test_poll_mode_pending_clears_after_terminal_callback(gw):
    gw.register_agent("hetu-worker", endpoint="", capabilities=[], token="t")
    m = gw.delegate("bt-p2", "hetu-worker", "x")
    gw.status_callback(m["a2a_task_id"], "completed", "done")
    assert gw.pending_for("hetu-worker") == []


def test_counterexample_push_mode_agent_not_in_pending(gw):
    """反例: 有 endpoint 的推送型 agent 不得进入待认领队列."""
    gw.transport = lambda e, p, t: {"state": "submitted"}
    gw.register_agent("pusher", endpoint="http://h:1", capabilities=[], token="t")
    gw.delegate("bt-p3", "pusher", "x")
    assert gw.pending_for("pusher") == []


def test_pending_survives_restart(gw, tmp_path):
    gw.register_agent("hetu-worker", endpoint="", capabilities=[], token="t")
    gw.delegate("bt-p4", "hetu-worker", "persist me")
    gw2 = A2AGateway(EventStore(str(tmp_path / "poll.db")))
    pending = gw2.pending_for("hetu-worker")
    assert len(pending) == 1 and pending[0]["prompt"] == "persist me"
