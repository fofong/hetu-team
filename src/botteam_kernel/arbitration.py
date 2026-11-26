"""Arbiter — 五种群组运行模式 (§5.1). 确定性规则优先短路，LLM 仲裁仅兜底（PoC 用规则路由）."""
from __future__ import annotations

MODES = {"default_silent", "default_single", "free_discussion", "collaborative", "independent_review"}

# 关键词 → 能力（PoC 规则路由；生产替换为小模型 Router）
_KEYWORD_CAPS = {
    "实现": "coding", "代码": "coding", "写": "coding", "功能": "coding",
    "安全": "security", "审查": "security", "评审": "research", "研究": "research", "价值": "research",
}


class Arbiter:
    def __init__(self, mode_enforced: bool = True):
        self.mode_enforced = mode_enforced

    def decide(self, mode: str, message: dict, agents: list[dict], max_responders: int = 3) -> list[str]:
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode}")
        mentioned = message["mentions"]["agents"]
        if not self.mode_enforced:
            return list(mentioned)  # 反例路径：无仲裁直接广播
        if mode == "default_silent":
            return list(mentioned)
        if mode == "default_single":
            if len(mentioned) <= 1:
                return list(mentioned)
            return [self._route(message["text"], mentioned, agents)]
        if mode == "free_discussion":
            return mentioned[:max_responders]
        if mode == "collaborative":
            return list(mentioned)  # 协同执行：命中者全部参与，受发言预算约束
        if mode == "independent_review":
            return list(mentioned)
        return []

    def decide_plan(self, mode: str, message: dict, agents: list[dict]) -> dict:
        responders = self.decide(mode, message, agents)
        plan = {"mode": mode, "responders": responders, "isolation": False, "views": {}}
        if mode == "independent_review":
            plan["isolation"] = True
            # 每个评审者只看到原始方案，看不到彼此输出
            plan["views"] = {rid: {"task": message["text"], "peer_outputs": []} for rid in responders}
        return plan

    @staticmethod
    def _route(text: str, mentioned: list[str], agents: list[dict]) -> str:
        by_id = {a["member_id"]: a for a in agents}
        for kw, cap in _KEYWORD_CAPS.items():
            if kw in text:
                for mid in mentioned:
                    if cap in by_id[mid].get("capabilities", []):
                        return mid
        return mentioned[0]
