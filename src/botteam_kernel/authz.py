"""分级授权 L0-L3 (§5.3). 铁律: 聊天内容可表达任务意图，但不能自行扩大权限；
实际执行权限由独立授权系统判定，只接受结构化事件（认证身份 + 时间戳 + nonce + 幂等键）."""
from __future__ import annotations

LEVELS = ["L0", "L1", "L2", "L3"]

# 动作 → 所需最低级别
_ACTION_LEVEL = {
    "tool:read_file": "L0",
    "tool:exec_code": "L1",
    "file:write_workspace": "L1",
    "file:write_prod": "L2",
    "system:config": "L3",
}


class AuthorizationSystem:
    def __init__(self, parse_chat_text_as_grants: bool = False):
        self._levels: dict[str, str] = {}
        self._nonces: set[str] = set()
        self._approvals: set[tuple[str, str]] = set()
        self._audit: list[dict] = []
        self.parse_chat_text = parse_chat_text_as_grants  # 反例开关（生产必须 False）

    def level_of(self, principal: str) -> str:
        return self._levels.get(principal, "L0")

    def grant(self, principal: str, level: str, granted_by: str, nonce: str, ts: float) -> None:
        if level not in LEVELS:
            raise ValueError(f"unknown level {level}")
        if nonce in self._nonces:
            raise ValueError(f"nonce replay rejected: {nonce}")
        self._nonces.add(nonce)
        cur = LEVELS.index(self.level_of(principal))
        self._levels[principal] = level if LEVELS.index(level) > cur else self.level_of(principal)
        self._audit.append({"event": "grant", "principal": principal, "level": level, "by": granted_by, "ts": ts, "nonce": nonce})

    def approve(self, action: str, principal: str, approver: str, nonce: str, ts: float) -> None:
        if nonce in self._nonces:
            raise ValueError(f"nonce replay rejected: {nonce}")
        self._nonces.add(nonce)
        self._approvals.add((principal, action))
        self._audit.append({"event": "approve", "principal": principal, "action": action, "by": approver, "ts": ts, "nonce": nonce})

    def consume_approval(self, principal: str, action: str) -> bool:
        key = (principal, action)
        if key in self._approvals:
            self._approvals.discard(key)
            return True
        return False

    def check(self, principal: str, action: str) -> bool:
        required = _ACTION_LEVEL.get(action, "L3")
        if LEVELS.index(self.level_of(principal)) < LEVELS.index(required):
            return False
        if required == "L2":
            return (principal, action) in self._approvals  # L2 单次动作需审批
        return True

    def ingest_chat_text(self, principal: str, text: str) -> None:
        """聊天文本永远只是数据。仅当反例开关打开（错误实现）才解析为授权。"""
        if self.parse_chat_text and "授权" in text:
            for lv in LEVELS:
                if lv in text:
                    self._levels[principal] = lv
                    return

    def audit_log(self) -> list[dict]:
        return list(self._audit)
