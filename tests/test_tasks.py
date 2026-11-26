"""Tracer bullet 3: Task / Run / Approval + 四条语义 (§5.4) + 五预算 (§5.2)."""
import pytest
from botteam_kernel.events import EventStore
from botteam_kernel.tasks import TaskService, BudgetExceeded


@pytest.fixture()
def svc(tmp_path):
    return TaskService(EventStore(str(tmp_path / "t.db")))


def test_task_created_from_message_is_separate_from_message_send(svc):
    """语义1: 消息发送成功 ≠ 任务执行成功 —— 两者是不同事件."""
    msg = svc.record_message("g1", "沣锅", "@Hetu 写个爬虫")
    task = svc.create_task("g1", origin_message_id=msg["message_id"], assignee="hetu-1", title="写爬虫")
    assert task["status"] == "queued"
    kinds = [e["kind"] for e in svc.store.since(0)]
    assert "message.created" in kinds and "task.created" in kinds
    assert kinds.index("message.created") < kinds.index("task.created")


def test_run_lifecycle_queued_running_completed(svc):
    task = svc.create_task("g1", origin_message_id=None, assignee="hetu-1", title="t")
    run = svc.start_run(task["task_id"], agent_id="hetu-1")
    assert run["status"] == "running"
    assert svc.get_task(task["task_id"])["status"] == "running"
    svc.complete_run(run["run_id"], result="done")
    assert svc.get_run(run["run_id"])["status"] == "completed"


def test_agent_claiming_completion_does_not_complete_task(svc):
    """语义4: Agent 宣称完成 ≠ 交付通过验收 —— 必须经 Artifact 验收."""
    task = svc.create_task("g1", origin_message_id=None, assignee="hetu-1", title="t")
    run = svc.start_run(task["task_id"], agent_id="hetu-1")
    svc.complete_run(run["run_id"], result="我完成了")
    assert svc.get_task(task["task_id"])["status"] == "awaiting_acceptance"
    svc.accept_task(task["task_id"], accepted_by="沣锅")
    assert svc.get_task(task["task_id"])["status"] == "completed"


def test_duplicate_run_completion_is_idempotent(svc):
    """语义3: 收到重复事件 ≠ 应该重复执行."""
    task = svc.create_task("g1", origin_message_id=None, assignee="hetu-1", title="t")
    run = svc.start_run(task["task_id"], agent_id="hetu-1")
    svc.complete_run(run["run_id"], result="ok")
    svc.complete_run(run["run_id"], result="ok")  # redelivery
    assert svc.get_task(task["task_id"])["status"] == "awaiting_acceptance"
    assert len([e for e in svc.store.since(0) if e["kind"] == "run.completed"]) == 1


def test_utterance_budget_exceeded_forces_stop_with_best_effort(svc):
    """§5.2: 发言预算超限 → 强制停止 + 最佳努力答案 + 未竟事项."""
    svc.set_budget("g1", "hetu-1", utterances=2)
    svc.record_utterance("g1", "hetu-1")
    svc.record_utterance("g1", "hetu-1")
    with pytest.raises(BudgetExceeded):
        svc.record_utterance("g1", "hetu-1")
    best = svc.best_effort_summary("g1", "hetu-1")
    assert best["forced_stop"] is True
    assert "未竟事项" in best


def test_execution_budget_independent_from_utterance_budget(svc):
    """§5.2 修订 R11: 发言次数与任务执行次数分离 —— 后台工作数小时只汇报三次."""
    svc.set_budget("g1", "hetu-1", utterances=3, executions=100)
    for _ in range(50):
        svc.record_execution("g1", "hetu-1")  # 50 次执行
    for _ in range(3):
        svc.record_utterance("g1", "hetu-1")  # 只汇报 3 次
    assert svc.budget_usage("g1", "hetu-1") == {"utterances": 3, "executions": 50}
    for _ in range(50):
        svc.record_execution("g1", "hetu-1")  # 补满执行预算 100
    assert svc.budget_usage("g1", "hetu-1")["executions"] == 100
    with pytest.raises(BudgetExceeded):
        svc.record_execution("g1", "hetu-1")  # 第 101 次执行才被强制停止


def test_counterexample_without_budget_agent_never_stops(tmp_path):
    """反例：无预算记账时发言无上限（不变量：超限必停）."""
    naive = TaskService(EventStore(str(tmp_path / "n.db")), enforce_budgets=False)
    for _ in range(10_000):
        naive.record_utterance("g1", "rogue")  # never raises
    assert naive.budget_usage("g1", "rogue")["utterances"] == 10_000
