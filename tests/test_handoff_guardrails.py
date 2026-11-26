"""切片11 — 结构化交接与熔断护栏（TeamAgentX 借鉴：fanout/depth/budget/revisit + 人类发言清零）."""
import pytest
from fastapi.testclient import TestClient

from botteam_kernel.events import EventStore
from botteam_kernel.handoff import HandoffService
from botteam_kernel.server import create_app


@pytest.fixture
def hs(tmp_path):
    return HandoffService(EventStore(str(tmp_path / "h.db")), fanout_max=3, depth_max=2,
                          budget_max=5, revisit_max=1, dispatch_cap=3)


def _ctx(root="root1", depth=0, lineage=()):
    return {"root": root, "depth": depth, "lineage": list(lineage)}


def test_simple_handoff_accepted(hs):
    out = hs.handoff("g1", from_agent="a", targets=[{"agent": "b", "task": "审查"}], ctx=_ctx())
    assert [t["agent"] for t in out["accepted"]] == ["b"]
    assert out["next_ctx"]["depth"] == 1 and out["next_ctx"]["lineage"] == ["a", "b"]


def test_counterexample_fanout_exceeded(hs):
    out = hs.handoff("g1", "a", [{"agent": f"t{i}", "task": "x"} for i in range(4)], _ctx())
    assert out["accepted"] == []
    assert out["rejected"][0]["reason"] == "fanout_exceeded"


def test_counterexample_depth_exceeded(hs):
    out = hs.handoff("g1", "a", [{"agent": "b", "task": "x"}], _ctx(depth=2))
    assert out["rejected"][0]["reason"] == "depth_exceeded"


def test_counterexample_root_budget_exceeded(hs):
    for _ in range(5):
        hs.handoff("g1", "a", [{"agent": "b", "task": "x"}], _ctx())
    out = hs.handoff("g1", "a", [{"agent": "b", "task": "x"}], _ctx())
    assert out["rejected"][0]["reason"] == "budget_exceeded"


def test_counterexample_revisit_exceeded(hs):
    """环检测：目标在血缘中重访超上限即拒（宽松版 A→B→A→B）."""
    hs.handoff("g1", "a", [{"agent": "b", "task": "x"}], _ctx(lineage=["a"]))
    out = hs.handoff("g1", "b", [{"agent": "a", "task": "y"}], _ctx(depth=1, lineage=["a", "b"]))
    out2 = hs.handoff("g1", "a", [{"agent": "b", "task": "z"}], _ctx(depth=2, lineage=["a", "b", "a"]))
    assert out2["rejected"][0]["reason"] == "revisit_exceeded"


def test_human_message_resets_consecutive(hs):
    for _ in range(3):
        hs.handoff("g1", "a", [{"agent": "b", "task": "x"}], _ctx())
    assert hs.consecutive("g1") == 3
    out = hs.handoff("g1", "a", [{"agent": "b", "task": "x"}], _ctx())
    assert out["rejected"][0]["reason"] == "dispatch_cap"  # 连续自动派发封顶
    hs.human_message("g1")  # 人类发言清零一切自动调度
    assert hs.consecutive("g1") == 0
    assert hs.handoff("g1", "a", [{"agent": "b", "task": "x"}], _ctx())["accepted"]


def test_restart_persistence(tmp_path):
    db = str(tmp_path / "r.db")
    h1 = HandoffService(EventStore(db), fanout_max=3, depth_max=2, budget_max=5, revisit_max=1, dispatch_cap=3)
    h1.handoff("g1", "a", [{"agent": "b", "task": "x"}], _ctx())
    h2 = HandoffService(EventStore(db), fanout_max=3, depth_max=2, budget_max=5, revisit_max=1, dispatch_cap=3)
    assert h2.consecutive("g1") == 1


# ---- server 接线：完成回传携带结构化交接 → 受护栏约束的下一棒委派 ----
def test_callback_handoff_delegates_next_hop(tmp_path):
    calls = []
    app = create_app(str(tmp_path / "s.db"),
                     transport=lambda ep, payload, token: calls.append(payload) or {"id": "x", "state": "submitted"})
    c = TestClient(app)
    g = c.post("/groups", json={"name": "g", "mode": "default_single"}).json()["group_id"]
    c.post("/gateway/agents", json={"agent_id": "a", "endpoint": "", "capabilities": [], "token": "t"})
    c.post("/gateway/agents", json={"agent_id": "b", "endpoint": "", "capabilities": [], "token": "t"})
    c.post("/gateway/delegate", json={"botteam_task_id": "bt1", "agent_id": "a", "prompt": "做", "group_id": g})
    tid = c.get("/gateway/delegations/bt1").json()["a2a_task_id"]
    r = c.post("/gateway/callback", json={"a2a_task_id": tid, "state": "completed", "result": "done",
                                          "handoff": [{"agent": "b", "task": "接力审查"}]})
    assert r.status_code == 200
    pend = c.get("/gateway/agents/b/pending").json()
    assert any("接力审查" in p["prompt"] for p in pend)
