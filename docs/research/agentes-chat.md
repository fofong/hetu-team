# agentes.chat 四项机制研究报告

仓库根：`poc/agentes-chat`（Bun monorepo：`packages/db` Drizzle schema，`packages/engine` 业务核心，`apps/server` 服务入口）。只读研究，2026-11-26。

## 1) 成员模型（人/Agent 统一建模）
- `packages/db/src/schema/chat.ts:207` — `authorTypeEnum = ["person","agent","app"]`：消息表单一 `authorType` + `authorUserId`/`authorAgentId`，三类身份消息层统一。
- `packages/db/src/schema/chat.ts:146` — `channelMembers`：同表互斥列 `userId XOR agentId`（CHECK `channel_members_one_member`）；Agent 独有 `autoReply` 开关（关=只响应 @提及）。
- `packages/db/src/schema/agents.ts:114` — `agents` 表即名片：`handle`（@名+改名追踪）、`role`、`appearance`、`audienceMode`、`approvalsLevel`、`brain`。
- `packages/db/src/schema/tenancy.ts:114` — `organizationMembers`：人类名片，`availability`（auto/away/busy/out/hidden）+ 带过期状态行。
- `packages/engine/src/member-status.ts` — `MemberPresence`：连接态（Presence 适配器）与自选状态合成。
- 能力标签：`capabilities.ts` `summarizeGrants` 把授权 scope 汇总成人话，路由时作候选说明。

## 2) Router/裁决（谁应答）
- `router.ts:806` `routeMessage()`：无 @ 消息路由入口（outbox `message.posted` 触发）。策略链 `RouterStrategy`（Jev 小模型→小 LLM→作者 plan，ADR 0074），**模型打分、代码裁决**：`selectAgents()`（:288）阈值 minScore 0.5 / maxAgents 4 / continuityMinScore 0.3 / needsAgent<0.25 无人应答。
- 防广播唤醒全员：`ALREADY_ANSWERED_THRESHOLD=0.7`、`FOR_EVERYONE_THRESHOLD=0.5`、`withAddressee`（:219）被说话者压制得分；Agent 消息只能以 `AGENT_CHIME_IN_SCORE=0.75` 拉入一个且 `AGENT_CHIME_IN_MAX_HOP` 封顶防互聊。
- 去抖：`ROUTER_QUIET_MS=2500` + `continuedSoon()`（:1934）同人 2.5s 内下一条统一裁决；`burst.ts` typing 帧推迟首次模型调用。
- 兜底：链全挂走 R1/R2/R3 代码规则，否则 `mention_needed`；决策全落 `routing_decisions` 表（含各候选分数）。
- `dispatch.ts` `createAgentRuns()`：所有唤醒路径共用，`(trigger_message_id, agent_id)` 唯一键幂等。
- 沉默权：`reply-policy.ts` 未点名可 `pass`，`NO_REPLY` 哨兵永不发帖。

## 3) 审批闸门
- `runtime.ts:636` `approvals` 表：绑定 `actionHash`（工具+参数 SHA-256，只执行该哈希动作）、`toolUseId`、`title/why` 必填、`approvers`（指定人/角色/请求者不可自批）、`highRisk`、`supersededById`、`afterIds` 排队依赖。
- 状态机：`pending → approved/refused/expired/canceled`；`executedAt` 置位永不重跑；审批以聊天卡片呈现。
- 超时：`APPROVAL_TTL_MS=24h`（tools/gateway.ts:334）；`expireApprovals()` 每分钟扫过期置 `expired` 恢复 run。
- 代批：`approvalCovers` 表 + `approval-rules.ts`（"永远批准"）；决策入口 `decideApproval()`。

## 4) 流式渲染
- 事件：`events.ts:129` `ChannelEvent` 判别联合：`agent.thinking/step/tool/tool.progress(≤64KB 节流)/tool.output/run/typing`、`run.finished`、`approval.created/decided`。
- 传输：`apps/server/src/realtime/ws.ts` Bun WS `/api/ws`；跨实例 `PubSub`（默认 PG LISTEN/NOTIFY）；`holdEvents()` 事务提交后才发布。
- 发射：`executor.ts` `ownStep()` publish `agent.step`；持久化 `agentRunSteps` 表带 `seq`。
- 断线恢复：**无事件重放**——客户端指数退避+全抖动重连后 `catch-up`：HTTP 重读事实（PG 是真相源，事件只是提示）；落后过多直接 `close(1013)` 逼重连重读。

**一句话**：身份=单表互斥列+authorType；路由=模型打分代码裁决逐条落库；审批=动作哈希绑定+24h TTL 状态机卡片；流式=事实落库+事件仅提示、断线全量补读。

## 对 Hetu Bot-team 可借鉴（裁决输入）
1. 「模型打分、代码裁决」与我方 Arbiter 规则路由同构，阈值常量表可直接引入生产 Router。
2. 幂等键 `(trigger_message_id, agent_id)` 与我方 `msg-{id}-{agent}` 委派键一致，已实证同路线。
3. 审批 actionHash 绑定（只执行哈希对应动作）是 L1/L2 分级授权的强化项，列入 Phase 1。
4. 「事实落库、事件仅提示」与我方 EventStore 重放派生 pending 队列同构，验证我方断线续传设计。
