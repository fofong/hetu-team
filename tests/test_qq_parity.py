"""QQ 对齐补全切片（清单逐条对照，2026-11-27）:
被动响应三档(仅@/关键词/全量) / 引用全文注入委派 / 撤回(副作用声明红线) / 表情回应 / 消息置顶 / 消息搜索.

反例对: mention_only 无 @ 不触发；无引用不注入引用段；撤回他人 403、重复撤回 400；
回应不存在消息 404；搜索不混入他群与不匹配项。
"""
import pytest
from fastapi.testclient import TestClient

from botteam_kernel.server import create_app


@pytest.fixture()
def client(tmp_path):
    app = create_app(db_path=str(tmp_path / "qq.db"))
    with TestClient(app) as c:
        yield c


def _group(client, name="对齐群"):
    return client.post("/groups", json={"name": name, "mode": "default_single"}).json()["group_id"]


def _register(client, agent_id):
    # endpoint 空 = 轮询认领模式
    client.post("/gateway/agents", json={"agent_id": agent_id, "endpoint": "", "capabilities": [], "token": "t"})


def _pending(client, agent_id):
    return client.get(f"/gateway/agents/{agent_id}/pending").json()


# ---- P0 被动响应三档 ----

def test_keyword_response_mode(client):
    gid = _group(client)
    m = client.post(f"/groups/{gid}/members", json={"kind": "agent", "name": "kw-bot"}).json()
    client.post(f"/groups/{gid}/members/{m['member_id']}/response_mode",
                json={"mode": "keyword", "keywords": ["查一下"]})
    _register(client, "kw-bot")
    client.post(f"/groups/{gid}/messages", json={"sender": "沣锅", "text": "帮我查一下明天天气"})
    assert len(_pending(client, "kw-bot")) == 1  # 关键词命中 → 委派
    # 反例: 未命中关键词不触发
    client.post(f"/groups/{gid}/messages", json={"sender": "沣锅", "text": "随便聊聊"})
    assert len(_pending(client, "kw-bot")) == 1


def test_full_listen_and_mention_only_counterexample(client):
    gid = _group(client)
    full = client.post(f"/groups/{gid}/members", json={"kind": "agent", "name": "watch-bot"}).json()
    silent = client.post(f"/groups/{gid}/members", json={"kind": "agent", "name": "quiet-bot"}).json()
    client.post(f"/groups/{gid}/members/{full['member_id']}/response_mode", json={"mode": "full"})
    _register(client, "watch-bot")
    _register(client, "quiet-bot")
    client.post(f"/groups/{gid}/messages", json={"sender": "沣锅", "text": "随口一句话"})
    assert len(_pending(client, "watch-bot")) == 1
    assert len(_pending(client, "quiet-bot")) == 0  # 反例: 默认仅@响应，无 @ 不触发


def test_response_mode_survives_restart(tmp_path):
    db = str(tmp_path / "rm.db")
    with TestClient(create_app(db_path=db)) as c:
        gid = c.post("/groups", json={"name": "g"}).json()["group_id"]
        m = c.post(f"/groups/{gid}/members", json={"kind": "agent", "name": "b"}).json()
        c.post(f"/groups/{gid}/members/{m['member_id']}/response_mode",
               json={"mode": "keyword", "keywords": ["k"]})
    with TestClient(create_app(db_path=db)) as c2:  # 事件溯源重启恢复
        ms = c2.get(f"/groups/{gid}/members").json()
        assert ms[0]["response_mode"] == "keyword" and ms[0]["keywords"] == ["k"]


# ---- P0 引用全文注入委派 prompt ----

def test_quote_injected_into_delegation_prompt(client):
    gid = _group(client)
    client.post(f"/groups/{gid}/members", json={"kind": "agent", "name": "q-bot"})
    _register(client, "q-bot")
    a = client.post(f"/groups/{gid}/messages", json={"sender": "沣锅", "text": "服务器昨晚 OOM 了"}).json()
    client.post(f"/groups/{gid}/messages",
                json={"sender": "沣锅", "text": "@q-bot 分析下", "reply_to": a["message_id"]})
    pend = _pending(client, "q-bot")
    assert len(pend) == 1
    assert "【引用消息】沣锅: 服务器昨晚 OOM 了" in pend[0]["prompt"]
    # 反例: 无引用的委派不含引用段
    client.post(f"/groups/{gid}/messages", json={"sender": "沣锅", "text": "@q-bot 现在几点"})
    pend2 = _pending(client, "q-bot")
    assert len(pend2) == 2 and "【引用消息】" not in pend2[1]["prompt"]


