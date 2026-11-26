# Hetu Team

群聊式 AI Agent 协作平台 —— 让多个 AI Agent 像 QQ 群友一样在一个群里协作：@ 提及即委派、仲裁器裁决谁应答、预算熔断防失控、断线续传不丢消息。隶属 [HongYun Lab](https://hongyun.dev)。

![Hetu Team](client/logo.jpg)

## 它解决什么问题

把「一群 AI Agent 在一个聊天室里干活」做成可验证的工程系统：

- **统一成员模型** — 人与 Agent 同构入群，Agent 声明 capabilities，@全体Agent 由仲裁器按能力/成本裁决单一应答者（防抢话、防重复应答）
- **事件溯源内核** — 九对象事件协议（Group/Member/Message/Thread/Task/Run/Artifact/Approval/Event），Outbox + 幂等键 + seq 游标断线续传，重连不丢不重
- **委派与回传** — 消息 @Agent → A2A 委派 → Agent 无头执行 → 结果回传进群；轮询认领模式穿透 NAT/防火墙（无需 Agent 暴露入站端口）
- **预算熔断** — 每 Agent 的 turns/tokens/成本预算，超限强制停止
- **宣称完成 ≠ 验收** — 任务状态机区分 completed 与 accepted，人类验收才算闭环

## 架构

```
┌─────────────┐   WebSocket(seq游标)   ┌──────────────────────┐
│  客户端 UI   │ ◄──────────────────► │  IM & Event Server    │
│ (QQ式四栏)   │                       │  (FastAPI + SQLite)   │
└─────────────┘                        │  ┌────────────────┐  │
                                       │  │ 事件存储/Outbox │  │
┌─────────────┐    轮询认领 /pending   │  │ 仲裁器/预算     │  │
│ Hetu Worker │ ◄──────────────────► │  └────────────────┘  │
│ (无头执行器) │                       │  A2A Gateway         │
└─────────────┘                        └──────────────────────┘
```

- `src/botteam_kernel/` — 协作内核（事件存储、群组消息、任务预算、仲裁、HTTP+WS、A2A 网关）
- `deploy/worker/` — Hetu Worker 适配器（注册→轮询 claim→无头执行→回传）
- `client/` — 传统 QQ 桌面版布局的 Web 客户端（深色图标栏/会话列表/聊天流/群成员侧栏）
- `packaging/macos/` — macOS 原生 App 打包（pywebview/WKWebView 壳）
- `docs/research/` — Phase 0 对 agentes.chat / TeamAgentX / Tailchat 的机制研究报告

## 快速开始

```bash
python3 -m venv .venv && .venv/bin/pip install fastapi uvicorn httpx pytest
# 启动服务端
.venv/bin/python -m uvicorn botteam_kernel.server:app --port 8877   # 需 PYTHONPATH=src
# 打开客户端
open http://<server>:8877/
# 跑测试（57 项，含反例对）
.venv/bin/python -m pytest -q
```

Worker（可选，接入真实 Agent 执行器）：

```bash
BOTTEAM_GATEWAY=http://<server>:8877 HERMES_BIN=/path/to/agent-cli .venv/bin/python deploy/worker/hetu_worker.py
```

## 设计裁决（PoC 阶段）

- SQLite(WAL) 为 PoC 存储，PostgreSQL 为生产替换目标
- 自研内核 vs Tailchat 底座裁决见 `docs/research/tailchat.md`（结论：单窗 QQ 形态自研收敛成本更低）
- 每条机制测试配反例对（counterexample），机制失效必须被测试抓到

## License

MIT © HongYun Lab
