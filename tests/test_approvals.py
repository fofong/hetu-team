"""切片10 — 审批闸门 Approval（agentes.chat 借鉴：actionHash 绑定 + 状态机 + TTL）."""
import pytest

from botteam_kernel.approvals import ApprovalService
from botteam_kernel.events import EventStore


class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


@pytest.fixture
def svc(tmp_path):
    return ApprovalService(EventStore(str(tmp_path / "a.db")), ttl_sec=3600, now=(c := Clock())), c


def _req(s):
    return s.request("g1", requester="沣锅", tool="deploy", args={"env": "prod"}, title="发布prod", why="验证")


def test_request_pending_with_action_hash(svc):
    s, _ = svc
    a = _req(s)
    assert a["status"] == "pending"
    assert len(a["action_hash"]) == 64  # SHA-256 hex


def test_approve_then_consume_ok(svc):
    s, _ = svc
    a = _req(s)
    assert s.decide(a["approval_id"], approver="mamhum", approve=True)["status"] == "approved"
    got = s.consume(a["approval_id"], tool="deploy", args={"env": "prod"})
    assert got["tool"] == "deploy"
    assert s.get(a["approval_id"])["status"] == "executed"


def test_counterexample_requester_cannot_self_approve(svc):
    s, _ = svc
    a = _req(s)
    with pytest.raises(ValueError):
        s.decide(a["approval_id"], approver="沣锅", approve=True)


def test_counterexample_consume_wrong_args_rejected(svc):
    """actionHash 绑定：只执行审批时看到的那个动作，改参数即拒."""
    s, _ = svc
    a = _req(s)
    s.decide(a["approval_id"], approver="mamhum", approve=True)
    with pytest.raises(ValueError):
        s.consume(a["approval_id"], tool="deploy", args={"env": "staging"})


def test_counterexample_executed_never_reruns(svc):
    s, _ = svc
    a = _req(s)
    s.decide(a["approval_id"], approver="mamhum", approve=True)
    s.consume(a["approval_id"], tool="deploy", args={"env": "prod"})
    with pytest.raises(ValueError):
        s.consume(a["approval_id"], tool="deploy", args={"env": "prod"})


def test_counterexample_pending_cannot_consume(svc):
    s, _ = svc
    a = _req(s)
    with pytest.raises(ValueError):
        s.consume(a["approval_id"], tool="deploy", args={"env": "prod"})


def test_ttl_expiry(svc):
    s, clock = svc
    a = _req(s)
    clock.t += 3601
    expired = s.expire_stale()
    assert [x["approval_id"] for x in expired] == [a["approval_id"]]
    assert s.get(a["approval_id"])["status"] == "expired"
    with pytest.raises(ValueError):  # 过期不可再批
        s.decide(a["approval_id"], approver="mamhum", approve=True)


def test_refuse_terminal(svc):
    s, _ = svc
    a = _req(s)
    assert s.decide(a["approval_id"], approver="mamhum", approve=False)["status"] == "refused"
    with pytest.raises(ValueError):
        s.decide(a["approval_id"], approver="other", approve=True)


def test_restart_persistence(tmp_path):
    db = str(tmp_path / "p.db")
    clock = Clock()
    s1 = ApprovalService(EventStore(db), ttl_sec=3600, now=clock)
    a = s1.request("g1", requester="u", tool="t", args={}, title="x", why="y")
    s1.decide(a["approval_id"], approver="admin", approve=True)
    s2 = ApprovalService(EventStore(db), ttl_sec=3600, now=clock)
    assert s2.get(a["approval_id"])["status"] == "approved"
    assert s2.consume(a["approval_id"], tool="t", args={})["tool"] == "t"


def test_http_endpoints(tmp_path):
    """REST 接线：请求→他人批准→消费 全链路."""
    from fastapi.testclient import TestClient

    from botteam_kernel.server import create_app

    c = TestClient(create_app(str(tmp_path / "h.db")))
    g = c.post("/groups", json={"name": "g"}).json()["group_id"]
    a = c.post(f"/groups/{g}/approvals", json={"requester": "沣锅", "tool": "deploy",
                                               "args": {"env": "prod"}, "title": "发布", "why": "验证"}).json()
    assert c.post(f"/approvals/{a['approval_id']}/decide", json={"approver": "沣锅", "approve": True}).status_code == 400
    assert c.post(f"/approvals/{a['approval_id']}/decide", json={"approver": "mamhum", "approve": True}).json()["status"] == "approved"
    r = c.post(f"/approvals/{a['approval_id']}/consume", json={"tool": "deploy", "args": {"env": "prod"}})
    assert r.status_code == 200
    assert c.post(f"/approvals/{a['approval_id']}/consume", json={"tool": "deploy", "args": {"env": "prod"}}).status_code == 400
