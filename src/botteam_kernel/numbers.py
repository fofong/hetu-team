"""纯数字 ID 系统（QQ 号/群号显示句柄）.

对齐 QQ 官方规则：QQ 号 5-11 位纯数字首位 1-9；群号 5-12 位纯数字。
本系统分配：成员 QQ 号 10 位（1000000001 起，按人全局唯一）、群号 8 位
（10000001 起）。事件溯源、创建即分配、幂等（重复分配返回同号）、重启不重发。
hex UUID 仍是内部主键——数字号只是 QQ 式用户可见句柄（QQ 自身 uin 与内部
id 也是两套），不破坏既有事件链/幂等/Worker 注册。
"""
from __future__ import annotations

import re

from .events import EventStore

QQ_RE = r"^[1-9]\d{9}$"        # 本系统分配的成员 QQ 号：10 位
GROUP_NO_RE = r"^[1-9]\d{7}$"  # 本系统分配的群号：8 位
_QQ_RULE = re.compile(r"^[1-9]\d{4,10}$")       # 官方规则 5-11 位
_GROUP_RULE = re.compile(r"^[1-9]\d{4,11}$")    # 官方规则 5-12 位
BASE_MEMBER, BASE_GROUP = 1_000_000_001, 10_000_001


def valid_qq(s: str) -> bool:
    return bool(_QQ_RULE.match(s))


def valid_group_no(s: str) -> bool:
    return bool(_GROUP_RULE.match(s))


class NumberService:
    def __init__(self, store: EventStore):
        self.store = store
        self._by_ref: dict[tuple, str] = {}
        self._count = {"member": 0, "group": 0}
        self._cursor = 0
        self._project_new()

    def assign(self, kind: str, ref: str) -> str:
        key = (kind, ref)
        if key in self._by_ref:
            return self._by_ref[key]
        base = BASE_MEMBER if kind == "member" else BASE_GROUP
        number = str(base + self._count[kind])
        self.store.append("number.assigned", {"kind": kind, "ref": ref, "number": number},
                          idempotency_key=f"num-{kind}-{ref}")
        self._project_new()
        return self._by_ref[key]

    def get(self, kind: str, ref: str) -> str | None:
        return self._by_ref.get((kind, ref))

    def list_all(self) -> list[dict]:
        return [{"kind": k[0], "ref": k[1], "number": v} for k, v in self._by_ref.items()]

    def _project_new(self) -> None:
        events = self.store.since(self._cursor)
        for e in events:
            if e["kind"] != "number.assigned":
                continue
            p = e["payload"]
            key = (p["kind"], p["ref"])
            if key in self._by_ref:
                continue  # 反例保护：同号不重发
            self._by_ref[key] = p["number"]
            self._count[p["kind"]] += 1
        if events:
            self._cursor = events[-1]["seq"]
