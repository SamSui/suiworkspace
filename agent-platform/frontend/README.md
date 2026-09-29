# agent-platform · 前端（P4 中台统一入口）

> SUIG-38 步骤 2 交付：**工程骨架 + 路由 + base API/请求封装 + 核心页面（骨架）**。
> 技术栈：React 18 + TypeScript(strict) + Vite 5 + TailwindCSS + Zustand + TanStack Query + Recharts（占位）+ react-router。

## 目录结构

```
frontend/
├─ vite.config.ts          # 开发代理：/v1、/healthz、/metrics → 后端网关(默认 8000)
├─ src/
│  ├─ main.tsx / App.tsx   # 入口：注入 QueryClient + Router，启动时恢复登录态
│  ├─ index.css            # Tailwind 基座 + 设计令牌（ADR §6.1）
│  ├─ router/index.tsx     # 路由表 + Protected 守卫（未登录 → /login?redirect=回跳）
│  ├─ api/                 # 请求封装：client(统 401 拦截/错误映射/SSE) + auth/kb/doc/chat
│  ├─ store/               # Zustand：auth（登录态）、chat（会话/SSE 增量/HITL）
│  ├─ components/          # 布局(顶栏导航) + ui(骨架/徽章/四态反馈)
│  └─ pages/               # Login / Dashboard / 知识库(列表+详情状态机) / 对话(三栏) / 监控 / P2 占位
└─ scripts/                # 逻辑级冒烟测试 cases/*.test.ts（node scripts/smoke-runner.mjs）
```

## 运行（开发）

```bash
npm install
npm run dev        # 起于 http://localhost:5173，代理 /v1 → http://127.0.0.1:8000
npm run build      # tsc -b && vite build（严格类型 + 产物到 dist/）
npm run lint       # ESLint（src 全量）
```

后端网关默认在 `127.0.0.1:8000`（`agent-platform` 的 `uvicorn api.main:app`）。
如需覆盖代理目标：`VITE_API_PROXY=http://<host>:<port>`（复制 `.env.example`→`.env.local`）。

## 部署（生产）

- 纯静态构建产物 `npm run build` → `dist/`。
- 由部署侧反代把 `/v1`、`/healthz`、`/metrics` 转发到后端网关；前端与应用同源，无 CORS。
- 生产 token 持久化策略：默认走内存（`VITE_TOKEN_STORAGE` 置 `localStorage` 可放宽），
  建议生产配合安全存储（避免 XSS 窃取）。见 `.env.example`。

## 对接后端契约

以 `agent-platform/docs/api-contract.md` 与后端源码为准，本步**不虚构端、不续桩**：

- 鉴权：`POST /v1/auth/token`（公开）→ `GET /v1/users/me` → `POST /v1/auth/logout`(204 本地清 token)。
- 知识库：`GET/POST /v1/kb`、`PATCH/DELETE /v1/kb/{id}`。
- 文档/任务：`POST /v1/doc`(202, multipart)、`GET /v1/task/{id}`(status 0/1/2/3 + progress)、`DELETE /v1/doc/{id}`。
- 对话：`POST /v1/chat/stream`（SSE `token/interrupt/done/error`+`:ping` 逐事件透传）、`POST /v1/chat/resume`（HITL）、`POST /v1/chat`（非流）。
- RAG 引用第 3 层 `GET /v1/doc/{id}/chunk/{chunk_id}`：本期后端未落 → 前端按 P2 占位，不阻塞。

## 三条用例冒烟

`node scripts/smoke-runner.mjs`（对真实 `src/api/client.ts` 打桩验证）：

1. **鉴权跳转**：401 → 清 token + 回跳 `/login?redirect=…`。
2. **错误码映射**：`not_found(404)` → 可读文案。
3. **SSE 无卡顿**：事件跨 chunk 边界逐事件增量解析，token `你好` 按序累积 + interrupt/done/error 分发。

> 完整 E2E（真实浏览器 + 后端）由随测试在 SUIG-36 步骤 4 覆盖；本步只做逻辑级冒烟（build/dev/smoke 均通过）。
