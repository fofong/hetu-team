"""信用点 + 好友互加 + A2A 授权/点数调度闸门."""
from fastapi.testclient import TestClient

from botteam_kernel.server import create_app
from botteam_kernel.events import EventStore
from botteam_kernel.credits import CreditService
from botteam_kernel.friends import FriendService


def _client(tmp_path, name="ca.db"):
    return TestClient(create_app(str(tmp_path / name)))


def test_credit_grant_consume_persist(tmp_path):
    c = CreditService(EventStore(str(tmp_path / "cr.db")))
    c.grant("hetu-worker", 10, "初始额度")
    assert c.consume("hetu-worker", 3, "任务") is True
    assert c.balance_of("hetu-worker") == 7
    c2 = CreditService(EventStore(str(tmp_path / "cr.db")))
    assert c2.balance_of("hetu-worker") == 7  # 事件溯源重启恢复


def test_counterexample_insufficient_consume(tmp_path):
    c = CreditService(EventStore(str(tmp_path / "c2.db")))
    c.grant("a", 1, "")
    try:
        c.consume("a", 2, "")
        raise AssertionError("must raise")
    except ValueError:
        pass
    assert c.balance_of("a") == 1  # 反例: 失败不扣


def test_friend_request_approve_flow(tmp_path):
    from botteam_kernel.events import EventStore
    fs = FriendService(EventStore(str(tmp_path / "f.db")))
    fs.request("hetu-worker", "沣锅")  # bot 主动加人（互加）
    assert fs.list_requests() == [{"from": "hetu-worker", "to": "沣锅"}]
    assert not fs.are_friends("hetu-worker", "沣锅")  # 反例: 未同意不是好友
    fs.approve("hetu-worker", "沣锅")
    assert fs.are_friends("hetu-worker", "沣锅") and fs.are_friends("沣锅", "hetu-worker")
    assert fs.list_requests() == []
    # 反例三连: 自请、重复、批准不存在
    for bad in (lambda: fs.request("沣锅", "沣锅"),
                lambda: fs.approve("x", "y"),
                lambda: fs.request("hetu-worker", "沣锅")):
        try:
            bad()
            raise AssertionError("must raise")
        except ValueError:
            pass


def _setup(tmp_path):
    c = _client(tmp_path)
    c.post("/gateway/agents", json={"agent_id": "botx", "endpoint": "", "capabilities": [], "token": "t"})
    return c


def test_delegate_authz_via_friend(tmp_path):
    c = _setup(tmp_path)
    c.post("/friends", json={"name": "botx", "kind": "agent"})  # 沣锅加 botx → 好友
    r = c.post("/gateway/delegate", json={"botteam_task_id": "t1", "agent_id": "botx",
                                          "prompt": "p", "requester": "沣锅"})
    assert r.status_code == 200
    # 好友授权路径不扣信用点
    assert c.get("/credits/botx").json()["balance"] == 0


def test_delegate_credits_for_nonfriend(tmp_path):
    c = _setup(tmp_path)
    c.post("/credits/grant", json={"name": "botx", "amount": 2, "reason": "充值"})
    r = c.post("/gateway/delegate", json={"botteam_task_id": "t2", "agent_id": "botx",
                                          "prompt": "p", "requester": "陌生人"})
    assert r.status_code == 200  # 非好友走信用点
    assert c.get("/credits/botx").json()["balance"] == 1
    r2 = c.post("/gateway/delegate", json={"botteam_task_id": "t3", "agent_id": "botx",
                                           "prompt": "p", "requester": "陌生人"})
    assert r2.status_code == 200
    # 反例: 点数耗尽且非好友 → 拒绝派发
    r3 = c.post("/gateway/delegate", json={"botteam_task_id": "t4", "agent_id": "botx",
                                           "prompt": "p", "requester": "陌生人"})
    assert r3.status_code in (402, 403)


def test_counterexample_delegate_no_friend_no_credits(tmp_path):
    c = _setup(tmp_path)
    r = c.post("/gateway/delegate", json={"botteam_task_id": "t9", "agent_id": "botx",
                                          "prompt": "p", "requester": "陌生人"})
    assert r.status_code in (402, 403)
    # 反例: 无 requester 的内部可信路径不受影响（向后兼容）
    assert c.post("/gateway/delegate", json={"botteam_task_id": "t10", "agent_id": "botx",
                                             "prompt": "p"}).status_code == 200


def test_friend_requests_rest(tmp_path):
    c = _setup(tmp_path)
    r = c.post("/friends/request", json={"from": "botx", "to": "沣锅"})
    assert r.status_code == 200
    assert c.get("/friends/requests").json() == [{"from": "botx", "to": "沣锅"}]
    assert c.post("/friends/approve", json={"from": "botx", "to": "沣锅"}).status_code == 200
    assert "botx" in [f["name"] for f in c.get("/friends").json()]  # 同意后进入好友列表
    # 反例: 重复请求 400
    assert c.post("/friends/request", json={"from": "botx", "to": "沣锅"}).status_code == 400
