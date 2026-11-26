"""A2A Gateway HTTP endpoints (部署组件: Bot-team A2A Gateway)."""
import pytest
from fastapi.testclient import TestClient

from botteam_kernel.server import create_app


@pytest.fixture()
def client(tmp_path):
    app = create_app(db_path=str(tmp_path / "gw.db"), transport=lambda endpoint, payload, token: {"id": payload["a2a_task_id"], "state": "submitted"})
    with TestClient(app) as c:
        yield c


def test_register_and_list_agents(client):
    r = client.post("/gateway/agents", json={"agent_id": "hetu-main", "endpoint": "http://192.168.2.10:9900", "capabilities": ["general"], "token": "t1"})
    assert r.status_code == 200
    assert any(a["agent_id"] == "hetu-main" for a in client.get("/gateway/agents").json())


def test_delegate_and_callback_roundtrip(client):
    client.post("/gateway/agents", json={"agent_id": "hetu-main", "endpoint": "http://h:9900", "capabilities": [], "token": "t"})
    d = client.post("/gateway/delegate", json={"botteam_task_id": "bt-9", "agent_id": "hetu-main", "prompt": "hi"}).json()
    assert d["a2a_task_id"]
    cb = client.post("/gateway/callback", json={"a2a_task_id": d["a2a_task_id"], "state": "completed", "result": "ok"})
    assert cb.status_code == 200
    assert client.get("/gateway/delegations/bt-9").json()["state"] == "completed"


def test_delegate_unknown_agent_400(client):
    r = client.post("/gateway/delegate", json={"botteam_task_id": "bt-x", "agent_id": "ghost", "prompt": "hi"})
    assert r.status_code == 400


def test_poll_endpoint_claim_and_clear(tmp_path):
    def boom(endpoint, payload, token):
        raise AssertionError("poll-mode must not be pushed")

    app = create_app(db_path=str(tmp_path / "poll.db"), transport=boom)
    with TestClient(app) as c:
        c.post("/gateway/agents", json={"agent_id": "hetu-worker", "endpoint": "", "capabilities": [], "token": "t"})
        d = c.post("/gateway/delegate", json={"botteam_task_id": "bt-poll", "agent_id": "hetu-worker", "prompt": "hello"}).json()
        pend = c.get("/gateway/agents/hetu-worker/pending").json()
        assert len(pend) == 1 and pend[0]["prompt"] == "hello"
        c.post("/gateway/callback", json={"a2a_task_id": d["a2a_task_id"], "state": "completed", "result": "ok"})
        assert c.get("/gateway/agents/hetu-worker/pending").json() == []
