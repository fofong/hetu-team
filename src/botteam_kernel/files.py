"""文件存储（图片/文件消息底座）.

落盘于事件库同级 files/ 目录；上限 10MB（超限 413 反例）；文件名走
Content-Disposition 原样回带。事件库只存元数据（消息 attachments），
文件本体走磁盘——事件流不被大二进制污染。
"""
from __future__ import annotations

import mimetypes
import re
import uuid
from pathlib import Path

MAX_BYTES = 10 * 1024 * 1024


class FileStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, name: str, data: bytes, content_type: str = "") -> dict:
        if len(data) > MAX_BYTES:
            raise ValueError("file too large")
        safe = re.sub(r"[^\w.\-\u4e00-\u9fff]", "_", name)[:80] or "file"
        fid = uuid.uuid4().hex
        mime = content_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
        meta = {"file_id": fid, "name": name, "size": len(data), "mime": mime, "stored": f"{fid}-{safe}"}
        (self.root / meta["stored"]).write_bytes(data)
        return meta

    def load(self, file_id: str) -> tuple[Path, str, str] | None:
        for p in sorted(self.root.glob(f"{file_id}-*")):
            stored = p.name
            name = stored.split("-", 1)[1] if "-" in stored else stored
            mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
            return p, name, mime
        return None

    def exists(self, file_id: str) -> bool:
        return self.load(file_id) is not None
