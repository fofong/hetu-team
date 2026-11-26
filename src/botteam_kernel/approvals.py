"""Approval 审批闸门（九对象协议补全，agentes.chat 借鉴）.

核心不变量：
- actionHash = SHA-256(tool + 规范化 args)：审批绑定到具体动作，执行时参数必须一致；
- 状态机 pending → approved/refused/expired，approved 一次性 consume → executed，executed 永不重跑；
- 请求者不可自批；pending 超 TTL 置 expired。全部事件溯源，重启可恢复。
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid

from .events import EventStore

TERMINAL = {"executed", "refused", "expired"}


def action_hash(tool: str, args: dict) -> str:
    canon = json.dumps({"tool": tool, "args": args}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


class ApprovalService:
    def __init__(self, store: EventStore, ttl_sec: int = 86400, now=time.time):
        self.store = store
        self.ttl = ttl_sec
        self.now = now
        self._items: dict[str, dict] = {}
        self._cursor = 0
        self._project_new()

    def request(self, group_id: str, requester: str, tool: str, args: dict, title: str, why: str) -> dict:
        item = {
            "approval_id": uuid.uuid4().hex, "group_id": group_id, "requester": requester,
            "tool": tool, "args": args, "title": title, "why": why,
            "action_hash": action_hash(tool, args), "requested_at": self.now(),
        }
        self.store.append("approval.requested", item, idempotency_key=f"appr-{item['approval_id']}-req")
        self._project_new()
        return dict(self._items[item["approval_id"]])

    def decide(self, approval_id: str, approver: str, approve: bool) -> dict:
        cur = self._require(approval_id)
        if cur["status"] != "pending":
            raise ValueError(f"approval {approval_id} is {cur['status']}, not pending")
        if approver == cur["requester"]:
            raise ValueError("requester cannot approve own request")
        ev = "approval.approved" if approve else "approval.refused"
        self.store.append(ev, {"approval_id": approval_id, "approver": approver, "decided_at": self.now()},
                          idempotency_key=f"appr-{approval_id}-decided")
        self._project_new()
        return dict(self._items[approval_id])

    def consume(self, approval_id: str, tool: str, args: dict) -> dict:
        """执行闸门：仅 approved 且 actionHash 匹配才放行，且一次性."""
        cur = self._require(approval_id)
        if cur["status"] != "approved":
            raise ValueError(f"approval {approval_id} is {cur['status']}, cannot execute")
        if action_hash(tool, args) != cur["action_hash"]:
            raise ValueError("action does not match approved actionHash")
        self.store.append("approval.executed", {"approval_id": approval_id, "executed_at": self.now()},
                          idempotency_key=f"appr-{approval_id}-executed")
        self._project_new()
        return {"approval_id": approval_id, "tool": cur["tool"], "args": cur["args"]}

    def expire_stale(self) -> list[dict]:
        out = []
        now = self.now()
        for a in list(self._items.values()):
            if a["status"] == "pending" and now - a["requested_at"] > self.ttl:
                self.store.append("approval.expired", {"approval_id": a["approval_id"]},
                                  idempotency_key=f"appr-{a['approval_id']}-expired")
                out.append(a)
        if out:
            self._project_new()
        return [dict(a) for a in out]

    def get(self, approval_id: str) -> dict:
        return dict(self._require(approval_id))

    def list_for(self, group_id: str) -> list[dict]:
        return [dict(a) for a in self._items.values() if a["group_id"] == group_id]

    def _require(self, approval_id: str) -> dict:
        if approval_id not in self._items:
            raise ValueError(f"unknown approval {approval_id}")
        return self._items[approval_id]

    # ---- projection ----
    def _project_new(self) -> None:
        events = self.store.since(self._cursor)
        for e in events:
            self._project(e)
        if events:
            self._cursor = events[-1]["seq"]

    def _project(self, e: dict) -> None:
        k, p = e["kind"], e["payload"]
        if k == "approval.requested":
            self._items[p["approval_id"]] = {**p, "status": "pending"}
        elif k == "approval.approved":
            self._items[p["approval_id"]].update(status="approved", approver=p["approver"])
        elif k == "approval.refused":
            self._items[p["approval_id"]].update(status="refused", approver=p["approver"])
        elif k == "approval.executed":
            self._items[p["approval_id"]].update(status="executed", executed_at=p["executed_at"])
        elif k == "approval.expired":
            if self._items[p["approval_id"]]["status"] == "pending":
                self._items[p["approval_id"]]["status"] = "expired"
