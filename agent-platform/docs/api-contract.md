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

## 变更记录

| 版本 | 变更 |
|---|---|
| v0.1 | 增量 2.4/2.5 首次落地：`/v1/doc` 上传/查询/删除、`/v1/task/{id}`、`/v1/agent` 查询/创建 |