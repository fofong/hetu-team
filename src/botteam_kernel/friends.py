"""服务端好友体系（事件溯源）——QQ 好友 + 双向互加（好友请求/同意）.

边模型：好友=无向边 {a,b}；请求=有向 pending。旧事件 friend.added {name}
投影为 (沣锅, name) 边（本部署唯一人类主账号），向后兼容。
"""
from __future__ import annotations

from .events import EventStore

OWNER = "沣锅"


class FriendService:
    def __init__(self, store: EventStore):
        self.store = store
        self._friends: list[dict] = []      # 主账号视角列表（客户端兼容）
        self._edges: set[frozenset] = set()
        self._requests: list[dict] = []
        self._cursor = 0
        self._project_new()

    def add(self, name: str, kind: str = "human", by: str = OWNER) -> dict:
        if any(f["name"] == name for f in self._friends):
            raise ValueError(f"friend exists: {name}")
        self.store.append("friend.added", {"name": name, "kind": kind, "by": by},
                          idempotency_key=f"friend-{name}")
        self._project_new()
        return {"name": name, "kind": kind}

    def remove(self, name: str) -> bool:
        if not any(f["name"] == name for f in self._friends):
            return False
        self.store.append("friend.removed", {"name": name},
                          idempotency_key=f"friend-rm-{name}")
        self._project_new()
        return True

    def request(self, frm: str, to: str) -> dict:
        if frm == to:
            raise ValueError("cannot add yourself")
        if frozenset((frm, to)) in self._edges:
            raise ValueError("already friends")
        if any(r["from"] == frm and r["to"] == to for r in self._requests):
            raise ValueError("request pending")
        self.store.append("friend.requested", {"from": frm, "to": to},
                          idempotency_key=f"freq-{frm}-{to}")
        self._project_new()
        return {"from": frm, "to": to}

    def approve(self, frm: str, to: str) -> dict:
        if not any(r["from"] == frm and r["to"] == to for r in self._requests):
            raise ValueError("no such request")
        self.store.append("friend.approved", {"from": frm, "to": to},
                          idempotency_key=f"fapp-{frm}-{to}")
        self._project_new()
        return {"from": frm, "to": to}

    def decline(self, frm: str, to: str) -> bool:
        if not any(r["from"] == frm and r["to"] == to for r in self._requests):
            return False
        self.store.append("friend.declined", {"from": frm, "to": to},
                          idempotency_key=f"fdec-{frm}-{to}")
        self._project_new()
        return True

    def are_friends(self, a: str, b: str) -> bool:
        return a != b and frozenset((a, b)) in self._edges

    def list_requests(self) -> list[dict]:
        return list(self._requests)

    def list(self) -> list[dict]:
        return list(self._friends)

    def _project_new(self) -> None:
        events = self.store.since(self._cursor)
        for e in events:
            p = e["payload"]
            if e["kind"] == "friend.added":
                self._friends.append({"name": p["name"], "kind": p.get("kind", "human")})
                self._edges.add(frozenset((p.get("by", OWNER), p["name"])))
            elif e["kind"] == "friend.removed":
                self._friends = [f for f in self._friends if f["name"] != p["name"]]
                self._edges = {ed for ed in self._edges if ed != frozenset((OWNER, p["name"]))}
            elif e["kind"] == "friend.requested":
                self._requests.append({"from": p["from"], "to": p["to"]})
            elif e["kind"] == "friend.approved":
                self._requests = [r for r in self._requests if not (r["from"] == p["from"] and r["to"] == p["to"])]
                self._edges.add(frozenset((p["from"], p["to"])))
                for me, other in ((p["from"], p["to"]), (p["to"], p["from"])):
                    if me == OWNER and not any(f["name"] == other for f in self._friends):
                        self._friends.append({"name": other, "kind": "human"})
            elif e["kind"] == "friend.declined":
                self._requests = [r for r in self._requests if not (r["from"] == p["from"] and r["to"] == p["to"])]
        if events:
            self._cursor = events[-1]["seq"]
