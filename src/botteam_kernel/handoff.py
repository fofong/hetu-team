"""结构化交接 + 熔断护栏（TeamAgentX 借鉴）.

Agent 完成回传可携带结构化交接目标（等价 mention_agents，非自由文本 @）。
护栏顺序（先窄后宽，预算按尝试预留——保守防失控）：
fanout（单棒目标数）→ revisit（血缘重访，环检测宽松版）→ budget（rootMessageId 全局派发预算）
→ depth（链深度）→ dispatch_cap（两次人类发言间连续自动派发封顶）。
铁律：人类发言清零一切自动调度计数。全部事件溯源，重启可恢复。
"""
from __future__ import annotations

import uuid

from .events import EventStore


class HandoffService:
    def __init__(self, store: EventStore, fanout_max: int = 20, depth_max: int = 100,
                 budget_max: int = 20, revisit_max: int = 5, dispatch_cap: int = 5):
        self.store = store
        self.fanout_max = fanout_max
        self.depth_max = depth_max
        self.budget_max = budget_max
        self.revisit_max = revisit_max
        self.dispatch_cap = dispatch_cap
        self._consecutive: dict[str, int] = {}
        self._root_used: dict[str, int] = {}
        self._cursor = 0
        self._project_new()

    def handoff(self, group_id: str, from_agent: str, targets: list[dict], ctx: dict) -> dict:
        rejected: list[dict] = []
        survivors: list[dict] = []
        lineage = ctx.get("lineage", [])
        if len(targets) > self.fanout_max:
            rejected = [{"agent": t["agent"], "reason": "fanout_exceeded"} for t in targets]
            budget_reserved = 0
        else:
            for t in targets:
                if lineage.count(t["agent"]) >= self.revisit_max:
                    rejected.append({"agent": t["agent"], "reason": "revisit_exceeded"})
                elif self._root_used.get(ctx["root"], 0) >= self.budget_max:
                    rejected.append({"agent": t["agent"], "reason": "budget_exceeded"})
                else:
                    survivors.append(t)
            budget_reserved = len(survivors)
            if survivors and ctx.get("depth", 0) + 1 > self.depth_max:
                rejected += [{"agent": t["agent"], "reason": "depth_exceeded"} for t in survivors]
                survivors = []
            if survivors and self._consecutive.get(group_id, 0) >= self.dispatch_cap:
                rejected += [{"agent": t["agent"], "reason": "dispatch_cap"} for t in survivors]
                survivors = []
        self.store.append("handoff.attempt", {"group_id": group_id, "root": ctx["root"],
                                              "reserved": budget_reserved},
                          idempotency_key=f"handoff-attempt-{uuid.uuid4().hex}")
        if survivors:
            self.store.append("handoff.accepted", {"group_id": group_id, "from": from_agent,
                                                   "targets": [t["agent"] for t in survivors]},
                              idempotency_key=f"handoff-acc-{uuid.uuid4().hex}")
        self._project_new()
        next_ctx = None
        if survivors:
            next_ctx = {"root": ctx["root"], "depth": ctx.get("depth", 0) + 1,
                        "lineage": lineage + [from_agent] + [t["agent"] for t in survivors]}
        return {"accepted": survivors, "rejected": rejected, "next_ctx": next_ctx}

    def human_message(self, group_id: str) -> None:
        if self._consecutive.get(group_id, 0) == 0:
            return  # 无计数可清不发事件，保持事件流干净
        self.store.append("handoff.human_reset", {"group_id": group_id},
                          idempotency_key=f"human-{group_id}-{uuid.uuid4().hex}")
        self._project_new()

    def consecutive(self, group_id: str) -> int:
        return self._consecutive.get(group_id, 0)

    # ---- projection ----
    def _project_new(self) -> None:
        events = self.store.since(self._cursor)
        for e in events:
            self._project(e)
        if events:
            self._cursor = events[-1]["seq"]

    def _project(self, e: dict) -> None:
        k, p = e["kind"], e["payload"]
        if k == "handoff.attempt":
            self._root_used[p["root"]] = self._root_used.get(p["root"], 0) + p["reserved"]
        elif k == "handoff.accepted":
            self._consecutive[p["group_id"]] = self._consecutive.get(p["group_id"], 0) + 1
        elif k == "handoff.human_reset":
            self._consecutive[p["group_id"]] = 0
