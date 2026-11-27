"""Phase 3: 服务端好友列表 + 文件/图片消息（QQ 完整 GUI 缺口）."""
import base64

from fastapi.testclient import TestClient

from botteam_kernel.server import create_app


def _client(tmp_path, name="f.db"):
    return TestClient(create_app(str(tmp_path / name)))


# ---- 好友列表（服务端化，跨设备不丢）----

def test_friends_crud_and_persistence(tmp_path):
    c = _client(tmp_path)
    assert c.get("/friends").json() == []
    r = c.post("/friends", json={"name": "hetu-worker", "kind": "agent"})
    assert r.status_code == 200
    c.post("/friends", json={"name": "同事甲", "kind": "human"})
    names = [f["name"] for f in c.get("/friends").json()]
    assert names == ["hetu-worker", "同事甲"]
    # 反例: 重复添加 400
    assert c.post("/friends", json={"name": "hetu-worker", "kind": "agent"}).status_code == 400
    assert c.delete("/friends/同事甲").json()["removed"] is True
    assert [f["name"] for f in c.get("/friends").json()] == ["hetu-worker"]
    c2 = _client(tmp_path)  # 事件溯源: 重启后好友仍在
    assert [f["name"] for f in c2.get("/friends").json()] == ["hetu-worker"]


# ---- 文件消息 ----

def test_file_upload_download(tmp_path):
    c = _client(tmp_path)
    data = base64.b64encode("你好文件".encode()).decode()
    r = c.post("/files", json={"name": "说明.txt", "data_b64": data, "content_type": "text/plain"})
    assert r.status_code == 200
    fid = r.json()["file_id"]
    assert r.json()["size"] == len("你好文件".encode())
    got = c.get(f"/files/{fid}")
    assert got.status_code == 200
    assert got.content.decode() == "你好文件"
    from urllib.parse import quote
    assert f"filename*=UTF-8''{quote('说明.txt')}" in got.headers.get("content-disposition", "")
    assert c.get("/files/nope").status_code == 404


def test_file_oversize_counterexample(tmp_path):
    """反例: 超过 10MB 拒绝 413，不落盘."""
    c = _client(tmp_path)
    big = base64.b64encode(b"x" * (11 * 1024 * 1024)).decode()
    assert c.post("/files", json={"name": "big.bin", "data_b64": big, "content_type": "application/octet-stream"}).status_code == 413


def test_message_with_attachment(tmp_path):
    c = _client(tmp_path)
    g = c.post("/groups", json={"name": "g"}).json()["group_id"]
    png = base64.b64encode(b"\x89PNG fake").decode()
    f = c.post("/files", json={"name": "截图.png", "data_b64": png, "content_type": "image/png"}).json()
    msg = c.post(f"/groups/{g}/messages", json={"sender": "沣锅", "text": "", "idempotency_key": "att-1",
                                                "attachments": [{"file_id": f["file_id"], "name": "截图.png",
                                                                 "size": f["size"], "mime": "image/png"}]})
    assert msg.status_code == 200
    atts = msg.json()["attachments"]
    assert atts[0]["file_id"] == f["file_id"]
    # 群文件聚合
    gf = c.get(f"/groups/{g}/files").json()
    assert gf[0]["name"] == "截图.png"
    # 反例: 不存在的 file_id 拒绝
    bad = c.post(f"/groups/{g}/messages", json={"sender": "沣锅", "text": "x",
                                                "attachments": [{"file_id": "ghost", "name": "n", "size": 1, "mime": "image/png"}]})
    assert bad.status_code == 400
