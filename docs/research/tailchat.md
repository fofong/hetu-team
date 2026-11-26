# Tailchat 侵入改造评估报告（Phase 0 裁决输入）

基于 v1.11.23（commit 1525b3e），仓库 `poc/tailchat-repo`（msgbyte/tailchat，Apache-2.0），只读研究，2026-11-26。

## 1) 信息架构：Space 已死，实际 Group→Panel 两级
- Space 层级 v1.11 已整体移除（redux 切片仅 chat/global/group/ui/user；types/model 仅 group/inbox/message/user）。
- 现行：Navbar 图标栏 → Group → Panel（频道）。`GroupInfo.panels` 内嵌群文档（`packages/types/src/model/group.ts` `GroupPanelType.TEXT/GROUP/PLUGIN`）。
- 路由硬编码 panelId：`client/web/src/routes/Main/Content/index.tsx` → `Group/index.tsx`（`/:panelId` + `GroupPanelRedirect`）→ `Group/Panel.tsx`。
- 与 QQ 单窗口差异：①群与消息流之间多 Panel 层；②左列 w-18 图标栏非文字群列表；③成员栏默认折叠（TextPanel `setRightPanel` 弹出）；④有多窗口弹出面板概念（`usePanelWindow.ts`）。

## 2) 插件体系：注册表式槽位，无主布局替换点
- mini-star v1.11 只管插件加载（`client/web/src/plugin/manager.ts`）；UI 注入点=`plugin/common/reg.ts` 约 20 个注册表：`pluginCustomPanel`（position: personal/setting/groupdetail/navbar-*）、`pluginGroupPanel`、`getMessageRender`/`messageInterpreter`、`pluginChatInput*`、`pluginRootRoute`/`pluginPanelRoute`、`pluginPanelActions`、`pluginInboxItemMap`、`pluginSettings` 等。
- **无任何"替换主布局/路由表"槽位**。隐藏 Panel 必须 fork 主工程：前端布局/路由 6-8 处 + 状态层 2-3 处 + 服务端 2-3 处，合计 **~12-15 侵入点**。

## 3) Bot API：能收发，不能改消息 → 原生做不了流式
- HTTP 回调 bot：`server/services/openapi/bot.service.ts`（@机器人 POST callbackUrl）；SDK `sendMessage/replyMessage`；WS bot 见 docs。
- 能力边界=`chat/message.service.ts` action 清单：send/fetch/recall/delete/search/reaction——**无更新消息内容 action**。
- 流式进度条：现有 API 做不到；最小改造=服务端加 `updateMessage` action（客户端 reducer `updateMessageInfo` 已就绪），约 2-3 人日。

## 4) 断线恢复：最终一致，无严格不丢证明
- socket.io 自动重连（`client/shared/api/socket.ts`）；`onReconnect` 全量重拉 + 当前会话重取最新 50 条；去重靠 `_id` uniqBy；分页游标 ObjectId（无 seq）；推送 fire-and-forget，离线即丢靠重拉补偿。
- **评价：at-least-once + 客户端去重，断线超 50 条缺口无 gap 检测**——对 IM 够用，对 Agent 高频流式偏弱。（我方 EventStore seq 游标补发不丢不重强于此。）

## 5) 裁决清单
| 改造项 | 侵入点 | 粗估 |
|---|---|---|
| 去 Panel 层（路由拍平） | 前端 6-8 + 服务端 2-3 | 5-8 人日 |
| QQ 式群列表 | Navbar/GroupNav、Sidebar | 2-3 人日 |
| 成员栏常驻 | TextPanel 改固定列 | 1-2 人日 |
| 服务端 updateMessage（流式基础） | message.service | 2-3 人日 |
| Agent 流式渲染 | reg.ts 槽位内 | 2-3 人日 |
| （可选）seq 补偿强化 | chat 模型+切片 | 3-5 人日 |

**合计 15-25 人日**达 QQ 单窗口+流式，且**必须 fork**（纯插件不可行），fork 维护成本为主要风险；此估算还不含 Agent 协作引擎本体（仲裁/授权/预算/A2A 全需另建）。
