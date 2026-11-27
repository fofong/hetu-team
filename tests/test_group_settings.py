"""群设置 HTTP 层：改名 + 发言模式切换（POST /groups/{gid}/settings）+ 校验反例."""
from fastapi.testclient import TestClient

from botteam_kernel.server import create_app


def test_http_settings_rename_and_mode(tmp_path):
    c = TestClient(create_app(str(tmp_path / "g.db")))
    g = c.post("/groups", json={"name": "旧名", "mode": "default_single"}).json()["group_id"]
    r = c.post(f"/groups/{g}/settings", json={"name": "新名", "mode": "free_discussion"})
    assert r.status_code == 200
    assert r.json()["name"] == "新名" and r.json()["mode"] == "free_discussion"
    assert [x for x in c.get("/groups").json() if x["group_id"] == g][0]["name"] == "新名"


def test_http_settings_bad_mode_and_missing(tmp_path):
    """反例: 非法模式 400（不得让群卡死在坏模式）、不存在群 404."""
    c = TestClient(create_app(str(tmp_path / "g2.db")))
    g = c.post("/groups", json={"name": "g"}).json()["group_id"]
    assert c.post(f"/groups/{g}/settings", json={"mode": "胡来"}).status_code == 400
    assert c.post("/groups/nope/settings", json={"name": "x"}).status_code == 404
    assert [x for x in c.get("/groups").json() if x["group_id"] == g][0]["mode"] == "default_single"


def test_http_settings_restart_persistence(tmp_path):
    db = str(tmp_path / "g3.db")
    c = TestClient(create_app(db))
    g = c.post("/groups", json={"name": "a"}).json()["group_id"]
    c.post(f"/groups/{g}/settings", json={"mode": "collaborative"})
    c2 = TestClient(create_app(db))
    assert [x for x in c2.get("/groups").json() if x["group_id"] == g][0]["mode"] == "collaborative"
