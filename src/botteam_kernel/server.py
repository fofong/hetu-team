"""IM & Event Server — REST + WebSocket（可替换层，§二）.

WS 语义：客户端带 last_seq 连接 → 先补发 since(last_seq) 再实时推送。
持久事件表即 Outbox：不丢（游标补发）不重（seq 幂等消费由客户端保证）。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .arbitration import Arbiter
from .authz import AuthorizationSystem
from .events import EventStore
from .gateway import A2AGateway
from .groups import GroupService
from .tasks import TaskService


class GroupIn(BaseModel):
    name: str
    mode: str = "default_single"


class MemberIn(BaseModel):
    kind: str
    name: str
    capabilities: list[str] = []


class MessageIn(BaseModel):
    sender: str
    text: str
    reply_to: str | None = None
    idempotency_key: str | None = None


class TaskIn(BaseModel):
    group_id: str
    assignee: str
    title: str
    origin_message_id: str | None = None


class RunIn(BaseModel):
    agent_id: str


class CompleteIn(BaseModel):
    result: str


class AcceptIn(BaseModel):
    accepted_by: str


class AgentIn(BaseModel):
    agent_id: str
    endpoint: str
    capabilities: list[str] = []
    token: str


class DelegateIn(BaseModel):
    botteam_task_id: str
    agent_id: str
    prompt: str


class CallbackIn(BaseModel):
    a2a_task_id: str
    state: str
    result: str | None = None


def create_app(db_path: str, transport=None) -> FastAPI:
    store = EventStore(db_path)
    groups = GroupService(store)
    tasks = TaskService(store)
    arbiter = Arbiter()
    authz = AuthorizationSystem()
    gateway = A2AGateway(store, transport=transport) if transport is not None else A2AGateway(store)
    app = FastAPI(title="Hetu Bot-team IM & Event Server")
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.state.store, app.state.groups, app.state.tasks = store, groups, tasks
    app.state.authz = authz

    _client_html = Path(__file__).parent / "webclient" / "index.html"

    @app.get("/", include_in_schema=False)
    def index():
        return HTMLResponse(_client_html.read_text(encoding="utf-8"))

    @app.post("/groups")
    def create_group(body: GroupIn):
        return groups.create_group(name=body.name, mode=body.mode)

    @app.get("/groups")
    def list_groups():
        return list(groups._groups.values())

    @app.post("/groups/{gid}/members")
    def add_member(gid: str, body: MemberIn):
        return groups.add_member(gid, kind=body.kind, name=body.name, capabilities=body.capabilities)

    @app.get("/groups/{gid}/members")
    def list_members(gid: str):
        return groups.members(gid)

    @app.get("/groups/{gid}/messages")
    def list_messages(gid: str):
        return groups.messages(gid)

    @app.post("/groups/{gid}/messages")
    def post_message(gid: str, body: MessageIn):
        msg = groups.post_message(
            gid, sender=body.sender, text=body.text,
            reply_to=body.reply_to, idempotency_key=body.idempotency_key,
        )
        group = groups._groups[gid]
        agents = [m for m in groups.members(gid) if m["kind"] == "agent"]
        route = None
        if msg["mentions"]["agents"]:
            responders = arbiter.decide(group["mode"], msg, agents)
            route = {
                "responders": responders,
                "mode": group["mode"],
                "estimated_cost": f"{len(msg['mentions']['agents'])} 候选 → {len(responders)} 应答，预计 token 成本按应答者任务量估算",
            }
            store.append("route.decided", {"group_id": gid, "message_id": msg["message_id"], **route},
                         idempotency_key=f"route-{msg['message_id']}")
            # 接线: 应答者若是已注册网关 agent, 自动创建委派（轮询型进待认领队列）
            by_id = {m["member_id"]: m for m in agents}
            for rid in responders:
                name = by_id.get(rid, {}).get("name", rid)
                if name in gateway._agents:
                    gateway.delegate(f"msg-{msg['message_id']}-{name}", name, msg["text"], group_id=gid)
        return {**msg, "route": route}

    @app.get("/groups/{gid}/events")
    def events(gid: str, since: int = 0):
        return store.since(since, group_id=gid)

    @app.post("/tasks")
    def create_task(body: TaskIn):
        return tasks.create_task(body.group_id, body.origin_message_id, body.assignee, body.title)

    @app.get("/tasks/{task_id}")
    def get_task(task_id: str):
        return tasks.get_task(task_id)

    @app.post("/tasks/{task_id}/runs")
    def start_run(task_id: str, body: RunIn):
        return tasks.start_run(task_id, body.agent_id)

    @app.post("/runs/{run_id}/complete")
    def complete_run(run_id: str, body: CompleteIn):
        return tasks.complete_run(run_id, body.result)

    @app.post("/tasks/{task_id}/accept")
    def accept_task(task_id: str, body: AcceptIn):
        return tasks.accept_task(task_id, body.accepted_by)

    # ---- A2A Gateway endpoints ----
    @app.post("/gateway/agents")
    def register_agent(body: AgentIn):
        return gateway.register_agent(body.agent_id, body.endpoint, body.capabilities, body.token)

    @app.get("/gateway/agents")
    def list_agents():
        return [{"agent_id": a["agent_id"], "endpoint": a["endpoint"], "capabilities": a["capabilities"]} for a in gateway._agents.values()]

    @app.post("/gateway/delegate")
    def delegate(body: DelegateIn):
        try:
            return gateway.delegate(body.botteam_task_id, body.agent_id, body.prompt)
        except ValueError as e:
            from fastapi.responses import JSONResponse

            return JSONResponse({"error": str(e)}, status_code=400)

    @app.post("/gateway/callback")
    def callback(body: CallbackIn):
        try:
            d = gateway.status_callback(body.a2a_task_id, body.state, body.result)
        except ValueError as e:
            from fastapi.responses import JSONResponse

            return JSONResponse({"error": str(e)}, status_code=400)
        # 接线: 完成回传 → 结果作为 Agent 消息发回群（幂等键防重复应答）
        if body.state == "completed" and body.result and d.get("group_id"):
            groups.post_message(d["group_id"], sender=d["agent_id"], text=body.result,
                                idempotency_key=f"a2a-reply-{body.a2a_task_id}")
        return d

    @app.get("/gateway/agents/{agent_id}/pending")
    def agent_pending(agent_id: str):
        return gateway.pending_for(agent_id)

    @app.get("/gateway/delegations/{bt_task_id}")
    def delegation(bt_task_id: str):
        if bt_task_id not in gateway._delegations:
            from fastapi.responses import JSONResponse

            return JSONResponse({"error": "not found"}, status_code=404)
        return gateway._delegations[bt_task_id]

    @app.websocket("/ws/{gid}")
    async def ws_events(websocket: WebSocket, gid: str, last_seq: int = 0):
        await websocket.accept()
        cursor = last_seq
        for e in store.since(cursor, group_id=gid):
            await websocket.send_json(e)
            cursor = e["seq"]
        try:
            while True:
                await asyncio.sleep(0.05)
                for e in store.since(cursor, group_id=gid):
                    await websocket.send_json(e)
                    cursor = e["seq"]
        except WebSocketDisconnect:
            return

    return app
