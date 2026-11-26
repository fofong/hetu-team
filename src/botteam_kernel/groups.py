"""Group / Member / Message / Thread domain over the event store (§5.4)."""
from __future__ import annotations

import time
import uuid

from .events import EventStore


class GroupService:
    def __init__(self, store: EventStore):
        self.store = store
        self._groups: dict[str, dict] = {}
        self._members: dict[str, list[dict]] = {}
        self._messages: dict[str, dict] = {}
        self._group_seq: dict[str, int] = {}
        self._cursor = 0
        self._project_new()

    # ---- Group ----
    def create_group(self, name: str, mode: str = "default_single") -> dict:
        group = {"group_id": uuid.uuid4().hex, "name": name, "mode": mode}
        self.store.append("group.created", group, idempotency_key=f"group-{group['group_id']}")
        self._project_new()
        return group

    def update_settings(self, group_id: str, name: str | None = None, mode: str | None = None) -> dict:
        patch = {}
        if name is not None and name.strip():
            patch["name"] = name.strip()
        if mode is not None:
            patch["mode"] = mode
        if patch:
            self.store.append("group.settings_set", {"group_id": group_id, **patch},
                              idempotency_key=f"set-{group_id}-{uuid.uuid4().hex}")
            self._project_new()
        return self._groups[group_id]

    # ---- Member ----
    def add_member(self, group_id: str, kind: str, name: str, capabilities: list[str] | None = None) -> dict:
        member = {
            "group_id": group_id,
            "member_id": uuid.uuid4().hex,
            "kind": kind,
            "name": name,
            "capabilities": capabilities or [],
            "status": "online",
        }
        self.store.append("member.joined", member, idempotency_key=f"member-{member['member_id']}")
        self._project_new()
        return member

    def remove_member(self, group_id: str, member_id: str) -> bool:
        if not any(m["member_id"] == member_id for m in self._members.get(group_id, [])):
            return False
        self.store.append("member.left", {"group_id": group_id, "member_id": member_id},
                          idempotency_key=f"left-{member_id}-{uuid.uuid4().hex}")
        self._project_new()
        return True

    # ---- Persona（群成员人格/任务设定）----
    def set_persona(self, group_id: str, member_id: str, persona: str, task: str = "") -> dict | None:
        self.store.append("member.persona_set",
                          {"group_id": group_id, "member_id": member_id,
                           "persona": persona, "task": task},
                          idempotency_key=f"persona-{member_id}-{uuid.uuid4().hex}")
        self._project_new()
        return self.member(member_id)

    def member(self, member_id: str) -> dict | None:
        for ms in self._members.values():
            for m in ms:
                if m["member_id"] == member_id:
                    return m
        return None

    def persona_of(self, group_id: str, name: str) -> tuple[str, str]:
        for m in self._members.get(group_id, []):
            if m["name"] == name:
                return m.get("persona", ""), m.get("task", "")
        return "", ""

    # ---- Announcement（群公告 = 该群长期系统提示，QQ 范式）----
    def set_announcement(self, group_id: str, text: str, set_by: str = "") -> dict:
        ann = {"text": text, "set_by": set_by, "at": time.time()}
        self.store.append("group.announcement_set", {"group_id": group_id, **ann},
                          idempotency_key=f"ann-{group_id}-{uuid.uuid4().hex}")
        self._project_new()
        return self._groups[group_id]["announcement"]

    # ---- Message / Thread ----
    def post_message(
        self,
        group_id: str,
        sender: str,
        text: str,
        reply_to: str | None = None,
        idempotency_key: str | None = None,
        raw_at_all: bool = False,
    ) -> dict:
        key = idempotency_key or f"msg-{uuid.uuid4().hex}"
        existing = self._find_by_key(key)
        if existing is not None:
            return existing
        thread_id = self._messages[reply_to]["thread_id"] if reply_to else uuid.uuid4().hex
        seq = self._group_seq.get(group_id, 0) + 1
        msg = {
            "group_id": group_id,
            "message_id": uuid.uuid4().hex,
            "thread_id": thread_id,
            "reply_to": reply_to,
            "sender": sender,
            "text": text,
            "group_seq": seq,
            "mentions": self._parse_mentions(group_id, text, raw_at_all),
        }
        self.store.append("message.created", msg, idempotency_key=key)
        self._project_new()
        return msg

    def get_message(self, message_id: str) -> dict | None:
        return self._messages.get(message_id)

    def group_message_count(self, group_id: str) -> int:
        return self._group_seq.get(group_id, 0)

    def members(self, group_id: str) -> list[dict]:
        return list(self._members.get(group_id, []))

    def messages(self, group_id: str) -> list[dict]:
        return sorted(
            (m for m in self._messages.values() if m["group_id"] == group_id),
            key=lambda m: m["group_seq"],
        )

    # ---- internals ----
    def _parse_mentions(self, group_id: str, text: str, raw_at_all: bool) -> dict:
        members = self._members.get(group_id, [])
        humans = [m["name"] for m in members if m["kind"] == "human"]
        agents = [m["member_id"] for m in members if m["kind"] == "agent"]
        if raw_at_all and "@全体" in text:
            return {"humans": humans, "agents": agents}
        hs = list(humans) if "@全体人类" in text else []
        ags = list(agents) if "@全体Agent" in text else []
        for m in members:
            if f"@{m['name']}" in text:
                target = hs if m["kind"] == "human" else ags
                val = m["name"] if m["kind"] == "human" else m["member_id"]
                if val not in target:
                    target.append(val)
        return {"humans": hs, "agents": ags}

    def _find_by_key(self, key: str) -> dict | None:
        row = self.store._db.execute(
            "SELECT payload FROM events WHERE idempotency_key=?", (key,)
        ).fetchone()
        import json

        return json.loads(row[0]) if row else None

    def _project_new(self) -> None:
        events = self.store.since(self._cursor)
        for e in events:
            self._project(e)
        if events:
            self._cursor = events[-1]["seq"]

    def _project(self, e: dict) -> None:
        p = e["payload"]
        if e["kind"] == "group.created":
            self._groups[p["group_id"]] = p
            self._members.setdefault(p["group_id"], [])
        elif e["kind"] == "group.settings_set":
            if p["group_id"] in self._groups:
                for k in ("name", "mode"):
                    if k in p:
                        self._groups[p["group_id"]][k] = p[k]
        elif e["kind"] == "member.left":
            self._members[p["group_id"]] = [m for m in self._members.get(p["group_id"], [])
                                            if m["member_id"] != p["member_id"]]
        elif e["kind"] == "member.joined":
            self._members.setdefault(p["group_id"], []).append(p)
        elif e["kind"] == "member.persona_set":
            for m in self._members.get(p["group_id"], []):
                if m["member_id"] == p["member_id"]:
                    m["persona"] = p["persona"]
                    m["task"] = p["task"]
        elif e["kind"] == "group.announcement_set":
            if p["group_id"] in self._groups:
                self._groups[p["group_id"]]["announcement"] = {
                    "text": p["text"], "set_by": p["set_by"], "at": p["at"]}
        elif e["kind"] == "message.created":
            p.setdefault("created_at", e["ts"])  # 事件信封时间回填（历史消息亦可追溯）
            self._messages[p["message_id"]] = p
            self._group_seq[p["group_id"]] = max(self._group_seq.get(p["group_id"], 0), p["group_seq"])