# ---- P1 撤回（红线: 撤回必须声明副作用状态）----

def test_recall_own_message_with_side_effect(client):
    gid = _group(client)
    m = client.post(f"/groups/{gid}/messages", json={"sender": "沣锅", "text": "说错了"}).json()
    r = client.post(f"/groups/{gid}/messages/{m['message_id']}/recall",
                    json={"by": "沣锅", "side_effect": "none"})
    assert r.status_code == 200
    ms = client.get(f"/groups/{gid}/messages").json()
    assert ms[0]["recalled"] is True and ms[0]["side_effect"] == "none"


def test_recall_counterexamples(client):
    gid = _group(client)
    m = client.post(f"/groups/{gid}/messages", json={"sender": "沣锅", "text": "我的消息"}).json()
    bad = client.post(f"/groups/{gid}/messages/{m['message_id']}/recall", json={"by": "别人"})
    assert bad.status_code == 403  # 反例: 只能撤回自己的
    client.post(f"/groups/{gid}/messages/{m['message_id']}/recall", json={"by": "沣锅"})
    again = client.post(f"/groups/{gid}/messages/{m['message_id']}/recall", json={"by": "沣锅"})
    assert again.status_code == 400  # 反例: 重复撤回
    assert client.post("/groups/%s/messages/nope/recall" % gid, json={"by": "沣锅"}).status_code == 404


# ---- P1 表情回应（人类低摩擦确认入口）----

def test_react_toggle_and_counterexample(client):
    gid = _group(client)
    m = client.post(f"/groups/{gid}/messages", json={"sender": "hetu", "text": "任务完成"}).json()
    client.post(f"/groups/{gid}/messages/{m['message_id']}/react", json={"member": "沣锅", "emoji": "👍"})
    ms = client.get(f"/groups/{gid}/messages").json()
    assert ms[0]["reactions"]["👍"] == ["沣锅"]
    client.post(f"/groups/{gid}/messages/{m['message_id']}/react", json={"member": "沣锅", "emoji": "👍"})
    ms = client.get(f"/groups/{gid}/messages").json()
    assert "👍" not in ms[0].get("reactions", {}) or not ms[0]["reactions"].get("👍")  # 再点取消
    assert client.post("/groups/%s/messages/nope/react" % gid,
                       json={"member": "沣锅", "emoji": "👍"}).status_code == 404


# ---- P1 消息置顶（任务看板入口）----

def test_pin_unpin_and_counterexample(client):
    gid = _group(client)
    m = client.post(f"/groups/{gid}/messages", json={"sender": "沣锅", "text": "本周重点：完成验收"}).json()
    client.post(f"/groups/{gid}/messages/{m['message_id']}/pin", json={"by": "沣锅"})
    pins = client.get(f"/groups/{gid}/pins").json()
    assert [p["message_id"] for p in pins] == [m["message_id"]]
    client.delete(f"/groups/{gid}/messages/{m['message_id']}/pin")
    assert client.get(f"/groups/{gid}/pins").json() == []
    assert client.post("/groups/%s/messages/nope/pin" % gid, json={"by": "沣锅"}).status_code == 404


# ---- P1 消息搜索 ----

def test_search_messages_by_keyword_and_sender(client):
    gid = _group(client)
    other = _group(client, "他群")
    client.post(f"/groups/{gid}/messages", json={"sender": "沣锅", "text": "部署到 168"})
    client.post(f"/groups/{gid}/messages", json={"sender": "hetu", "text": "168 部署完成"})
    client.post(f"/groups/{other}/messages", json={"sender": "沣锅", "text": "别群的部署"})
    hits = client.get(f"/groups/{gid}/messages", params={"q": "168"}).json()
    assert len(hits) == 2
    hits2 = client.get(f"/groups/{gid}/messages", params={"q": "部署", "sender": "hetu"}).json()
    assert len(hits2) == 1 and hits2[0]["sender"] == "hetu"  # 反例: 发送者过滤
    assert len(client.get(f"/groups/{gid}/messages", params={"q": "不存在词"}).json()) == 0
