# Drama Engine Web（interaction.v1 前端）

Vite + React + TypeScript 前端，对接统一交互协议 `interaction.v1`
（见 `../docs/interaction_protocol_design.md`）。

## 设计要点

- **协议驱动 UI**：一切下行内容是 `InteractionMessage`，一切待回复是 `ReplyRequest`。
  组件按两轴降级链分派（`../docs/interaction_protocol_design.md` §9）：
  - 展示轴：`card.variant → card.kind → role → body.text`（`registry/cards.tsx`）
  - 输入轴：`reply.widget → reply.primitive`（8 原语兜底，`registry/widgets.tsx` + `components/inputs.tsx`）
- **前端零可见性过滤**（§2.3 铁律）：inbox 是 per-seat 投影，前端不做任何 secret/others 判断。
- **一套接口通吃多品类**：狼人杀/galgame/剧本杀/综艺/桌游共用同一组件，差异走 widget/card/props/panels。
- **两种主区呈现，同一数据面**：
  - 聊天流（`MessageFeed` + `Composer`）：狼人杀/剧本杀/综艺/桌游，多频道气泡。
  - 沉浸式舞台（`ImmersiveStage`）：文字冒险/galgame，全屏场景 + 逐句点击揭示 + 全屏视频 + 底部选项/输入 dock。
    由 `api/gamePresentation.ts` 的 `isImmersiveNarrative(kind)` 判定；两种呈现共用 `components/inputs.tsx` 的输入原语。

## 目录

```
src/
  types/interaction.ts     协议类型（唯一事实来源）
  api/
    client.ts              统一 DramaClient 接口 + 单例
    mockAdapter.ts         原型静态脚本 → v1（端点未就绪期，默认）
    v1Adapter.ts           直连 /inbox /reply /view（端点就绪后）
    mockData.ts            5 个演示游戏脚本
  registry/                widget / card 降级链注册表
  components/              inputs（8 原语，共享）/ Composer / MessageFeed / Message / ImmersiveStage
                           / Sidebar / RoundTable / ViewPlugins / Chrome
  hooks/                   useInbox（游标+回滚对齐）/ useStateView
  pages/                   Create / Host / Player / Viewer
```

## 运行

```bash
cd drama_engine/web
npm install

# 默认 mock 模式（无需后端，用演示脚本）
npm run dev        # http://localhost:5173

# 连真实后端（interaction.v1 端点就绪后）
VITE_API_MODE=v1 npm run dev
# 需先启动后端：python -m drama_engine.service.server --port 8766
# vite.config.ts 已把 /api 代理到 :8766

npm run build      # 类型检查 + 生产构建
npm run typecheck  # 仅类型检查
```

## 页面

| 路径 | 页面 | seat |
| --- | --- | --- |
| `/`、`/create` | 创建房间 | — |
| `/host/sessions/:id` | 主持人控制台（生命周期+圆桌+视图+回滚） | host |
| `/player?token=<sid[:seat]>` | 玩家交互 | player:<seat> |
| `/viewer/sessions/:id` | 观众公开流 | audience |

## 与后端的对接

前端只依赖 `interaction.v1` 契约。`v1Adapter` 需要后端提供：
`GET /api/sessions/:id/inbox`、`POST /api/sessions/:id/reply`、`GET /api/sessions/:id/view`。
生命周期/回滚沿用现有 `/api/sessions/*` 端点。这些归一端点由投影器层提供
（见协议文档第四/五部分）；就绪前用 mock 模式开发。
