"""切片9 — 防互聊铁律：Agent 消息只展示不触发委派（TeamAgentX 借鉴项）."""
from fastapi.testclient import TestClient

from botteam_kernel.server import create_app


def _app(tmp_path):
    calls = []
    app = create_app(str(tmp_path / "t.db"),
                     transport=lambda ep, payload, token: calls.append(payload) or {"id": "x", "state": "submitted"})
    return TestClient(app), calls


def test_human_mention_still_delegates(tmp_path):
    c, calls = _app(tmp_path)
    g = c.post("/groups", json={"name": "g", "mode": "default_single"}).json()["group_id"]
    c.post(f"/groups/{g}/members", json={"kind": "agent", "name": "coder", "capabilities": ["coding"]})
    c.post("/gateway/agents", json={"agent_id": "coder", "endpoint": "http://h:1", "capabilities": ["coding"], "token": "t"})
    r = c.post(f"/groups/{g}/messages", json={"sender": "沣锅", "text": "@coder 写代码"})
    assert len(r.json()["route"]["responders"]) == 1
    assert len(calls) == 1


def test_counterexample_agent_message_mention_never_delegates(tmp_path):
    """Agent 回复里带 @ 只展示，绝不触发新委派 —— 否则两个 Agent 互 @ 无限互聊."""
    c, calls = _app(tmp_path)
    g = c.post("/groups", json={"name": "g", "mode": "default_single"}).json()["group_id"]
    c.post(f"/groups/{g}/members", json={"kind": "agent", "name": "coder", "capabilities": ["coding"]})
    c.post(f"/groups/{g}/members", json={"kind": "agent", "name": "reviewer", "capabilities": ["security"]})
    c.post("/gateway/agents", json={"agent_id": "coder", "endpoint": "http://h:1", "capabilities": ["coding"], "token": "t"})
    c.post("/gateway/agents", json={"agent_id": "reviewer", "endpoint": "http://h:2", "capabilities": ["security"], "token": "t"})
    r = c.post(f"/groups/{g}/messages", json={"sender": "coder", "text": "@reviewer 帮我审查"})
    assert r.status_code == 200
    assert r.json()["route"] is None  # 不裁决
    assert calls == []               # 不委派


def test_counterexample_gateway_agent_sender_also_guarded(tmp_path):
    """sender 是网关注册 agent（未必是群成员）同样不触发."""
    c, calls = _app(tmp_path)
    g = c.post("/groups", json={"name": "g", "mode": "default_single"}).json()["group_id"]
    c.post(f"/groups/{g}/members", json={"kind": "agent", "name": "reviewer", "capabilities": ["security"]})
    c.post("/gateway/agents", json={"agent_id": "stranger-bot", "endpoint": "http://h:9", "capabilities": [], "token": "t"})
    c.post("/gateway/agents", json={"agent_id": "reviewer", "endpoint": "http://h:2", "capabilities": ["security"], "token": "t"})
    r = c.post(f"/groups/{g}/messages", json={"sender": "stranger-bot", "text": "@reviewer 协作"})
    assert r.json()["route"] is None
    assert calls == []


def test_agent_message_still_recorded(tmp_path):
    """铁律只拦触发，不拦展示：Agent 消息照常入库可查."""
    c, _ = _app(tmp_path)
    g = c.post("/groups", json={"name": "g", "mode": "default_single"}).json()["group_id"]
    c.post(f"/groups/{g}/members", json={"kind": "agent", "name": "coder", "capabilities": []})
    c.post(f"/groups/{g}/messages", json={"sender": "coder", "text": "我完成了", "idempotency_key": "k1"})
    texts = [m["text"] for m in c.get(f"/groups/{g}/messages").json()]
    assert "我完成了" in texts
