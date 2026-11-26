"""群管理切片（QQ 群设置范式）: 群任务列表 / 群名与模式修改 / 移除成员.

反例对: 部分更新不得重置未传字段；移除不存在成员 404；事件溯源重启恢复。
"""
import pytest
from fastapi.testclient import TestClient

from botteam_kernel.events import EventStore
from botteam_kernel.groups import GroupService
from botteam_kernel.server import create_app


@pytest.fixture()
def client(tmp_path):
    app = create_app(db_path=str(tmp_path / "admin.db"))
    with TestClient(app) as c:
        yield c


def _group(client, name="研发群"):
    return client.post("/groups", json={"name": name, "mode": "default_single"}).json()["group_id"]


def test_list_group_tasks(client):
    gid = _group(client)
    t1 = client.post("/tasks", json={"group_id": gid, "assignee": "hetu", "title": "写调研报告"}).json()
    client.post("/tasks", json={"group_id": gid, "assignee": "avatar", "title": "安全审查"})
    other = _group(client, "别的群")
    client.post("/tasks", json={"group_id": other, "assignee": "x", "title": "别群任务"})
    ts = client.get(f"/groups/{gid}/tasks").json()
    assert {t["title"] for t in ts} == {"写调研报告", "安全审查"}
    assert all(t["group_id"] == gid for t in ts)  # 反例: 别群任务不混入


def test_update_group_settings(tmp_path):
    db = str(tmp_path / "s.db")
    svc = GroupService(EventStore(db))
    g = svc.create_group("旧名")
    svc.update_settings(g["group_id"], name="新名", mode="free_discussion")
    assert svc._groups[g["group_id"]]["name"] == "新名"
    assert svc._groups[g["group_id"]]["mode"] == "free_discussion"
    svc.update_settings(g["group_id"], name="再改名")  # 反例: 部分更新不得重置 mode
    assert svc._groups[g["group_id"]]["mode"] == "free_discussion"
    svc2 = GroupService(EventStore(db))  # 事件溯源重启恢复
    assert svc2._groups[g["group_id"]]["name"] == "再改名"


def test_remove_member_and_counterexample(client):
    gid = _group(client)
    m = client.post(f"/groups/{gid}/members", json={"kind": "agent", "name": "临时Bot"}).json()
    r = client.delete(f"/groups/{gid}/members/{m['member_id']}")
    assert r.status_code == 200
    names = [x["name"] for x in client.get(f"/groups/{gid}/members").json()]
    assert "临时Bot" not in names
    assert client.delete(f"/groups/{gid}/members/nope").status_code == 404  # 反例: 不存在 404


def test_message_projection_has_created_at(tmp_path):
    """消息投影从事件信封 ts 回填 created_at；重启后仍带时间。"""
    db = str(tmp_path / "ts.db")
    with TestClient(create_app(db_path=db)) as c:
        gid = c.post("/groups", json={"name": "时间群"}).json()["group_id"]
        c.post(f"/groups/{gid}/messages", json={"sender": "a", "text": "hi"})
        assert all(m["created_at"] for m in c.get(f"/groups/{gid}/messages").json())
    with TestClient(create_app(db_path=db)) as c2:  # 事件溯源重启恢复
        ms = c2.get(f"/groups/{[g['group_id'] for g in c2.get('/groups').json() if g['name']=='时间群'][0]}/messages").json()
        assert ms[-1]["created_at"] > 0
