"""Hetu Worker Adapter — Host10 常驻进程.

轮询 Host168 Bot-team 网关待认领队列 → 无头执行 Hetu → 回传结果进群。
轮询模式原因：Host10 防火墙不接受入站推送（168->10:8878=000 实测）。
"""
from __future__ import annotations

import os
import subprocess
import time

import httpx

GATEWAY = os.environ.get("BOTTEAM_GATEWAY", "http://10.66.10.2:8877")
AGENT_ID = os.environ.get("BOTTEAM_AGENT_ID", "hetu-worker")
HERMES = os.environ.get("HERMES_BIN", "/Users/luca/Applications/Hetu Agent.app/Contents/Resources/agent-payload/bin/hermes")
POLL_SEC = int(os.environ.get("BOTTEAM_POLL", "5"))
TASK_TIMEOUT = int(os.environ.get("BOTTEAM_TASK_TIMEOUT", "900"))
MAX_TURNS = os.environ.get("BOTTEAM_MAX_TURNS", "30")


def register(client: httpx.Client) -> None:
    r = client.post(f"{GATEWAY}/gateway/agents", json={
        "agent_id": AGENT_ID, "endpoint": "",  # endpoint 空 = 轮询认领模式
        "capabilities": ["general", "hetu"], "token": os.environ.get("BOTTEAM_TOKEN", "hetu-worker-local"),
    })
    r.raise_for_status()


def run_hetu(prompt: str) -> str:
    r = subprocess.run(
        [HERMES, "chat", "-q", prompt, "--oneshot", "-Q", "--max-turns", MAX_TURNS],
        capture_output=True, text=True, timeout=TASK_TIMEOUT,
    )
    out = (r.stdout or "").strip()
    if r.returncode != 0 and not out:
        raise RuntimeError(f"hermes exit={r.returncode}: {(r.stderr or '')[-500:]}")
    return out or "(空输出)"


def main() -> None:
    # trust_env=False: 绕开 macOS 系统代理（httpx 经 scutil 代理转发非 loopback 请求）
    client = httpx.Client(timeout=60, trust_env=False)
    register(client)
    print(f"[worker] {AGENT_ID} registered, polling {GATEWAY}", flush=True)
    while True:
        try:
            pending = client.get(f"{GATEWAY}/gateway/agents/{AGENT_ID}/pending").json()
        except Exception as e:  # 网关瞬断
            print(f"[worker] poll error: {e}", flush=True)
            time.sleep(POLL_SEC)
            continue
        for d in pending:
            tid, prompt = d["a2a_task_id"], d["prompt"]
            print(f"[worker] claim {tid}: {prompt[:80]}", flush=True)
            try:
                client.post(f"{GATEWAY}/gateway/callback",
                            json={"a2a_task_id": tid, "state": "working"})
                result = run_hetu(prompt)
                client.post(f"{GATEWAY}/gateway/callback",
                            json={"a2a_task_id": tid, "state": "completed", "result": result})
                print(f"[worker] done {tid} ({len(result)} chars)", flush=True)
            except Exception as e:
                client.post(f"{GATEWAY}/gateway/callback",
                            json={"a2a_task_id": tid, "state": "failed", "result": f"worker error: {e}"})
                print(f"[worker] fail {tid}: {e}", flush=True)
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
