# TeamAgentX 调度/协同机制研究报告

仓库根：`poc/teamagentx`（MIT）。只读研究，2026-11-26。

## 1. 群聊触发模式（三模式已收敛为两种）
- `server/src/core/agent/agent-handler/trigger-mode.ts` `normalizeTriggerMode()`：auto 与 coordinator 已合并为「智能协作」（存储值 'coordinator'），实际只剩 `coordinator | manual`。
- `handler.ts`（`receivedMessage` L215-648）统一调度入口：
  - **manual**：助手消息 @ 只展示不触发（`assistantMentionTriggerSkipped`），无 watchdog、无协调器兜底；人类 @ 只取第一个。
  - **coordinator**：人类无 @ 消息按优先级「直达回复 > 引用回复 > 默认助手 > 协调器兜底」（`humanUnroutedMessage`）；多 @ 升级协调器裁决（`humanMultiMention`）；单 @ 快路径 `enqueueAgentTask` 省一次协调器 LLM。
  - 业务助手自由文本 @ **永不触发派发**，委派只能走结构化 `mention_agents`。

## 2. 协调助手裁决接力派发
- `coordinator-dispatch.ts` `runCoordinatorDispatch()`：LLM 产出 `DispatchDecision{dispatch|no_dispatch|ask_owner|cannot_dispatch, assignments[], dispatchMode: parallel|serial}`；显式 @ 强制 dispatch。
- `mention.tools.ts` `mention_agents`：助手登记结构化交接意向（目标+任务），TaskQueue settled 后统一处理。
- `structured-handoff.service.ts` `settleStructuredHandoff()`：按 `HandoffMentionBatch` 归一化串行阶段：单目标=接力、多目标=并行收敛回发起者、多阶段=串行链（前序产出显式注入下一棒 `buildSerialChainTrigger`，不依赖群历史）。
- 并行汇合 `parallel-batch-tracker.ts`（最后一个完成才唤起协调器统一裁决）；串行链 `serial-chain-tracker.ts`。

## 3. 防扇出熔断（四类护栏 + watchdog）
`structured-handoff.service.ts` + `structured-handoff-runtime.ts` + `config/index.ts`：

| 护栏 | 配置 | 默认 | 机制 |
|---|---|---|---|
| fanout | `handoffFanoutMax` | 20 | 单次交接目标数上限 |
| depth | `handoffDepthMax` | 100 | `HandoffContext.depth` 链深度 |
| budget | `handoffBudgetMax` | 20 | **按 rootMessageId 全局派发预算**，reserve/release 预留回滚，真正失控兜底 |
| revisit | `handoffRevisitMax` | 5 | 目标在 lineage 血缘中重访次数（环检测宽松版） |

第二重独立熔断 `stall-watchdog.ts`：`consecutiveDispatches`（两次人类发言间协调器连续救援上限 5）与 `consecutiveNoActivity`（空闲 3 轮），**人类发言即清零**；用户发言可 abort 进行中调度。触发后 `notifyGuardrail` 以协调助手身份发群消息并 @群主。无文本相似度环检测——靠 lineage 重访计数。

## 4. 执行状态回传群聊
- `status.ts` 全局回调注册表：`globalEmit`（成品）、`globalEmitStream`（流式增量）、`globalEmitTyping`（pending|executing 气泡）、`globalEmitDone`（耗时/token）、`globalEmitStatus`（agentId→idle|executing|busy + queueCounts）。
- `schema.prisma` `TaskQueue`：`status: pending|executing|interrupted|cancelled`，`handoffContext` JSON 持久化使熔断状态跨排队/恢复存活。
- `processor.ts` `streamCallback` + `streamEventsCache`（key=room_message_agent）断线重放式流式。
- 看板 `workbench.service.ts`：`draft|dispatched|in_progress|waiting_review|needs_input|completed`；协调决策留痕 `coordinator-log/`。

## 对 Hetu Bot-team 可借鉴（裁决输入）
1. 结构化交接（工具登记而非文本 @）——我方 @mention 触发已同构，`mention_agents` 工具列入 Phase 1。
2. 按 rootMessageId 预算制熔断 + 血缘重访计数——补强我方五预算（现仅任务级）。
3. 「人类发言清零一切自动调度」——防 Agent 互聊失控的最低成本铁律，建议直接采纳进 Arbiter。
4. 串行链前序产出显式注入——协同模式（collaborative）执行计划设计参考。
