"""信用点（事件溯源）——A2A 任务调度的点数闸门.

语义（QQ 范式）：好友授权调度免点数；非好友调度每次消耗目标 Agent 1 点；
点数不足且无好友授权 → 拒绝派发。发放/消费全部事件化，重启恢复。
"""
from __future__ import annotations

from .events import EventStore


class CreditService:
    def __init__(self, store: EventStore):
        self.store = store
        self._balance: dict[str, int] = {}
        self._cursor = 0
        self._project_new()

    def grant(self, name: str, amount: int, reason: str = "") -> int:
        if amount <= 0:
            raise ValueError("amount must be positive")
        self.store.append("credit.granted", {"name": name, "amount": amount, "reason": reason})
        self._project_new()
        return self._balance[name]

    def consume(self, name: str, amount: int, reason: str = "") -> bool:
        if amount <= 0:
            raise ValueError("amount must be positive")
        if self.balance_of(name) < amount:
            raise ValueError(f"insufficient credits: {name} has {self.balance_of(name)}, need {amount}")
        self.store.append("credit.consumed", {"name": name, "amount": amount, "reason": reason})
        self._project_new()
        return True

    def balance_of(self, name: str) -> int:
        return self._balance.get(name, 0)

    def _project_new(self) -> None:
        events = self.store.since(self._cursor)
        for e in events:
            p = e["payload"]
            if e["kind"] == "credit.granted":
                self._balance[p["name"]] = self._balance.get(p["name"], 0) + p["amount"]
            elif e["kind"] == "credit.consumed":
                self._balance[p["name"]] = self._balance.get(p["name"], 0) - p["amount"]
        if events:
            self._cursor = events[-1]["seq"]
