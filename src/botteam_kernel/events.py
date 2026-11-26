"""Persistent event store — §5.4 持久事件表 + Outbox + 幂等消费 + 断线续传游标."""
from __future__ import annotations

import json
import sqlite3
import uuid


class EventStore:
    def __init__(self, path: str):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS events (
                 seq INTEGER PRIMARY KEY AUTOINCREMENT,
                 event_id TEXT NOT NULL,
                 kind TEXT NOT NULL,
                 payload TEXT NOT NULL,
                 idempotency_key TEXT UNIQUE,
                 ts REAL NOT NULL DEFAULT (strftime('%s','now'))
               )"""
        )
        self._db.commit()

    def append(self, kind: str, payload: dict, idempotency_key: str | None = None) -> dict:
        if idempotency_key is not None:
            row = self._db.execute(
                "SELECT seq, event_id, kind, payload, ts FROM events WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if row:
                return self._row_to_event(row)
        event_id = uuid.uuid4().hex
        try:
            cur = self._db.execute(
                "INSERT INTO events (event_id, kind, payload, idempotency_key) VALUES (?,?,?,?)",
                (event_id, kind, json.dumps(payload, ensure_ascii=False), idempotency_key),
            )
            self._db.commit()
        except sqlite3.IntegrityError:
            # concurrent duplicate with the same key: return the winner
            row = self._db.execute(
                "SELECT seq, event_id, kind, payload, ts FROM events WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            return self._row_to_event(row)
        seq = cur.lastrowid
        row = self._db.execute(
            "SELECT seq, event_id, kind, payload, ts FROM events WHERE seq=?", (seq,)
        ).fetchone()
        return self._row_to_event(row)

    def since(self, cursor_seq: int = 0, group_id: str | None = None) -> list[dict]:
        rows = self._db.execute(
            "SELECT seq, event_id, kind, payload, ts FROM events WHERE seq > ? ORDER BY seq",
            (cursor_seq,),
        ).fetchall()
        events = [self._row_to_event(r) for r in rows]
        if group_id is not None:
            events = [e for e in events if e["payload"].get("group_id") == group_id]
        return events

    def count(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    @staticmethod
    def _row_to_event(row) -> dict:
        seq, event_id, kind, payload, ts = row
        return {"seq": seq, "event_id": event_id, "kind": kind, "payload": json.loads(payload), "ts": ts}
