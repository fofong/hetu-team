"""A2A Gateway — 注册·验签·委派·状态回传·恢复 (§二), 复用 Hetu a2a 插件 bearer-token 模式.

三层 ID 映射: Bot-team task_id ↔ Hetu native task_id ↔ A2A task_id/context_id.
"""
from __future__ import annotations

import uuid

from .events import EventStore

TERMINAL_STATES = {"completed", "failed", "canceled"}


def http_transport(endpoint: str, payload: dict, token: str) -> dict:
    """生产出站实现（bearer 鉴权，对齐 Hetu a2a client）."""
    import httpx

    r = httpx.post(endpoint, json=payload, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    return r.json()


class A2AGateway:
    def __init__(self, store: EventStore, transport=None, persist_registry: bool = True):
        self.store = store
        self.transport = transport or http_transport
        self.persist = persist_registry
        self._agents: dict[str, dict] = {}
        self._delegations: dict[str, dict] = {}  # botteam_task_id -> delegation
        self._a2a_index: dict[str, str] = {}  # a2a_task_id -> botteam_task_id
        self._stale: set[str] = set()
        if persist_registry:
            self._replay()

    # ---- registry ----
    def register_agent(self, agent_id: str, endpoint: str, capabilities: list[str], token: str) -> dict:
        rec = {"agent_id": agent_id, "endpoint": endpoint, "capabilities": capabilities}
        if self.persist:
            self.store.append("agent.registered", {**rec, "_token": token},
                              idempotency_key=f"agent-{agent_id}")
            self._replay()
        else:
            self._agents[agent_id] = {**rec, "_token": token}
        return rec

    def agent(self, agent_id: str) -> dict | None:
        return self._agents.get(agent_id)

    # ---- delegation ----
    def delegate(self, botteam_task_id: str, agent_id: str, prompt: str, group_id: str | None = None) -> dict:
        agent = self._agents.get(agent_id)
        if agent is None:
            raise ValueError(f"agent not registered: {agent_id}")
        a2a_task_id = uuid.uuid4().hex
        poll_mode = not agent["endpoint"]
        mapping = {"botteam_task_id": botteam_task_id, "a2a_task_id": a2a_task_id,
                   "agent_id": agent_id, "prompt": prompt, "queued": poll_mode, "group_id": group_id}
        self.store.append("delegation.created", mapping, idempotency_key=f"deleg-{botteam_task_id}")
        self._index(mapping)
        if poll_mode:
            # 轮询认领模式: 防火墙后适配器主动 claim, 不经推送
            self.store.append("delegation.queued", mapping,
                              idempotency_key=f"deleg-queued-{botteam_task_id}")
            return mapping
        resp = self.transport(agent["endpoint"], {"a2a_task_id": a2a_task_id, "prompt": prompt}, agent["_token"])
        self.store.append("delegation.dispatched", {**mapping, "response_state": resp.get("state")},
                          idempotency_key=f"deleg-dispatch-{botteam_task_id}")
        return mapping

    def pending_for(self, agent_id: str) -> list[dict]:
        """待认领队列: 该 agent 轮询模式且非终态的委派（事件重放派生, 重启安全）."""
        return [dict(d) for d in self._delegations.values()
                if d["agent_id"] == agent_id and d.get("queued") and d["state"] not in TERMINAL_STATES]

    def status_callback(self, a2a_task_id: str, state: str, result: str | None = None) -> dict:
        bt = self._a2a_index.get(a2a_task_id)
        if bt is None:
            raise ValueError(f"unknown a2a task: {a2a_task_id}")
        key = f"a2a-state-{a2a_task_id}-{state}"  # 幂等：重复回传只记一次
        self.store.append("a2a.state", {"botteam_task_id": bt, "a2a_task_id": a2a_task_id, "state": state, "result": result},
                          idempotency_key=key)
        self._delegations[bt]["state"] = state
        self._stale.discard(bt)
        return self._delegations[bt]

    def delegation_state(self, botteam_task_id: str) -> str:
        return self._delegations[botteam_task_id]["state"]

    # ---- ID mapping ----
    def resolve_a2a(self, a2a_task_id: str) -> dict:
        return self._delegations[self._a2a_index[a2a_task_id]]

    def resolve_botteam(self, botteam_task_id: str) -> dict:
        return self._delegations[botteam_task_id]

    # ---- reconciliation & recovery ----
    def mark_stale(self, botteam_task_id: str) -> None:
        self._stale.add(botteam_task_id)

    def reconcile(self) -> list[str]:
        """对账：running 且无终态回传的委派 → 待核查清单（不直接判失败）."""
        out = []
        for bt, d in self._delegations.items():
            if bt in self._stale and d["state"] not in TERMINAL_STATES:
                out.append(bt)
        return out

    def _replay(self) -> None:
        for e in self.store.since(0):
            p = e["payload"]
            if e["kind"] == "agent.registered":
                self._agents[p["agent_id"]] = p
            elif e["kind"] == "delegation.created":
                self._index(p)
            elif e["kind"] == "a2a.state":
                self._delegations[p["botteam_task_id"]]["state"] = p["state"]

    def _index(self, mapping: dict) -> None:
        self._delegations[mapping["botteam_task_id"]] = {**mapping, "state": "submitted"}
        self._a2a_index[mapping["a2a_task_id"]] = mapping["botteam_task_id"]
