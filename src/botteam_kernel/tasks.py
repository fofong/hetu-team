"""Task / Run lifecycle + five budgets (§5.2) + four semantics (§5.4)."""
from __future__ import annotations

import uuid

from .events import EventStore


class BudgetExceeded(Exception):
    pass


class TaskService:
    def __init__(self, store: EventStore, enforce_budgets: bool = True):
        self.store = store
        self.enforce = enforce_budgets
        self._tasks: dict[str, dict] = {}
        self._runs: dict[str, dict] = {}
        self._budgets: dict[tuple[str, str], dict] = {}
        self._usage: dict[tuple[str, str], dict] = {}
        self._forced: set[tuple[str, str]] = set()
        self._cursor = 0
        self._project_new()

    # ---- message (lightweight record for task origin) ----
    def record_message(self, group_id: str, sender: str, text: str) -> dict:
        msg = {"group_id": group_id, "message_id": uuid.uuid4().hex, "sender": sender, "text": text}
        self.store.append("message.created", msg, idempotency_key=f"msg-{msg['message_id']}")
        self._project_new()
        return msg

    # ---- Task ----
    def group_tasks(self, group_id: str) -> list[dict]:
        return [t for t in self._tasks.values() if t["group_id"] == group_id]

    def create_task(self, group_id: str, origin_message_id: str | None, assignee: str, title: str) -> dict:
        task = {
            "group_id": group_id,
            "task_id": uuid.uuid4().hex,
            "origin_message_id": origin_message_id,
            "assignee": assignee,
            "title": title,
            "status": "queued",
        }
        self.store.append("task.created", task, idempotency_key=f"task-{task['task_id']}-created")
        self._project_new()
        return task

    def start_run(self, task_id: str, agent_id: str) -> dict:
        run = {"task_id": task_id, "run_id": uuid.uuid4().hex, "agent_id": agent_id, "status": "running"}
        self.store.append("run.started", run, idempotency_key=f"run-{run['run_id']}-started")
        self._project_new()
        return run

    def complete_run(self, run_id: str, result: str) -> dict:
        run = self._runs[run_id]
        if run["status"] != "running":
            return run  # 语义3: 重复完成事件不重复执行
        done = {**run, "status": "completed", "result": result}
        self.store.append("run.completed", done, idempotency_key=f"run-{run_id}-completed")
        self._project_new()
        return self._runs[run_id]

    def accept_task(self, task_id: str, accepted_by: str) -> dict:
        acc = {"task_id": task_id, "accepted_by": accepted_by}
        self.store.append("task.accepted", acc, idempotency_key=f"task-{task_id}-accepted")
        self._project_new()
        return self._tasks[task_id]

    def get_task(self, task_id: str) -> dict:
        return self._tasks[task_id]

    def get_run(self, run_id: str) -> dict:
        return self._runs[run_id]

    # ---- budgets (§5.2: utterances 与 executions 分离) ----
    def set_budget(self, group_id: str, agent_id: str, utterances: int | None = None, executions: int | None = None) -> None:
        b = {"group_id": group_id, "agent_id": agent_id, "utterances": utterances, "executions": executions}
        self.store.append("budget.set", b, idempotency_key=f"budget-{group_id}-{agent_id}")
        self._project_new()

    def record_utterance(self, group_id: str, agent_id: str) -> None:
        self._charge(group_id, agent_id, "utterances")

    def record_execution(self, group_id: str, agent_id: str) -> None:
        self._charge(group_id, agent_id, "executions")

    def budget_usage(self, group_id: str, agent_id: str) -> dict:
        return dict(self._usage.get((group_id, agent_id), {"utterances": 0, "executions": 0}))

    def best_effort_summary(self, group_id: str, agent_id: str) -> dict:
        usage = self.budget_usage(group_id, agent_id)
        return {
            "forced_stop": (group_id, agent_id) in self._forced,
            "已发言": usage["utterances"],
            "已执行": usage["executions"],
            "未竟事项": ["预算耗尽，任务未验收，需人工复核后续排期"],
        }

    def _charge(self, group_id: str, agent_id: str, dim: str) -> None:
        key = (group_id, agent_id)
        usage = self._usage.setdefault(key, {"utterances": 0, "executions": 0})
        limit = self._budgets.get(key, {}).get(dim)
        if self.enforce and limit is not None and usage[dim] >= limit:
            self.store.append(
                "budget.exceeded",
                {"group_id": group_id, "agent_id": agent_id, "dimension": dim, "limit": limit},
                idempotency_key=f"budget-exceeded-{group_id}-{agent_id}-{dim}-{usage[dim]}",
            )
            self._project_new()
            raise BudgetExceeded(f"{agent_id} {dim} budget exceeded ({limit})")
        self.store.append(
            f"{dim}.counted", {"group_id": group_id, "agent_id": agent_id},
            idempotency_key=f"{dim}-{group_id}-{agent_id}-{usage[dim]}",
        )
        self._project_new()

    # ---- projection ----
    def _project_new(self) -> None:
        events = self.store.since(self._cursor)
        for e in events:
            self._project(e)
        if events:
            self._cursor = events[-1]["seq"]

    def _project(self, e: dict) -> None:
        p = e["payload"]
        k = e["kind"]
        if k == "task.created":
            self._tasks[p["task_id"]] = dict(p)
        elif k == "run.started":
            self._runs[p["run_id"]] = dict(p)
            self._tasks[p["task_id"]]["status"] = "running"
        elif k == "run.completed":
            self._runs[p["run_id"]].update(status="completed", result=p.get("result"))
            self._tasks[p["task_id"]]["status"] = "awaiting_acceptance"
        elif k == "task.accepted":
            self._tasks[p["task_id"]]["status"] = "completed"
        elif k == "budget.set":
            self._budgets[(p["group_id"], p["agent_id"])] = p
        elif k in ("utterances.counted", "executions.counted"):
            dim = "utterances" if k == "utterances.counted" else "executions"
            u = self._usage.setdefault((p["group_id"], p["agent_id"]), {"utterances": 0, "executions": 0})
            u[dim] += 1
        elif k == "budget.exceeded":
            self._forced.add((p["group_id"], p["agent_id"]))
