"""P0 纯数字 ID 系统（QQ 号/群号，对齐 QQ 规则：纯数字、首位非 0）."""
import re

from fastapi.testclient import TestClient

from botteam_kernel.server import create_app
from botteam_kernel.numbers import NumberService, QQ_RE, GROUP_NO_RE, valid_qq, valid_group_no


def _client(tmp_path, name="n.db"):
    return TestClient(create_app(str(tmp_path / name)))


def test_qq_number_rules():
    # 官方规则: QQ 号 5-11 位纯数字首位 1-9
    assert valid_qq("1000000001") and valid_qq("13509103")
    assert not valid_qq("0123456")   # 首位 0
    assert not valid_qq("1234")      # 太短
    assert not valid_qq("123456789012")  # 12 位
    assert not valid_qq("12a3456")   # 混字母
    assert valid_group_no("14839754")
    assert not valid_group_no("04839754")


def test_group_number_assigned(tmp_path):
    c = _client(tmp_path)
    g = c.post("/groups", json={"name": "验收群A"}).json()
    no = g["group_number"]
    assert re.match(GROUP_NO_RE, no), no  # 8 位纯数字首位非 0
    # 列表接口带群号
    listed = {x["name"]: x["group_number"] for x in c.get("/groups").json()}
    assert listed["验收群A"] == no
    # 反例: 幂等——重复读不重新分配
    assert c.get("/groups").json()[0]["group_number"] == no
    # 重启持久（事件溯源）
    c2 = _client(tmp_path)
    assert c2.get("/groups").json()[0]["group_number"] == no


def test_member_qq_number_global_unique(tmp_path):
    c = _client(tmp_path)
    g1 = c.post("/groups", json={"name": "g1"}).json()["group_id"]
    g2 = c.post("/groups", json={"name": "g2"}).json()["group_id"]
    c.post(f"/groups/{g1}/members", json={"kind": "agent", "name": "hetu-worker"})
    c.post(f"/groups/{g2}/members", json={"kind": "agent", "name": "hetu-worker"})
    c.post(f"/groups/{g1}/members", json={"kind": "human", "name": "同事甲"})
    ids = c.get("/ids").json()
    w = [i for i in ids if i["ref"] == "hetu-worker"]
    assert len(w) == 1  # 反例: 同人跨群只一个 QQ 号
    assert re.match(QQ_RE, w[0]["number"])
    # 好友接口带 qq_number
    c.post("/friends", json={"name": "hetu-worker", "kind": "agent"})
    fr = c.get("/friends").json()
    assert fr[0]["qq_number"] == w[0]["number"]


def test_backfill_existing_members(tmp_path):
    """存量成员/群在重启后也补齐数字号（回填不重发）."""
    c = _client(tmp_path)
    g = c.post("/groups", json={"name": "老群"}).json()["group_id"]
    c.post(f"/groups/{g}/members", json={"kind": "human", "name": "老成员"})
    c2 = _client(tmp_path)
    ids = c2.get("/ids").json()
    refs = {i["ref"] for i in ids}
    assert "老成员" in refs
    # 反例: 再重启一次号不变
    again = {i["ref"]: i["number"] for i in _client(tmp_path).get("/ids").json()}
    assert again == {i["ref"]: i["number"] for i in c2.get("/ids").json()}
