"""Tracer bullet 6: A2A Gateway — 注册·验签·委派·状态回传·恢复 + 三层 ID 映射 (§5.4)."""
import pytest
from botteam_kernel.events import EventStore
from botteam_kernel.gateway import A2AGateway


@pytest.fixture()
def gw(tmp_path):
    calls = []

    def fake_transport(endpoint, payload, token):
        calls.append((endpoint, payload, token))
        return {"id": payload["a2a_task_id"], "state": "submitted"}

    g = A2AGateway(EventStore(str(tmp_path / "gw.db")), transport=fake_transport)
    g.calls = calls
    return g


def test_register_agent_and_recover_from_event_log(tmp_path):
    store = EventStore(str(tmp_path / "r.db"))
    g = A2AGateway(store, transport=lambda *a: {})
    g.register_agent("hetu-main", endpoint="http://192.168.2.10:9900", capabilities=["general"], token="t1")
    g2 = A2AGateway(EventStore(str(tmp_path / "r.db")), transport=lambda *a: {})  # 重启
    assert g2.agent("hetu-main")["endpoint"] == "http://192.168.2.10:9900"


def test_delegate_creates_three_layer_id_mapping(gw):
    gw.register_agent("hetu-main", "http://h10:9900", ["general"], token="t1")
    d = gw.delegate(botteam_task_id="bt-1", agent_id="hetu-main", prompt="写爬虫")
    assert d["a2a_task_id"]
    assert gw.resolve_a2a(d["a2a_task_id"])["botteam_task_id"] == "bt-1"
    assert gw.resolve_botteam("bt-1")["a2a_task_id"] == d["a2a_task_id"]


def test_delegate_sends_bearer_token_to_agent(gw):
    gw.register_agent("hetu-main", "http://h10:9900", ["general"], token="secret-tok")
    gw.delegate("bt-2", "hetu-main", "hi")
    assert gw.calls[-1][2] == "secret-tok"  # 出站验签凭据


def test_status_callback_maps_to_botteam_task(gw):
    gw.register_agent("hetu-main", "http://h10:9900", ["general"], token="t1")
    d = gw.delegate("bt-3", "hetu-main", "hi")
    gw.status_callback(d["a2a_task_id"], state="working")
    gw.status_callback(d["a2a_task_id"], state="completed", result="done")
    assert gw.delegation_state("bt-3") == "completed"


def test_duplicate_status_callback_is_idempotent(gw):
    gw.register_agent("hetu-main", "http://h10:9900", ["general"], token="t1")
    d = gw.delegate("bt-4", "hetu-main", "hi")
    gw.status_callback(d["a2a_task_id"], "completed", result="x")
    gw.status_callback(d["a2a_task_id"], "completed", result="x")  # 重复回传
    assert gw.delegation_state("bt-4") == "completed"
    assert len([e for e in gw.store.since(0) if e["kind"] == "a2a.state" and e["payload"]["state"] == "completed"]) == 1


def test_reconcile_flags_stale_running_delegations(gw):
    """任务状态对账：running 无回传 → 标记待对账（SSE 断开 ≠ 任务失败，但必须核查）."""
    gw.register_agent("hetu-main", "http://h10:9900", ["general"], token="t1")
    gw.delegate("bt-5", "hetu-main", "hi")
    gw.mark_stale("bt-5")
    assert "bt-5" in gw.reconcile()


def test_counterexample_inmemory_gateway_loses_agents_on_restart(tmp_path):
    """反例：无事件日志恢复的网关重启即丢注册（不变量：恢复必可重建注册表）."""
    naive = A2AGateway(EventStore(str(tmp_path / "n.db")), transport=lambda *a: {}, persist_registry=False)
    naive.register_agent("x", "http://x", [], token="t")
    restarted = A2AGateway(EventStore(str(tmp_path / "n.db")), transport=lambda *a: {}, persist_registry=False)
    assert restarted.agent("x") is None  # 这正是持久注册表要防止的
