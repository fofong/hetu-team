"""验收 App 退出后不应遗留可访问的本机后端。"""

import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
PARENT = """
import os, subprocess, sys
server = subprocess.Popen([sys.executable, *sys.argv[1:], '--parent-pid', str(os.getpid())])
print(server.pid, flush=True)
sys.stdin.buffer.read(1)
"""


def test_preview_backend_exit_when_app_exits(tmp_path):
    ready = tmp_path / "port"
    command = [
        sys.executable,
        "-c",
        PARENT,
        str(ROOT / "qa/qq-parity/serve.py"),
        str(ROOT / "client/index.html"),
        "--port",
        "0",
        "--ready-file",
        str(ready),
    ]
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    with subprocess.Popen(
        command, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    ) as app:
        assert app.stdout and app.stdin
        pid = int(app.stdout.readline())
        local = build_opener(ProxyHandler({}))
        try:
            for _ in range(50):
                if ready.exists():
                    try:
                        url = f"http://127.0.0.1:{ready.read_text()}/preview"
                        with local.open(url, timeout=0.2) as response:
                            assert response.status == 200
                        break
                    except (OSError, ValueError):
                        pass
                time.sleep(0.1)
            else:
                raise AssertionError("验收后端未启动")
            app.stdin.close()
            assert app.wait(timeout=3) == 0
            for _ in range(50):
                try:
                    with local.open(url, timeout=0.2) as response:
                        assert response.status == 200
                except OSError:
                    break
                time.sleep(0.1)
            else:
                raise AssertionError("App 已退出但验收后端仍能接受连接")
        finally:
            if app.poll() is None:
                app.terminate()
                app.wait(timeout=3)
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
