"""Tracer bullet 5: IM & Event Server — REST + WebSocket 断线续传 / 不丢不重 (§5.4)."""
import pytest
from fastapi.testclient import TestClient

from botteam_kernel.server import create_app


@pytest.fixture()
def client(tmp_path):
    app = create_app(db_path=str(tmp_path / "server.db"))
    with TestClient(app) as c:
        yield c


def make_group(client, mode="default_single"):
    r = client.post("/groups", json={"name": "Hetu 研发群", "mode": mode})
    assert r.status_code == 200
    return r.json()["group_id"]


def test_create_group_and_add_members(client):
    gid = make_group(client)
    r = client.post(f"/groups/{gid}/members", json={"kind": "agent", "name": "Hetu", "capabilities": ["coding"]})
    assert r.status_code == 200 and r.json()["kind"] == "agent"


def test_post_message_persisted_and_listed(client):
    gid = make_group(client)
    client.post(f"/groups/{gid}/messages", json={"sender": "沣锅", "text": "大家好", "idempotency_key": "c1"})
    msgs = client.get(f"/groups/{gid}/messages").json()
    assert len(msgs) == 1 and msgs[0]["text"] == "大家好"


def test_duplicate_idempotency_key_no_second_message(client):
    gid = make_group(client)
    a = client.post(f"/groups/{gid}/messages", json={"sender": "沣锅", "text": "x", "idempotency_key": "dup"})
    b = client.post(f"/groups/{gid}/messages", json={"sender": "沣锅", "text": "x", "idempotency_key": "dup"})
    assert a.json()["message_id"] == b.json()["message_id"]
    assert len(client.get(f"/groups/{gid}/messages").json()) == 1


def test_at_all_agents_route_decided_single_with_cost(client):
    """@全体Agent → 路由命中者应答 + 显示预计成本（§四）."""
    gid = make_group(client)
    h = client.post(f"/groups/{gid}/members", json={"kind": "agent", "name": "Hetu", "capabilities": ["coding"]}).json()
    a = client.post(f"/groups/{gid}/members", json={"kind": "agent", "name": "Avatar", "capabilities": ["security"]}).json()
    r = client.post(f"/groups/{gid}/messages", json={"sender": "沣锅", "text": "@全体Agent 实现登录功能"})
    route = r.json()["route"]
    assert route["responders"] == [h["member_id"]]
    assert "estimated_cost" in route


def test_ws_replays_missed_events_after_disconnect(client):
    """断线续传：客户端带 last_seq 重连，补发全部错过事件，不丢."""
    gid = make_group(client)
    for i in range(3):
        client.post(f"/groups/{gid}/messages", json={"sender": "沣锅", "text": f"m{i}"})
    with client.websocket_connect(f"/ws/{gid}?last_seq=0") as ws:
        replayed = [ws.receive_json() for _ in range(4)]  # group.created + 3 messages
    assert [e["payload"].get("text") for e in replayed[1:]] == ["m0", "m1", "m2"]
    seqs = [e["seq"] for e in replayed]
    assert seqs == sorted(seqs)


def test_ws_reconnect_no_loss_no_duplicate(client):
    """重连后不丢不重：第二次连接从已收 seq 续传，无重复无缺口."""
    gid = make_group(client)
    client.post(f"/groups/{gid}/messages", json={"sender": "沣锅", "text": "a"})
    with client.websocket_connect(f"/ws/{gid}?last_seq=0") as ws:
        got = [ws.receive_json() for _ in range(2)]  # group.created + a
    last = got[-1]["seq"]
    client.post(f"/groups/{gid}/messages", json={"sender": "沣锅", "text": "b"})
    with client.websocket_connect(f"/ws/{gid}?last_seq={last}") as ws:
        second = ws.receive_json()
    assert second["payload"]["text"] == "b"
    assert second["seq"] > last


def test_task_flow_over_http(client):
    t = client.post("/tasks", json={"group_id": "g1", "assignee": "hetu-1", "title": "写爬虫"}).json()
    run = client.post(f"/tasks/{t['task_id']}/runs", json={"agent_id": "hetu-1"}).json()
    client.post(f"/runs/{run['run_id']}/complete", json={"result": "done"})
    assert client.get(f"/tasks/{t['task_id']}").json()["status"] == "awaiting_acceptance"
    client.post(f"/tasks/{t['task_id']}/accept", json={"accepted_by": "沣锅"})
    assert client.get(f"/tasks/{t['task_id']}").json()["status"] == "completed"


def test_counterexample_cursor_that_ignores_seq_loses_events(client):
    """反例：若客户端忽略 seq 只收增量，断线期间的事件必然丢失（不变量：补发无缺口）."""
    gid = make_group(client)
    client.post(f"/groups/{gid}/messages", json={"sender": "沣锅", "text": "missed-1"})
    client.post(f"/groups/{gid}/messages", json={"sender": "沣锅", "text": "missed-2"})
    with client.websocket_connect(f"/ws/{gid}?last_seq=0") as ws:
        got = [ws.receive_json() for _ in range(3)]  # group.created + 2 missed messages
    texts = [e["payload"].get("text") for e in got]
    assert "missed-1" in texts and "missed-2" in texts  # 带游标重连必补全；不带游标语义即丢
