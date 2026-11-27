"""IM & Event Server — REST + WebSocket（可替换层，§二）.

WS 语义：客户端带 last_seq 连接 → 先补发 since(last_seq) 再实时推送。
持久事件表即 Outbox：不丢（游标补发）不重（seq 幂等消费由客户端保证）。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from .arbitration import Arbiter
from .authz import AuthorizationSystem
from .events import EventStore
from .gateway import A2AGateway
from .groups import GroupService
from .handoff import HandoffService
from .approvals import ApprovalService
from .memory import MemoryService
from .friends import FriendService
from .files import FileStore
from .numbers import NumberService
from .credits import CreditService
from .tasks import TaskService


class GroupIn(BaseModel):
    name: str
    mode: str = "default_single"


class MemberIn(BaseModel):
    kind: str
    name: str
    capabilities: list[str] = []
    response_mode: str = "mention_only"
    keywords: list[str] = []


class ResponseModeIn(BaseModel):
    mode: str
    keywords: list[str] = []


class RecallIn(BaseModel):
    by: str
    side_effect: str = "none"  # none/rolled_back/irreversible（撤回≠回滚红线）


class ReactIn(BaseModel):
    member: str
    emoji: str


class PinIn(BaseModel):
    by: str


class MessageIn(BaseModel):
    sender: str
    text: str
    reply_to: str | None = None
    idempotency_key: str | None = None
    attachments: list[dict] | None = None


class FriendIn(BaseModel):
    name: str
    kind: str = "human"


class FileIn(BaseModel):
    name: str
    data_b64: str
    content_type: str = ""


class AnnouncementIn(BaseModel):
    text: str
    set_by: str = ""


class GroupSettingsIn(BaseModel):
    name: str | None = None
    mode: str | None = None


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
    group_id: str | None = None
    requester: str | None = None  # A2A 调度发起方；提供时启用好友授权/信用点闸门


class FriendReqIn(BaseModel):
    frm: str = Field(alias="from")
    to: str


class CreditGrantIn(BaseModel):
    name: str
    amount: int
    reason: str = ""


class CallbackIn(BaseModel):
    a2a_task_id: str
    state: str
    result: str | None = None
    handoff: list[dict] | None = None  # 结构化交接: [{"agent","task"}]，受护栏约束


class ApprovalReqIn(BaseModel):
    requester: str
    tool: str
    args: dict = {}
    title: str
    why: str


class ApprovalDecideIn(BaseModel):
    approver: str
    approve: bool


class ApprovalConsumeIn(BaseModel):
    tool: str
    args: dict = {}


class PersonaIn(BaseModel):
    persona: str
    task: str = ""


class MemoryIn(BaseModel):
    text: str
    kind: str = "fact"


def create_app(db_path: str, transport=None) -> FastAPI:
    store = EventStore(db_path)
    groups = GroupService(store)
    tasks = TaskService(store)
    arbiter = Arbiter()
    authz = AuthorizationSystem()
    gateway = A2AGateway(store, transport=transport) if transport is not None else A2AGateway(store)
    handoffs = HandoffService(store)
    approvals = ApprovalService(store)
    memories = MemoryService(store)
    friends = FriendService(store)
    files = FileStore(Path(db_path).parent / "files")
    numbers = NumberService(store)
    credits = CreditService(store)

    def _backfill_numbers():
        """存量群/成员补齐数字号（幂等，重启不重发）."""
        for g in groups._groups.values():
            numbers.assign("group", g["group_id"])
        for gid in list(groups._members):
            for m in groups.members(gid):
                numbers.assign("member", m["name"])

    _backfill_numbers()

    def compose_prompt(gid: str, agent_name: str, task_text: str, reply_to: str | None = None) -> str:
        """委派 prompt = 群公告 + 人格 + 职责 + 召回记忆 + 引用全文 + 任务；全空原样（反例保护）."""
        persona, duty = groups.persona_of(gid, agent_name)
        ann = groups._groups.get(gid, {}).get("announcement")
        parts = []
        if ann and ann.get("text"):
            parts.append(f"【群公告】{ann['text']}")
        if persona:
            parts.append(f"【你的角色设定】{persona}")
            if duty:
                parts.append(f"【你的职责】{duty}")
            hits = memories.recall(gid, agent_name, task_text, limit=5)
            if hits:
                parts.append("【已记住的相关信息】\n" + "\n".join(f"- {h['text']}" for h in hits))
        if reply_to:
            q = groups.get_message(reply_to)
            if q:
                parts.append(f"【引用消息】{q['sender']}: {q['text']}")
        if not parts:
            return task_text
        parts.append(f"【本次任务】{task_text}")
        return "\n\n".join(parts)
    app = FastAPI(title="Hetu Bot-team IM & Event Server")
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.state.store, app.state.groups, app.state.tasks = store, groups, tasks
    app.state.authz = authz

    _client_html = Path(__file__).parent / "webclient" / "index.html"

    @app.get("/", include_in_schema=False)
    def index():
        return HTMLResponse(_client_html.read_text(encoding="utf-8"))

    @app.get("/logo.jpg", include_in_schema=False)
    def logo():
        f = Path(__file__).parent / "webclient" / "logo.jpg"
        if not f.exists():
            raise HTTPException(status_code=404)
        from fastapi.responses import FileResponse
        return FileResponse(f, media_type="image/jpeg")

    @app.post("/groups")
    def create_group(body: GroupIn):
        g = groups.create_group(name=body.name, mode=body.mode)
        gid = g.get("group_id") or g.get("id")
        if gid:
            g["group_number"] = numbers.assign("group", gid)
        return g

    @app.get("/groups")
    def list_groups():
        out = []
        for g in groups._groups.values():
            d = dict(g)
            d["members_count"] = len(groups.members(g["group_id"]))
            d["group_number"] = numbers.assign("group", g["group_id"])
            out.append(d)
        return out

    @app.get("/ids")
    def list_ids():
        return numbers.list_all()

    @app.post("/groups/{gid}/members")
    def add_member(gid: str, body: MemberIn):
        m = groups.add_member(gid, kind=body.kind, name=body.name, capabilities=body.capabilities,
                              response_mode=body.response_mode, keywords=body.keywords)
        numbers.assign("member", body.name)
        return m

    @app.post("/groups/{gid}/members/{mid}/response_mode")
    def set_response_mode(gid: str, mid: str, body: ResponseModeIn):
        m = groups.set_response_mode(gid, mid, body.mode, body.keywords)
        if m is None:
            raise HTTPException(404, "member or mode invalid")
        return m

    @app.get("/groups/{gid}/members")
    def list_members(gid: str):
        return groups.members(gid)

    @app.get("/groups/{gid}/messages")
    def list_messages(gid: str, q: str | None = None, sender: str | None = None):
        return groups.messages(gid, q=q, sender=sender)

    @app.post("/groups/{gid}/messages/{mid}/recall")
    def recall_message(gid: str, mid: str, body: RecallIn):
        status = groups.recall_message(gid, mid, body.by, body.side_effect)
        if status == "not_found":
            raise HTTPException(404, "message not found")
        if status == "forbidden":
            raise HTTPException(403, "只能撤回自己的消息")
        if status == "already":
            raise HTTPException(400, "消息已撤回")
        if status == "bad_side_effect":
            raise HTTPException(400, "side_effect 必须为 none/rolled_back/irreversible")
        return {"ok": True}

    @app.post("/groups/{gid}/messages/{mid}/react")
    def react_message(gid: str, mid: str, body: ReactIn):
        if not groups.react(gid, mid, body.member, body.emoji):
            raise HTTPException(404, "message not found")
        return {"ok": True}

    @app.post("/groups/{gid}/messages/{mid}/pin")
    def pin_message(gid: str, mid: str, body: PinIn):
        if not groups.pin_message(gid, mid, body.by):
            raise HTTPException(404, "message not found")
        return {"ok": True}

    @app.delete("/groups/{gid}/messages/{mid}/pin")
    def unpin_message(gid: str, mid: str):
        if not groups.unpin_message(gid, mid):
            raise HTTPException(404, "pin not found")
        return {"ok": True}

    @app.get("/groups/{gid}/pins")
    def list_pins(gid: str):
        return groups.pins(gid)

    @app.post("/groups/{gid}/announcement")
    def set_announcement(gid: str, body: AnnouncementIn):
        return groups.set_announcement(gid, body.text, set_by=body.set_by)

    @app.post("/groups/{gid}/settings")
    def update_group_settings(gid: str, body: GroupSettingsIn):
        if gid not in groups._groups:
            return JSONResponse({"error": "not found"}, status_code=404)
        try:
            return groups.update_settings(gid, name=body.name, mode=body.mode)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.delete("/groups/{gid}/members/{mid}")
    def remove_member(gid: str, mid: str):
        if not groups.remove_member(gid, mid):
            raise HTTPException(404, "member not found")
        return {"ok": True}

    @app.post("/groups/{gid}/messages")
    def post_message(gid: str, body: MessageIn):
        if body.attachments:
            for a in body.attachments:
                if not files.exists(a.get("file_id", "")):
                    return JSONResponse({"error": f"unknown file_id {a.get('file_id')}"}, status_code=400)
        msg = groups.post_message(
            gid, sender=body.sender, text=body.text,
            reply_to=body.reply_to, idempotency_key=body.idempotency_key,
            attachments=body.attachments,
        )
        group = groups._groups[gid]
        agents = [m for m in groups.members(gid) if m["kind"] == "agent"]
        # 防互聊铁律: Agent 消息只展示不触发（TeamAgentX 借鉴）——sender 是群内 agent 或网关注册 agent 即拦截
        agent_senders = {m["name"] for m in agents} | set(gateway._agents)
        if msg["sender"] not in agent_senders:
            handoffs.human_message(gid)  # 铁律: 人类发言清零一切自动调度
        route = None
        if msg["mentions"]["agents"] and msg["sender"] not in agent_senders:
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
                    gateway.delegate(f"msg-{msg['message_id']}-{name}", name,
                                     compose_prompt(gid, name, msg["text"], reply_to=msg.get("reply_to")),
                                     group_id=gid)
        elif msg["sender"] not in agent_senders:
            # 被动响应三档: 无 @ 时按成员 response_mode 触发（keyword 命中 / full 全量监听）
            matched = [m for m in agents
                       if m.get("response_mode") == "full"
                       or (m.get("response_mode") == "keyword"
                           and any(k and k in msg["text"] for k in m.get("keywords", [])))]
            if matched:
                route = {"responders": [m["member_id"] for m in matched], "mode": "response_mode",
                         "estimated_cost": f"响应策略命中 {len(matched)} 个 Agent"}
                store.append("route.decided", {"group_id": gid, "message_id": msg["message_id"], **route},
                             idempotency_key=f"route-{msg['message_id']}")
                for m in matched:
                    if m["name"] in gateway._agents:
                        gateway.delegate(f"msg-{msg['message_id']}-{m['name']}", m["name"],
                                         compose_prompt(gid, m["name"], msg["text"], reply_to=msg.get("reply_to")),
                                         group_id=gid)
        return {**msg, "route": route}

    @app.get("/groups/{gid}/events")
    def events(gid: str, since: int = 0):
        return store.since(since, group_id=gid)

    @app.post("/tasks")
    def create_task(body: TaskIn):
        return tasks.create_task(body.group_id, body.origin_message_id, body.assignee, body.title)

    @app.get("/groups/{gid}/tasks")
    def list_group_tasks(gid: str):
        return tasks.group_tasks(gid)

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
        # A2A 调度双闸门（QQ 范式）：好友授权免点数；非好友扣目标 Agent 1 点；皆无拒绝。
        # requester 缺省 = 内部可信路径（群内 @ 路由等），不受闸门影响（向后兼容）。
        if body.requester and not friends.are_friends(body.requester, body.agent_id):
            try:
                credits.consume(body.agent_id, 1, f"A2A 调度 {body.botteam_task_id}")
            except ValueError:
                from fastapi.responses import JSONResponse

                return JSONResponse({"error": "insufficient credits and not friends: dispatch refused"},
                                    status_code=402)
        try:
            return gateway.delegate(body.botteam_task_id, body.agent_id, body.prompt, group_id=body.group_id)
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
        # 接线: 结构化交接 → 护栏裁决后派发下一棒（自由文本 @ 永不触发，见防互聊铁律）
        if body.state == "completed" and body.handoff and d.get("group_id"):
            ctx = d.get("ctx") or {"root": d["botteam_task_id"], "depth": 0, "lineage": [d["agent_id"]]}
            out = handoffs.handoff(d["group_id"], from_agent=d["agent_id"], targets=body.handoff, ctx=ctx)
            for t in out["accepted"]:
                gateway.delegate(f"handoff-{body.a2a_task_id}-{t['agent']}", t["agent"],
                                 compose_prompt(d["group_id"], t["agent"], t["task"]),
                                 group_id=d["group_id"], ctx=out["next_ctx"])
            return {**d, "handoff_result": out}
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

    # ---- 好友 / 文件 endpoints ----
    @app.get("/friends")
    def list_friends():
        out = []
        for f in friends.list():
            d = dict(f)
            d["qq_number"] = numbers.assign("member", f["name"])
            out.append(d)
        return out

    @app.post("/friends")
    def add_friend(body: FriendIn):
        try:
            return friends.add(body.name, kind=body.kind)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.delete("/friends/{name}")
    def remove_friend(name: str):
        return {"removed": friends.remove(name)}

    @app.post("/friends/request")
    def friend_request(body: FriendReqIn):
        try:
            return friends.request(body.frm, body.to)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.get("/friends/requests")
    def friend_requests():
        return friends.list_requests()

    @app.post("/friends/approve")
    def friend_approve(body: FriendReqIn):
        try:
            return friends.approve(body.frm, body.to)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.post("/friends/decline")
    def friend_decline(body: FriendReqIn):
        return {"declined": friends.decline(body.frm, body.to)}

    @app.get("/credits/{name}")
    def credit_balance(name: str):
        return {"name": name, "balance": credits.balance_of(name)}

    @app.post("/credits/grant")
    def credit_grant(body: CreditGrantIn):
        try:
            return {"name": body.name, "balance": credits.grant(body.name, body.amount, body.reason)}
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.post("/files")
    def upload_file(body: FileIn):
        import base64 as b64
        try:
            data = b64.b64decode(body.data_b64)
        except Exception:
            return JSONResponse({"error": "bad base64"}, status_code=400)
        try:
            return files.save(body.name, data, body.content_type)
        except ValueError:
            return JSONResponse({"error": "file too large (max 10MB)"}, status_code=413)

    @app.get("/files/{fid}")
    def download_file(fid: str):
        got = files.load(fid)
        if got is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        path, name, mime = got
        from urllib.parse import quote
        return FileResponse(path, media_type=mime,
                            headers={"content-disposition": f"inline; filename*=UTF-8''{quote(name)}"})

    @app.get("/groups/{gid}/files")
    def group_files(gid: str):
        out, seen = [], set()
        for m in reversed(groups.messages(gid)):
            for a in m.get("attachments") or []:
                if a.get("file_id") not in seen:
                    seen.add(a.get("file_id"))
                    out.append(a)
        return out

    # ---- 人格 / 记忆 endpoints ----
    @app.post("/groups/{gid}/members/{mid}/persona")
    def set_persona(gid: str, mid: str, body: PersonaIn):
        m = groups.set_persona(gid, mid, persona=body.persona, task=body.task)
        if m is None:
            return JSONResponse({"error": "member not found"}, status_code=404)
        return m

    @app.post("/groups/{gid}/agents/{aid}/memory")
    def remember(gid: str, aid: str, body: MemoryIn):
        return memories.remember(gid, aid, text=body.text, kind=body.kind)

    @app.get("/groups/{gid}/agents/{aid}/memory")
    def recall(gid: str, aid: str, q: str = "", limit: int = 20):
        return memories.recall(gid, aid, q, limit=limit)

    @app.delete("/groups/{gid}/agents/{aid}/memory/{mid}")
    def forget(gid: str, aid: str, mid: str):
        return {"forgotten": memories.forget(gid, aid, mid)}

    # ---- 审批闸门 endpoints ----
    @app.post("/groups/{gid}/approvals")
    def request_approval(gid: str, body: ApprovalReqIn):
        return approvals.request(gid, requester=body.requester, tool=body.tool, args=body.args,
                                 title=body.title, why=body.why)

    @app.get("/groups/{gid}/approvals")
    def list_approvals(gid: str):
        return approvals.list_for(gid)

    @app.post("/approvals/{aid}/decide")
    def decide_approval(aid: str, body: ApprovalDecideIn):
        try:
            return approvals.decide(aid, approver=body.approver, approve=body.approve)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.post("/approvals/{aid}/consume")
    def consume_approval(aid: str, body: ApprovalConsumeIn):
        try:
            return approvals.consume(aid, tool=body.tool, args=body.args)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

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
