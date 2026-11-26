"""Phase 2: 单 Bot 独立记忆系统 + 人格/任务设定（TDD）.

设计对齐 v3 §6.3：记忆按 (group, agent) 命名空间严格隔离，聊天共识不得自动晋升；
记忆为显式写入（API/UI），召回=关键词相关度+新近度。人格=群成员 persona，
委派时服务端组装 prompt = 人格 + 召回记忆 + 任务（无 persona 无记忆则原样，反例）。
"""
from botteam_kernel.events import EventStore
from botteam_kernel.memory import MemoryService


def test_memory_namespace_isolation(tmp_path):
    """铁律: 记忆按 (群, agent) 隔离，跨 agent/跨群一律不可见（反例对）."""
    ms = MemoryService(EventStore(str(tmp_path / "m.db")))
    ms.remember("g1", "hetu", "沣锅的狗叫豆豆")
    assert ms.recall("g1", "hetu", "狗")[0]["text"] == "沣锅的狗叫豆豆"
    assert ms.recall("g1", "worker", "狗") == []  # 反例: 别的 agent 看不到
    assert ms.recall("g2", "hetu", "狗") == []    # 反例: 别的群看不到


def test_memory_recall_relevance_and_recency(tmp_path):
    ms = MemoryService(EventStore(str(tmp_path / "m.db")))
    ms.remember("g1", "a", "沣锅喜欢美式咖啡不加糖")
    ms.remember("g1", "a", "项目部署在 Host168")
    ms.remember("g1", "a", "沣锅的狗叫豆豆，今年三岁")
    hits = ms.recall("g1", "a", "狗")
    assert hits and hits[0]["text"].startswith("沣锅的狗叫豆豆")
    assert all("咖啡" not in h["text"] for h in hits)  # 反例: 无关条目不进召回
    recent = ms.recall("g1", "a", "", limit=1)  # 空查询按新近度
    assert recent[0]["text"].startswith("沣锅的狗叫豆豆")


def test_memory_cap_eviction(tmp_path):
    """容量封顶: 超限逐出最旧（防无限膨胀）."""
    ms = MemoryService(EventStore(str(tmp_path / "m.db")), cap=3)
    for i in range(4):
        ms.remember("g1", "a", f"记忆{i}")
    items = ms.recall("g1", "a", "")  # 召回语义: 新近优先（新→旧）
    assert [x["text"] for x in items] == ["记忆3", "记忆2", "记忆1"]


def test_memory_forget_and_restart(tmp_path):
    db = str(tmp_path / "m.db")
    ms = MemoryService(EventStore(db))
    m = ms.remember("g1", "a", "临时信息")
    ms.forget("g1", "a", m["memory_id"])
    assert ms.recall("g1", "a", "临时") == []
    ms2 = MemoryService(EventStore(db))  # 事件溯源: 重启后仍是已遗忘状态
    assert ms2.recall("g1", "a", "临时") == []
    assert len(ms2.recall("g1", "a", "")) == 0


def test_persona_prompt_composition(tmp_path):
    """委派 prompt = 人格 + 召回记忆 + 任务；无设定则原样（反例）."""
    from fastapi.testclient import TestClient

    from botteam_kernel.server import create_app

    c = TestClient(create_app(str(tmp_path / "p.db")))
    g = c.post("/groups", json={"name": "g", "mode": "default_single"}).json()["group_id"]
    m = c.post(f"/groups/{g}/members", json={"kind": "agent", "name": "hetu-worker",
                                             "capabilities": ["general"]}).json()
    c.post("/gateway/agents", json={"agent_id": "hetu-worker", "endpoint": "",
                                    "capabilities": ["general"], "token": "t"})
    # 反例: 无人格无记忆 → prompt 原样
    c.post(f"/groups/{g}/messages", json={"sender": "沣锅", "text": "@hetu-worker 查一下天气"})
    d = c.get("/gateway/agents/hetu-worker/pending").json()
    assert d[-1]["prompt"] == "@hetu-worker 查一下天气"
    # 设人格 + 写记忆
    c.post(f"/groups/{g}/members/{m['member_id']}/persona",
           json={"persona": "你是猫娘助手，句尾带喵", "task": "负责日常闲聊"})
    c.post(f"/groups/{g}/agents/hetu-worker/memory", json={"text": "沣锅的狗叫豆豆"})
    c.post(f"/groups/{g}/messages", json={"sender": "沣锅", "text": "@hetu-worker 我狗叫什么"})
    d = c.get("/gateway/agents/hetu-worker/pending").json()
    p = d[-1]["prompt"]
    assert "你是猫娘助手，句尾带喵" in p
    assert "沣锅的狗叫豆豆" in p
    assert "我狗叫什么" in p
    assert "负责日常闲聊" in p


def test_persona_restart_persistence(tmp_path):
    from fastapi.testclient import TestClient

    from botteam_kernel.server import create_app

    db = str(tmp_path / "pp.db")
    c = TestClient(create_app(db))
    g = c.post("/groups", json={"name": "g"}).json()["group_id"]
    m = c.post(f"/groups/{g}/members", json={"kind": "agent", "name": "a1"}).json()
    c.post(f"/groups/{g}/members/{m['member_id']}/persona", json={"persona": "严谨工程师", "task": ""})
    c2 = TestClient(create_app(db))
    members = c2.get(f"/groups/{g}/members").json()
    assert [x for x in members if x["member_id"] == m["member_id"]][0]["persona"] == "严谨工程师"


def test_memory_http_endpoints(tmp_path):
    from fastapi.testclient import TestClient

    from botteam_kernel.server import create_app

    c = TestClient(create_app(str(tmp_path / "mh.db")))
    g = c.post("/groups", json={"name": "g"}).json()["group_id"]
    m = c.post(f"/groups/{g}/agents/hetu/memory", json={"text": "沣锅喜欢黑色键盘"}).json()
    assert c.get(f"/groups/{g}/agents/hetu/memory?q=键盘").json()[0]["text"] == "沣锅喜欢黑色键盘"
    assert c.delete(f"/groups/{g}/agents/hetu/memory/{m['memory_id']}").json()["forgotten"] is True
    assert c.get(f"/groups/{g}/agents/hetu/memory").json() == []
