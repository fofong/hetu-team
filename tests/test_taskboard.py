"""A2A 任务发布（Task Board）× 额度托管（escrow）测试.

对应 docs/DESIGN-A2A-TASK-ECON-20261127.md E1 验收标准：
正例全生命周期 + 部分结算；反例覆盖备忘录五难题（重复扣费/超时退款/部分完成/越权/自认领/无托管）。
"""
import pytest

from botteam_kernel.credits import CreditService
from botteam_kernel.events import EventStore
from botteam_kernel.taskboard import TaskBoard


@pytest.fixture()
def env(tmp_path):
    store = EventStore(str(tmp_path / "events.jsonl"))
    credits = CreditService(store)
    credits.grant("沣锅", 100, "初始")
    credits.grant("hetu-worker", 10, "初始")
    return store, credits, TaskBoard(store, credits)


def test_full_lifecycle(env):
    _, credits, tb = env
    t = tb.publish("沣锅", "审计登录模块", category="audit", reward=50)
    assert t["status"] == "open"
    assert credits.balance_of("沣锅") == 50  # 50 已冻结
    assert credits.frozen_of("沣锅") == 50
    tb.claim(t["task_id"], "hetu-worker")
    tb.deliver(t["task_id"], "hetu-worker", artifact="审计报告 v1")
    s = tb.settle(t["task_id"], "沣锅")
    assert s["amount"] == 50
    assert credits.balance_of("hetu-worker") == 60
    assert credits.balance_of("沣锅") == 50
    assert credits.frozen_of("沣锅") == 0
    assert tb.get(t["task_id"])["status"] == "settled"


def test_partial_settle_refunds_difference(env):
    _, credits, tb = env
    t = tb.publish("沣锅", "部分完成", category="code", reward=30)
    tb.claim(t["task_id"], "hetu-worker")
    tb.deliver(t["task_id"], "hetu-worker", artifact="半成品")
    s = tb.settle(t["task_id"], "沣锅", amount=12)
    assert s["amount"] == 12 and s["refunded"] == 18
    assert credits.balance_of("hetu-worker") == 22
    # 冻结30后沣锅可用70，退差额18 → 88
    assert credits.balance_of("沣锅") == 88
    assert credits.frozen_of("沣锅") == 0


def test_publish_insufficient_credits_rejected(env):
    _, credits, tb = env
    with pytest.raises(ValueError):
        tb.publish("hetu-worker", "超出余额", reward=999)
    assert tb.list_tasks() == []


def test_counterexample_double_claim(env):
    _, _, tb = env
    t = tb.publish("沣锅", "任务", reward=5)
    tb.claim(t["task_id"], "hetu-worker")
    with pytest.raises(ValueError):
        tb.claim(t["task_id"], "Avatar")


def test_counterexample_self_claim(env):
    _, _, tb = env
    t = tb.publish("沣锅", "任务", reward=5)
    with pytest.raises(ValueError):
        tb.claim(t["task_id"], "沣锅")


def test_counterexample_deliver_by_non_claimer(env):
    _, _, tb = env
    t = tb.publish("沣锅", "任务", reward=5)
    tb.claim(t["task_id"], "hetu-worker")
    with pytest.raises(ValueError):
        tb.deliver(t["task_id"], "Avatar", artifact="冒领交付")


def test_counterexample_settle_by_non_evaluator(env):
    _, _, tb = env
    t = tb.publish("沣锅", "任务", reward=5)
    tb.claim(t["task_id"], "hetu-worker")
    tb.deliver(t["task_id"], "hetu-worker", artifact="x")
    with pytest.raises(ValueError):
        tb.settle(t["task_id"], "Avatar")


def test_counterexample_double_settle(env):
    _, credits, tb = env
    t = tb.publish("沣锅", "任务", reward=5)
    tb.claim(t["task_id"], "hetu-worker")
    tb.deliver(t["task_id"], "hetu-worker", artifact="x")
    tb.settle(t["task_id"], "沣锅")
    with pytest.raises(ValueError):  # 同一 task 不得二次结算（幂等防线）
        tb.settle(t["task_id"], "沣锅")
    assert credits.balance_of("hetu-worker") == 15  # 只结算了一次


def test_counterexample_settle_over_escrow(env):
    _, _, tb = env
    t = tb.publish("沣锅", "任务", reward=5)
    tb.claim(t["task_id"], "hetu-worker")
    tb.deliver(t["task_id"], "hetu-worker", artifact="x")
    with pytest.raises(ValueError):
        tb.settle(t["task_id"], "沣锅", amount=6)


def test_timeout_expires_and_refunds(env):
    _, credits, tb = env
    t = tb.publish("沣锅", "超时任务", reward=20, deadline=1)  # deadline=1 epoch，早已过期
    tb.expire_due(now=10 ** 9)
    assert tb.get(t["task_id"])["status"] == "expired"
    assert credits.balance_of("沣锅") == 100  # 全额退回
    assert credits.frozen_of("沣锅") == 0
    with pytest.raises(ValueError):
        tb.claim(t["task_id"], "hetu-worker")


def test_cancel_refunds(env):
    _, credits, tb = env
    t = tb.publish("沣锅", "撤单", reward=10)
    tb.cancel(t["task_id"], "沣锅")
    assert credits.balance_of("沣锅") == 100
    with pytest.raises(ValueError):
        tb.claim(t["task_id"], "hetu-worker")


def test_reject_after_deliver_refunds(env):
    _, credits, tb = env
    t = tb.publish("沣锅", "假交付", reward=40)
    tb.claim(t["task_id"], "hetu-worker")
    tb.deliver(t["task_id"], "hetu-worker", artifact="垃圾")
    tb.reject(t["task_id"], "沣锅", reason="不合格")
    assert tb.get(t["task_id"])["status"] == "refunded"
    assert credits.balance_of("沣锅") == 100
    assert credits.balance_of("hetu-worker") == 10


def test_reputation(env):
    _, _, tb = env
    t1 = tb.publish("沣锅", "A", reward=5)
    tb.claim(t1["task_id"], "hetu-worker")
    tb.deliver(t1["task_id"], "hetu-worker", artifact="ok")
    tb.settle(t1["task_id"], "沣锅")
    t2 = tb.publish("沣锅", "B", reward=5)
    tb.claim(t2["task_id"], "hetu-worker")
    tb.deliver(t2["task_id"], "hetu-worker", artifact="bad")
    tb.reject(t2["task_id"], "沣锅")
    rep = tb.reputation("hetu-worker")
    assert rep["settled"] == 1 and rep["refunded"] == 1
    assert abs(rep["success_rate"] - 0.5) < 1e-9


def test_restart_recovery(env, tmp_path):
    store, credits, tb = env
    t = tb.publish("沣锅", "重启存活", reward=7)
    tb.claim(t["task_id"], "hetu-worker")
    tb2 = TaskBoard(store, credits)
    assert tb2.get(t["task_id"])["status"] == "claimed"
    assert credits.frozen_of("沣锅") == 7


def test_counterexample_no_escrow_task_cannot_exist(env):
    _, credits, tb = env
    # 托管是硬约束：每个未终态任务必有等值冻结
    t = tb.publish("沣锅", "托管校验", reward=9)
    assert credits.frozen_of("沣锅") == 9
    tb.settle  # 存在性
    with pytest.raises(KeyError):
        tb.get("t-999999")
