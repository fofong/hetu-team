"""A2A 任务看板（事件溯源）——发布/认领/交付/验收结算，额度托管.

角色模型借 ERC-8183：publisher(client) / claimer(provider) / evaluator(默认=publisher)。
状态机：open → claimed → submitted → settled | refunded | cancelled | expired。
托管借备忘录 Quota Ledger：发布即冻结报价（无 escrow 不成立），结算可部分、差额退回。
幂等：任务进入终态后一切再操作拒绝（同一 task 不重复扣费）。
"""
from __future__ import annotations

import time

from .credits import CreditService
from .events import EventStore

TERMINAL = {"settled", "refunded", "cancelled", "expired"}
CATEGORIES = {"simple": 1, "research": 20, "code": 30, "audit": 50, "verify": 0}  # verify=另行报价


class TaskBoard:
    def __init__(self, store: EventStore, credits: CreditService):
        self.store = store
        self.credits = credits
        self._tasks: dict[str, dict] = {}
        self._seq = 0
        self._cursor = 0
        self._project_new()

    # ---------- 命令 ----------

    def publish(self, publisher: str, title: str, desc: str = "", category: str = "simple",
                reward: int | None = None, deadline: int | None = None, evaluator: str | None = None) -> dict:
        if not title.strip():
            raise ValueError("title required")
        if reward is None:
            reward = CATEGORIES.get(category, 0)
        if reward < 1:
            raise ValueError("reward must be >= 1 (verify 类任务请另行报价)")
        self._seq += 1
        task_id = f"t-{self._seq:06d}"
        self.credits.freeze(publisher, reward, task_id)  # 托管硬约束：不足直接抛错，任务不落库
        self.store.append("task.published", {
            "task_id": task_id, "publisher": publisher, "title": title.strip(), "desc": desc,
            "category": category, "reward": reward, "deadline": deadline,
            "evaluator": evaluator or publisher, "created_at": int(time.time()),
        })
        self._project_new()
        return self._tasks[task_id]

    def claim(self, task_id: str, claimer: str) -> dict:
        t = self._require(task_id)
        self._expire_if_due(t)
        if t["status"] != "open":
            raise ValueError(f"task not open: {task_id} ({t['status']})")
        if claimer == t["publisher"]:
            raise ValueError("publisher cannot claim own task")
        self._seq += 1
        attempt_id = f"a-{self._seq:06d}"
        self.store.append("task.claimed", {"task_id": task_id, "claimer": claimer, "attempt_id": attempt_id})
        self._project_new()
        return self._tasks[task_id]

    def deliver(self, task_id: str, claimer: str, artifact: str, note: str = "") -> dict:
        t = self._require(task_id)
        if t["status"] != "claimed":
            raise ValueError(f"task not claimed: {task_id} ({t['status']})")
        if claimer != t["claimer"]:
            raise ValueError("only current claimer can deliver")
        if not artifact.strip():
            raise ValueError("artifact required")
        self.store.append("task.delivered", {"task_id": task_id, "claimer": claimer,
                                             "artifact": artifact.strip(), "note": note})
        self._project_new()
        return self._tasks[task_id]

    def settle(self, task_id: str, evaluator: str, amount: int | None = None) -> dict:
        """验收结算：amount≤reward，差额自动退回发布方（部分完成）."""
        t = self._require(task_id)
        if t["status"] != "submitted":
            raise ValueError(f"task not submitted: {task_id} ({t['status']})")
        if evaluator != t["evaluator"]:
            raise ValueError("only evaluator can settle")
        reward = t["reward"]
        amount = reward if amount is None else amount
        if not 1 <= amount <= reward:
            raise ValueError(f"amount must be in [1, {reward}]")
        self._seq += 1
        settlement_id = f"s-{self._seq:06d}"
        refund = reward - amount
        if amount:
            self.credits.settle_frozen(t["publisher"], t["claimer"], amount, task_id)
        if refund:
            self.credits.release_frozen(t["publisher"], refund, task_id)
        self.store.append("task.settled", {"task_id": task_id, "evaluator": evaluator, "amount": amount,
                                           "refunded": refund, "settlement_id": settlement_id})
        self._project_new()
        return {"amount": amount, "refunded": refund, "settlement_id": settlement_id}

    def reject(self, task_id: str, evaluator: str, reason: str = "") -> dict:
        t = self._require(task_id)
        if t["status"] not in ("claimed", "submitted"):
            raise ValueError(f"cannot reject: {task_id} ({t['status']})")
        if evaluator != t["evaluator"]:
            raise ValueError("only evaluator can reject")
        self.credits.release_frozen(t["publisher"], t["reward"], task_id)
        self.store.append("task.rejected", {"task_id": task_id, "evaluator": evaluator, "reason": reason})
        self._project_new()
        return self._tasks[task_id]

    def cancel(self, task_id: str, publisher: str) -> dict:
        t = self._require(task_id)
        if t["status"] != "open":
            raise ValueError(f"only open task can cancel: {task_id} ({t['status']})")
        if publisher != t["publisher"]:
            raise ValueError("only publisher can cancel")
        self.credits.release_frozen(publisher, t["reward"], task_id)
        self.store.append("task.cancelled", {"task_id": task_id, "publisher": publisher})
        self._project_new()
        return self._tasks[task_id]

    def expire_due(self, now: int | None = None) -> int:
        """惰性过期清扫：超时未认领 → expired 全额退款."""
        now = now or int(time.time())
        n = 0
        for t in list(self._tasks.values()):
            if t["status"] == "open" and t.get("deadline") and t["deadline"] <= now:
                self.credits.release_frozen(t["publisher"], t["reward"], t["task_id"])
                self.store.append("task.expired", {"task_id": t["task_id"]})
                self._project_new()
                n += 1
        return n

    # ---------- 查询 ----------

    def get(self, task_id: str) -> dict:
        t = self._require(task_id)
        self._expire_if_due(t)
        return t

    def list_tasks(self, status: str | None = None, publisher: str | None = None) -> list[dict]:
        self.expire_due()
        out = []
        for t in self._tasks.values():
            if status and t["status"] != status:
                continue
            if publisher and t["publisher"] != publisher:
                continue
            out.append(t)
        return sorted(out, key=lambda x: x["task_id"])

    def reputation(self, name: str) -> dict:
        """信誉投影（ERC-8004 语义最小版）：作为服务方的交付成功率."""
        settled = refunded = 0
        for t in self._tasks.values():
            if t.get("claimer") != name:
                continue
            if t["status"] == "settled":
                settled += 1
            elif t["status"] == "refunded":
                refunded += 1
        total = settled + refunded
        return {"settled": settled, "refunded": refunded,
                "success_rate": (settled / total) if total else 0.0}

    # ---------- 内部 ----------

    def _require(self, task_id: str) -> dict:
        if task_id not in self._tasks:
            raise KeyError(f"no such task: {task_id}")
        return self._tasks[task_id]

    def _expire_if_due(self, t: dict) -> None:
        if t["status"] == "open" and t.get("deadline") and t["deadline"] <= int(time.time()):
            self.credits.release_frozen(t["publisher"], t["reward"], t["task_id"])
            self.store.append("task.expired", {"task_id": t["task_id"]})
            self._project_new()

    def _project_new(self) -> None:
        events = self.store.since(self._cursor)
        for e in events:
            p = e["payload"]
            k = e["kind"]
            if k == "task.published":
                self._tasks[p["task_id"]] = dict(p, status="open", claimer=None, attempt_id=None,
                                                 artifact=None, delivery_note=None)
                n = int(p["task_id"].split("-")[1])
                self._seq = max(self._seq, n)
            elif k == "task.claimed":
                t = self._tasks[p["task_id"]]
                t.update(status="claimed", claimer=p["claimer"], attempt_id=p["attempt_id"])
                self._seq = max(self._seq, int(p["attempt_id"].split("-")[1]))
            elif k == "task.delivered":
                t = self._tasks[p["task_id"]]
                t.update(status="submitted", artifact=p["artifact"], delivery_note=p.get("note", ""))
            elif k == "task.settled":
                self._tasks[p["task_id"]].update(status="settled")
                self._seq = max(self._seq, int(p["settlement_id"].split("-")[1]))
            elif k == "task.rejected":
                self._tasks[p["task_id"]].update(status="refunded")
            elif k == "task.cancelled":
                self._tasks[p["task_id"]].update(status="cancelled")
            elif k == "task.expired":
                self._tasks[p["task_id"]].update(status="expired")
        if events:
            self._cursor = events[-1]["seq"]
