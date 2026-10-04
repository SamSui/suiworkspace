# 增量 2 网关 API 契约（2.4 / 2.5 / 2.6）

> 对齐 SUIG-16（网关收尾）。完整机器可读契约即时生成于 `GET /openapi.json`，
> 本文件为增量 2.4/2.5 新增接口的对外契约说明 + 变更记录。
> 鉴权：除免鉴权路径（`/v1/auth/token`、`POST /v1/users`、`/healthz*`、`/docs*`）外，
> 均需 `Authorization: Bearer <JWT>`，缺失/无效 → 401 `unauthenticated`。

## 错误体

所有错误统一：`{"error": {"code", "message", "detail?"}}`。`code` 稳定可编程。

| code | HTTP | 语义 |
|---|---|---|
| `unauthenticated` | 401 | 无/无效 token |
| `not_found` | 404 | 资源不存在或越权（统一语义，不泄漏存在性） |
| `invalid_argument` | 422 | 请求参数不合法（含上传类型/大小超限） |
| `rate_limited` | 429 | 触发限流（附 `Retry-After`） |

## 2.4 文档上传 `/v1/doc`

### `POST /v1/doc` — 上传并入队（202）

multipart/form-data：
- `kb_id` (Form, 必填)：目标知识库。
- `file` (File, 必填)：文件本体。

处理：鉴权 → kb 归属校验（越权/不存在 404）→ 类型/大小白名单（默认
`.pdf,.docx,.md,.txt`，≤200MB；越限 422）→ sha256 去重 → 落盘本地 →
写 `document(status=0)` → 入队 arq（尽力而为）→ **202**。

响应 `202`：`{"id","kb_id","file_name","status":0,"chunk_count":0}`。

- 无 token → 401；类型/大小超限 → 422 `invalid_argument`。

### `GET /v1/doc/{doc_id}` — 文档元信息
- 200 `DocumentOut`；不存在/越权 → 404；无 token → 401。

### `DELETE /v1/doc/{doc_id}` — 删除
- 204；不存在/越权 → 404。（ES/Milvus 残留由 ingest worker 依裁决 #3 清理。）

## 2.5 任务查询 `/v1/task` 与 Agent 配置 `/v1/agent`

### `GET /v1/task/{task_id}` — 轮询摄入状态
- 任务即文档：`task_id` == `document.id`。`status` 与 `document.status` **严格一致**
  （0 未处理 / 1 处理中 / 2 完成 / 3 失败）。
- 200 `TaskOut`；不存在/越权/非法 id → 404；无 token → 401。

### `/v1/agent`
- `GET /v1/agent?kb_id=<id>`：列出该 kb 下当前用户配置（200）；越权/不存在 404；无 token 401。
- `POST /v1/agent`：创建（201）；`kb_id` 归属校验；越权 404。

## 2.6 RAG 引用「点击回原文」 `/v1/doc/{doc_id}/chunk/{chunk_id}`（SUIG-39）

RAG 引用 `[N]` 点回原文的核心契约端点（前端终裁方案 A，薄透传）：

- `GET /v1/doc/{doc_id}/chunk/{chunk_id}` → 200 `{"chunk_id","doc_id","kb_id","text"}`。
- 底层复用 `core/storage/es.py` 的 `fetch_chunks` 从 ES 取正文，**无新检索逻辑 / 无新存储改动**。
- 鉴权：Bearer JWT；文档级归属经 `require_kb_access`（与 `GET /v1/doc/{doc_id}` 同源 404 语义）。
- 404 语义：文档不存在/越权、chunk 缺失、或 chunk 的 `doc_id`/`kb_id` 与文档不一致（跨文档/跨库越权）→ 统一 404，不泄漏他人切片是否存在；无 token → 401。

## 2.7 RAG 引用事件透出（SSE `cite`，可选增强）

`/v1/chat/stream` 生成流中、`token` 首帧之前追加引用事件（向后兼容，不改 token 文本格式）：

- `event: cite`  data `{"seq":N,"ref":N,"chunk_id":"...","doc_id":<id>,"kb_id":<id>}`。
- `ref` 即 LLM 内联 `[N]` 的下标：前端据此把 `[N]` 绑定到 `doc_id/chunk_id`，并以
  `GET /v1/doc/{doc_id}/chunk/{chunk_id}` 拉取原文高亮。
- 网关对编排逐字节透传，前端按需消费 `cite` 事件；既有 `token`/`interrupt`/`done`/`error` 契约不变。

## 变更记录

| 版本 | 变更 |
|---|---|
| v0.1 | 增量 2.4/2.5 首次落地：`/v1/doc` 上传/查询/删除、`/v1/task/{id}`、`/v1/agent` 查询/创建 |
| v0.2 | SUIG-39：新增 `GET /v1/doc/{doc_id}/chunk/{chunk_id}`；SSE 追加可选 `cite` 引用事件（向后兼容） |