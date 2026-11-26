"""Tracer bullet 2: Group / Member / Message / Thread (§5.4 九对象核心 + §四 @全体两层)."""
import pytest
from botteam_kernel.events import EventStore
from botteam_kernel.groups import GroupService


@pytest.fixture()
def svc(tmp_path):
    return GroupService(EventStore(str(tmp_path / "g.db")))


def test_create_group_records_group_created_event(svc):
    g = svc.create_group(name="Hetu 研发群")
    assert g["group_id"]
    events = svc.store.since(0)
    assert [e["kind"] for e in events] == ["group.created"]


def test_add_agent_member_with_profile(svc):
    gid = svc.create_group(name="g")["group_id"]
    m = svc.add_member(gid, kind="agent", name="Hetu", capabilities=["coding", "research"])
    assert m["member_id"]
    assert m["kind"] == "agent"
    assert m["status"] == "online"
    assert "coding" in m["capabilities"]


def test_post_message_persists_with_group_sequence(svc):
    gid = svc.create_group(name="g")["group_id"]
    svc.add_member(gid, kind="human", name="沣锅")
    m1 = svc.post_message(gid, sender="沣锅", text="第一条")
    m2 = svc.post_message(gid, sender="沣锅", text="第二条")
    assert (m1["group_seq"], m2["group_seq"]) == (1, 2)
    assert svc.get_message(m2["message_id"])["text"] == "第二条"


def test_reply_to_links_message_into_thread(svc):
    gid = svc.create_group(name="g")["group_id"]
    root = svc.post_message(gid, sender="沣锅", text="根消息")
    reply = svc.post_message(gid, sender="Hetu", text="回复", reply_to=root["message_id"])
    assert reply["thread_id"] == root["thread_id"]
    assert reply["reply_to"] == root["message_id"]


def test_duplicate_idempotency_key_returns_same_message(svc):
    gid = svc.create_group(name="g")["group_id"]
    a = svc.post_message(gid, sender="沣锅", text="hi", idempotency_key="cli-1")
    b = svc.post_message(gid, sender="沣锅", text="hi", idempotency_key="cli-1")
    assert a["message_id"] == b["message_id"]
    assert svc.group_message_count(gid) == 1


def test_mentions_split_at_all_humans_vs_agents(svc):
    """§四: @全体绝不唤醒全部 Agent —— 拆 @全体人类 / @全体Agent 两层."""
    gid = svc.create_group(name="g")["group_id"]
    svc.add_member(gid, kind="human", name="沣锅")
    hetu = svc.add_member(gid, kind="agent", name="Hetu", capabilities=["coding"])
    avatar = svc.add_member(gid, kind="agent", name="Avatar", capabilities=["security"])
    m = svc.post_message(gid, sender="沣锅", text="@全体Agent 汇报进展")
    assert m["mentions"] == {"humans": [], "agents": [hetu["member_id"], avatar["member_id"]]}
    m2 = svc.post_message(gid, sender="沣锅", text="@Hetu 写代码 @全体人类 注意")
    assert m2["mentions"] == {"humans": ["沣锅"], "agents": [hetu["member_id"]]}


def test_counterexample_single_reply_mode_would_route_at_all_to_everyone(svc):
    """反例：若不拆两层，@全体 会命中全部成员（广播唤醒全员，红线 §6.1-2）。"""
    gid = svc.create_group(name="g")["group_id"]
    svc.add_member(gid, kind="human", name="沣锅")
    svc.add_member(gid, kind="agent", name="Hetu")
    naive = svc.post_message(gid, sender="沣锅", text="@全体 都说话", raw_at_all=True)
    total = len(naive["mentions"]["humans"]) + len(naive["mentions"]["agents"])
    assert total == 2  # naive broadcast hits every member — exactly what the two-layer split prevents
