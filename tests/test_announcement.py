"""群公告（P0 清单：群公告 = 该群长期系统提示，QQ 范式：群主发布、全员通知、Agent 自动注入）.

语义：set_announcement 事件溯源、重启恢复；公告作为约束自动注入委派 prompt
（compose_prompt，无 persona 也生效）；未设置公告不得注入（反例）；跨群不泄漏（反例）。
"""
import pytest
from fastapi.testclient import TestClient

from botteam_kernel.events import EventStore
from botteam_kernel.groups import GroupService
from botteam_kernel.server import create_app


def test_set_announcement_projects_onto_group(tmp_path):
    svc = GroupService(EventStore(str(tmp_path / "a.db")))
    g = svc.create_group("研究群")
    svc.set_announcement(g["group_id"], "今日重点：GUI QQ 化", set_by="沣锅")
    ann = svc._groups[g["group_id"]].get("announcement")
    assert ann and ann["text"] == "今日重点：GUI QQ 化"
    g2 = svc.create_group("别的群")
    assert not svc._groups[g2["group_id"]].get("announcement")  # 反例: 别的群不受影响


def test_announcement_survives_restart(tmp_path):
    db = str(tmp_path / "a.db")
    svc = GroupService(EventStore(db))
    g = svc.create_group("研究群")
    svc.set_announcement(g["group_id"], "重点", set_by="沣锅")
    svc.set_announcement(g["group_id"], "更新后的重点", set_by="沣锅")
    svc2 = GroupService(EventStore(db))  # 事件溯源重放
    assert svc2._groups[g["group_id"]]["announcement"]["text"] == "更新后的重点"


@pytest.fixture()
def client(tmp_path):
    def boom(endpoint, payload, token):
        raise AssertionError("poll-mode must not be pushed")

    app = create_app(db_path=str(tmp_path / "ann.db"), transport=boom)
    with TestClient(app) as c:
        yield c


def _setup(client):
    gid = client.post("/groups", json={"name": "研发群", "mode": "default_single"}).json()["group_id"]
    client.post(f"/groups/{gid}/members", json={"name": "沣锅", "kind": "human"})
    client.post(f"/groups/{gid}/members", json={"name": "hetu-worker", "kind": "agent"})
    client.post("/gateway/agents", json={"agent_id": "hetu-worker", "endpoint": "", "capabilities": ["general"], "token": "t"})
    return gid


def test_announcement_rest_and_event(client):
    gid = _setup(client)
    r = client.post(f"/groups/{gid}/announcement", json={"text": "今天不许摸鱼", "set_by": "沣锅"})
    assert r.status_code == 200
    g = next(x for x in client.get("/groups").json() if x["group_id"] == gid)
    assert g["announcement"]["text"] == "今天不许摸鱼"
    evs = client.get(f"/groups/{gid}/events?since=0").json()
    assert any(e["kind"] == "group.announcement_set" for e in evs)


def test_announcement_injected_into_delegation_prompt(client):
    """公告=长期系统提示：委派 prompt 自动注入（无 persona 也生效）."""
    gid = _setup(client)
    client.post(f"/groups/{gid}/announcement", json={"text": "每句话结尾加喵~", "set_by": "沣锅"})
    client.post(f"/groups/{gid}/messages", json={"sender": "沣锅", "text": "@hetu-worker 汇报进展", "idempotency_key": "k1"})
    pend = client.get("/gateway/agents/hetu-worker/pending").json()
    assert len(pend) == 1
    assert "【群公告】每句话结尾加喵~" in pend[0]["prompt"]
    assert "【本次任务】@hetu-worker 汇报进展" in pend[0]["prompt"]


def test_counterexample_no_announcement_no_injection(client):
    """反例: 未设置公告时 prompt 不得出现【群公告】段."""
    gid = _setup(client)
    client.post(f"/groups/{gid}/messages", json={"sender": "沣锅", "text": "@hetu-worker 汇报进展", "idempotency_key": "k2"})
    pend = client.get("/gateway/agents/hetu-worker/pending").json()
    assert len(pend) == 1
    assert "【群公告】" not in pend[0]["prompt"]
