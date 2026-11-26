"""TDD 切片8: 消息→委派→Worker回传→群内应答 全链路接线."""
import pytest
from fastapi.testclient import TestClient

from botteam_kernel.server import create_app


@pytest.fixture()
def client(tmp_path):
    def boom(endpoint, payload, token):
        raise AssertionError("poll-mode must not be pushed")

    app = create_app(db_path=str(tmp_path / "wire.db"), transport=boom)
    with TestClient(app) as c:
        yield c


def _setup(client):
    gid = client.post("/groups", json={"name": "研发群", "mode": "default_single"}).json()["group_id"]
    client.post(f"/groups/{gid}/members", json={"name": "沣锅", "kind": "human"})
    client.post(f"/groups/{gid}/members", json={"name": "hetu-worker", "kind": "agent"})
    client.post("/gateway/agents", json={"agent_id": "hetu-worker", "endpoint": "", "capabilities": ["general"], "token": "t"})
    return gid


def test_mention_creates_queued_delegation(client):
    gid = _setup(client)
    r = client.post(f"/groups/{gid}/messages", json={"sender": "沣锅", "text": "@hetu-worker 总结今天进展"})
    assert r.status_code == 200
    pend = client.get("/gateway/agents/hetu-worker/pending").json()
    assert len(pend) == 1
    assert "总结今天进展" in pend[0]["prompt"]
    assert pend[0]["group_id"] == gid


def test_callback_result_posts_agent_reply_to_group(client):
    gid = _setup(client)
    client.post(f"/groups/{gid}/messages", json={"sender": "沣锅", "text": "@hetu-worker 讲个笑话"})
    d = client.get("/gateway/agents/hetu-worker/pending").json()[0]
    cb = client.post("/gateway/callback", json={"a2a_task_id": d["a2a_task_id"], "state": "completed", "result": "这是Hetu的回答"})
    assert cb.status_code == 200
    msgs = client.get(f"/groups/{gid}/messages").json()
    replies = [m for m in msgs if m["sender"] == "hetu-worker"]
    assert len(replies) == 1 and replies[0]["text"] == "这是Hetu的回答"


def test_counterexample_no_mention_no_delegation(client):
    gid = _setup(client)
    client.post(f"/groups/{gid}/messages", json={"sender": "沣锅", "text": "随便聊聊"})
    assert client.get("/gateway/agents/hetu-worker/pending").json() == []


def test_counterexample_duplicate_callback_posts_single_reply(client):
    """反例: 重复回传不得产生两条群应答（幂等）."""
    gid = _setup(client)
    client.post(f"/groups/{gid}/messages", json={"sender": "沣锅", "text": "@hetu-worker 幂等测试"})
    d = client.get("/gateway/agents/hetu-worker/pending").json()[0]
    for _ in range(3):
        client.post("/gateway/callback", json={"a2a_task_id": d["a2a_task_id"], "state": "completed", "result": "一次就好"})
    msgs = client.get(f"/groups/{gid}/messages").json()
    assert len([m for m in msgs if m["sender"] == "hetu-worker"]) == 1
