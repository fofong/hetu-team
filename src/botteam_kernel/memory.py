"""单 Bot 独立记忆系统（事件溯源）.

铁律（v3 §6.3）：记忆按 (group, agent) 命名空间严格隔离——聊天共识不得自动晋升，
跨 agent/跨群一律不可见。写入=显式 API/UI；召回=关键词相关度（CJK 字符 bigram）
+ 新近度；容量封顶逐出最旧。全部事件溯源，重启可恢复。
"""
from __future__ import annotations

import re
import uuid

from .events import EventStore


def _tokens(text: str) -> set[str]:
    """关键词切分：ASCII 词 + CJK 连续串的单字与相邻 bigram."""
    out: set[str] = set()
    for w in re.findall(r"[a-zA-Z0-9_]+", text.lower()):
        out.add(w)
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        out.update(run)
        out.update(run[i:i + 2] for i in range(len(run) - 1))
    return out


class MemoryService:
    def __init__(self, store: EventStore, cap: int = 200):
        self.store = store
        self.cap = cap
        self._ns: dict[tuple[str, str], list[dict]] = {}
        self._index: dict[str, tuple[str, str]] = {}  # memory_id -> ns key
        self._cursor = 0
        self._project_new()

    def remember(self, group_id: str, agent_id: str, text: str, kind: str = "fact") -> dict:
        key = (group_id, agent_id)
        items = self._ns.setdefault(key, [])
        if len(items) >= self.cap:  # 封顶逐出最旧（事件化，重启一致）
            self.forget(group_id, agent_id, items[0]["memory_id"])
        item = {"memory_id": uuid.uuid4().hex, "group_id": group_id,
                "agent_id": agent_id, "text": text, "kind": kind}
        self.store.append("memory.remembered", item, idempotency_key=f"mem-{item['memory_id']}")
        self._project_new()
        return item

    def recall(self, group_id: str, agent_id: str, query: str, limit: int = 5) -> list[dict]:
        items = self._ns.get((group_id, agent_id), [])
        if not query.strip():
            return list(reversed(items[-limit:]))  # 空查询=纯新近度
        q = _tokens(query)
        scored = [(_tokens(m["text"]) & q, m) for m in items]
        scored = [(len(s), m) for s, m in scored if s]
        scored.sort(key=lambda x: x[0], reverse=True)  # 稳定排序: 同分保持新近优先
        return [m for _, m in scored[:limit]]

    def forget(self, group_id: str, agent_id: str, memory_id: str) -> bool:
        key = (group_id, agent_id)
        if memory_id not in [m["memory_id"] for m in self._ns.get(key, [])]:
            return False
        self.store.append("memory.forgotten", {"group_id": group_id, "agent_id": agent_id,
                                               "memory_id": memory_id},
                          idempotency_key=f"mem-forget-{memory_id}")
        self._project_new()
        return True

    # ---- projection ----
    def _project_new(self) -> None:
        events = self.store.since(self._cursor)
        for e in events:
            self._project(e)
        if events:
            self._cursor = events[-1]["seq"]

    def _project(self, e: dict) -> None:
        p = e["payload"]
        if e["kind"] == "memory.remembered":
            key = (p["group_id"], p["agent_id"])
            self._ns.setdefault(key, []).append(p)
            self._index[p["memory_id"]] = key
        elif e["kind"] == "memory.forgotten":
            key = (p["group_id"], p["agent_id"])
            self._ns[key] = [m for m in self._ns.get(key, []) if m["memory_id"] != p["memory_id"]]
