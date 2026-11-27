"""信用点（事件溯源）——A2A 任务调度的点数闸门 + 任务托管（escrow）.

语义（QQ 范式）：好友授权调度免点数；非好友调度每次消耗目标 Agent 1 点；
点数不足且无好友授权 → 拒绝派发。发放/消费全部事件化，重启恢复。

托管语义（备忘录 Quota Ledger：发行、冻结、扣除、退款、转移）：
freeze 冻结（发布任务报价冻结，不立即结算）；settle_frozen 冻结转给收款方；
release_frozen 解冻退回。available = granted - consumed - frozen。
"""
from __future__ import annotations

from .events import EventStore


class CreditService:
    def __init__(self, store: EventStore):
        self.store = store
        self._balance: dict[str, int] = {}
        self._frozen: dict[str, int] = {}
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

    def freeze(self, name: str, amount: int, ref: str) -> None:
        """托管冻结：可用余额不足则拒绝（无 escrow 不成立）."""
        if amount <= 0:
            raise ValueError("amount must be positive")
        if self.balance_of(name) < amount:
            raise ValueError(f"insufficient credits to escrow: {name} has {self.balance_of(name)}, need {amount}")
        self.store.append("credit.frozen", {"name": name, "amount": amount, "ref": ref})
        self._project_new()

    def settle_frozen(self, payer: str, payee: str, amount: int, ref: str) -> None:
        """托管结算：从 payer 冻结额转给 payee 可用余额."""
        if amount <= 0:
            raise ValueError("amount must be positive")
        if self._frozen.get(payer, 0) < amount:
            raise ValueError(f"frozen insufficient: {payer}")
        self.store.append("credit.frozen_settled", {"payer": payer, "payee": payee, "amount": amount, "ref": ref})
        self._project_new()

    def release_frozen(self, name: str, amount: int, ref: str) -> None:
        """托管释放：解冻退回可用（退款/撤单/超时）."""
        if amount <= 0:
            raise ValueError("amount must be positive")
        if self._frozen.get(name, 0) < amount:
            raise ValueError(f"frozen insufficient: {name}")
        self.store.append("credit.frozen_released", {"name": name, "amount": amount, "ref": ref})
        self._project_new()

    def balance_of(self, name: str) -> int:
        return self._balance.get(name, 0)

    def frozen_of(self, name: str) -> int:
        return self._frozen.get(name, 0)

    def _project_new(self) -> None:
        events = self.store.since(self._cursor)
        for e in events:
            p = e["payload"]
            k = e["kind"]
            if k == "credit.granted":
                self._balance[p["name"]] = self._balance.get(p["name"], 0) + p["amount"]
            elif k == "credit.consumed":
                self._balance[p["name"]] = self._balance.get(p["name"], 0) - p["amount"]
            elif k == "credit.frozen":
                self._balance[p["name"]] = self._balance.get(p["name"], 0) - p["amount"]
                self._frozen[p["name"]] = self._frozen.get(p["name"], 0) + p["amount"]
            elif k == "credit.frozen_settled":
                self._frozen[p["payer"]] = self._frozen.get(p["payer"], 0) - p["amount"]
                self._balance[p["payee"]] = self._balance.get(p["payee"], 0) + p["amount"]
            elif k == "credit.frozen_released":
                self._frozen[p["name"]] = self._frozen.get(p["name"], 0) - p["amount"]
                self._balance[p["name"]] = self._balance.get(p["name"], 0) + p["amount"]
        if events:
            self._cursor = events[-1]["seq"]
