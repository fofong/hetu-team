"""服务端好友列表（事件溯源）——QQ 好友不再困在浏览器 localStorage."""
from __future__ import annotations

from .events import EventStore


class FriendService:
    def __init__(self, store: EventStore):
        self.store = store
        self._friends: list[dict] = []
        self._cursor = 0
        self._project_new()

    def add(self, name: str, kind: str = "human") -> dict:
        if any(f["name"] == name for f in self._friends):
            raise ValueError(f"friend exists: {name}")
        f = {"name": name, "kind": kind}
        self.store.append("friend.added", f, idempotency_key=f"friend-{name}")
        self._project_new()
        return f

    def remove(self, name: str) -> bool:
        if not any(f["name"] == name for f in self._friends):
            return False
        self.store.append("friend.removed", {"name": name},
                          idempotency_key=f"friend-rm-{name}")
        self._project_new()
        return True

    def list(self) -> list[dict]:
        return list(self._friends)

    def _project_new(self) -> None:
        events = self.store.since(self._cursor)
        for e in events:
            p = e["payload"]
            if e["kind"] == "friend.added":
                self._friends.append(p)
            elif e["kind"] == "friend.removed":
                self._friends = [f for f in self._friends if f["name"] != p["name"]]
        if events:
            self._cursor = events[-1]["seq"]
