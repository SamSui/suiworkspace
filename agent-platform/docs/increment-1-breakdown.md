# SUIG-10 研发落地 · 增量拆解与排期

> 编制：随研发 ｜ 依据：`TechnicalDesign.md` v1.0 + 架构裁决 `adjudication.md`
> 增量 1 已交付（骨架 + 存储层 + 开发栈），本文档覆盖**剩余四块**的拆解、依赖与排期。

---

## 0. 总览

| 增量 | 块 | 子任务数 | 关键路径 | 预估 |
|---|---|---|---|---|
| ✅ 1 | 骨架 / 存储层 / 开发栈 | — | — | 已交付 |
| 2 | api 网关 | 6 | 是 | 3.5 人日 |
| 3 | langgraph 编排 | 7 | 是 | 5.0 人日 |
| 4 | ingest 摄入 | 6 | 否（与 3 并行） | 3.0 人日 |
| 5 | 部署与可观测 | 5 | 否（尾部） | 2.5 人日 |

**关键路径**：增量 2 → 增量 3 → 增量 5。增量 4 可与增量 3 并行推进（两者只共享存储层，已冻结）。

**验收口径**：每个子任务交付 = 代码 + 单元测试 + 在 issue 上回传结论；块级交付额外需要一次架构复核。

---

## 增量 2：api 网关（3.5 人日）

网关只做鉴权 / 限流 / 校验 / SSE 透传，**不引入任何检索或 LLM 逻辑**。

| # | 子任务 | 产出 | 依赖 | 验收 |
|---|---|---|---|---|
| ✅ 2.1 | 用户与鉴权体系 | JWT 签发/校验、`user` CRUD、api_key 哈希轮换 | 增量 1 存储层 | 无 token→401；过期→401；合法→放行 |
| ✅ 2.2 | 知识库 CRUD + 权限 | `/v1/kb` 全套；`require_kb_access` 接入**所有**读写路径 | 2.1 | 越权访问他人 kb → 404（不泄漏存在性） |
| ✅ 2.3 | 对话接口 | `/v1/chat`、`/v1/chat/stream`(SSE)、`/v1/chat/resume` | 2.1、增量 3 契约 | SSE 逐字透传不缓冲；`trace_id` 贯穿 |
| 2.4 | 文档上传接口 | `/v1/doc` 上传→校验→落盘→`document(status=0)`→入队，返回 202 | 2.2 | 类型/大小白名单生效；请求内不解析 |
| 2.5 | 任务查询 + Agent 配置 | `/v1/task/{id}`、`/v1/agent` CRUD | 2.2 | 状态与 `document.status` 一致 |
| 2.6 | 网关单测 + 接口文档 | pytest 覆盖鉴权/限流/权限/上传校验；OpenAPI 契约 | 2.1–2.5 | 覆盖率 ≥80%；契约过架构复核 |

**风险**：2.3 与增量 3 的 SSE 契约需先冻结（`POST /v1/stream` 的事件格式），否则两侧返工。

### 2.1（已交付）用户与鉴权体系

新增路由（均已接入鉴权中间件）：

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| POST | `/v1/users` | 公开 | 注册（引导首个用户），`api_key` 明文仅回显一次 |
| POST | `/v1/auth/token` | 公开 | `name` + `api_key` → JWT（`exp` 内置） |
| GET | `/v1/users/me` | 需认证 | 当前用户资料 |
| GET | `/v1/users/{id}` | 需认证 | 按 id 取用户 |
| POST | `/v1/users/me/api-key/rotate` | 需认证 | 轮换 api_key（旧 key 即刻失效） |

契约要点：
- **JWT**：`pyjwt`（已是主依赖），`sub`=user_id，`exp`=签发时刻+`JWT_EXPIRE_MINUTES`。
- **api_key**：只存 `sha256` 哈希（`core/security.py`），明文仅创建/轮换成功回显一次；轮换=整行替换哈希。
- **鉴权准入**：`AuthMiddleware` 放行合法 token 并注入 `scope.state.user_id`；无/过期/串改 token → 401。
- 权限粘连（`es_chunk_ref`、`require_kb_access`）按裁决**不在本子任务接入**，2.2 收口。
- 验收口径（无 token→401、过期→401、合法→放行）已用单测 `tests/test_auth.py`、`tests/test_security.py` 与冒烟门禁 5 覆盖。

### 2.2（已交付）知识库 CRUD + 权限

新增路由（`get/update/delete` 全部经 `kb_access` 依赖注入归属校验）：

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/v1/kb` | 需认证 | 列出**当前用户**的库（owner_id + status 过滤） |
| POST | `/v1/kb` | 需认证 | 创建自己名下新库（owner=当前用户） |
| GET | `/v1/kb/{id}` | `require_kb_access` | 越权/不存在 → 404 |
| PATCH | `/v1/kb/{id}` | `require_kb_access` | 改名；越权/不存在 → 404 |
| DELETE | `/v1/kb/{id}` | `require_kb_access` | 软删（status=0）；越权/不存在 → 404 |

契约要点：
- **`kb_access` 依赖（`api/deps.py`）**：把路径参数 `kb_id` 绑定进 `require_kb_access`，路由直接 `Depends(kb_access)` 拿已过归属校验的 KB；校验失败与不存在统一 `NotFound(404)`，**不泄漏存在性**（裁决 #4）。
- **软删**：`DELETE` 置 `status=0`，与 `require_kb_access` 的 `status != 1 → 404` 语义一致，被删库对任何路径即刻不可见。
- 验收口径（越权访问他人 kb → 404）已用单测 `tests/test_kb_integration.py` 与冒烟门禁 6 覆盖。

### 2.3（已交付）对话接口 SSE

网关侧**接口骨架 + 契约实现**，只做透传 / 编排调用，不接检索 / LLM / 存储业务（裁决 #1）。编排的实际检索/生成节点属增量 3，此处以符合冻结契约的 SSE stub 对接。

路由（均走既有 JWT 鉴权中间件；无 token → 401）：

| 方法 | 路径 | 行为 | 鉴权 |
|---|---|---|---|
| POST | `/v1/chat` | 非流式：订阅编排流、读到 `done` 事件后返回 `{thread_id, message_id, usage}` | 需认证 |
| POST | `/v1/chat/stream` | SSE **逐字节透传**编排流，不缓冲 | 需认证 |
| POST | `/v1/chat/resume` | 以 thread_id 续访挂起的图；无该会话/无权 → 404 | 需认证 |

契约要点：
- **SSE 透传**（`api/upstream.py`）：`chat_stream` 对下游编排响应体**按字节转发**（迭代器逐 chunk yield 给 `StreamingResponse`），不重组、不解析、不攒包；心跳 `: ping`、`event`/`data`/`seq`、`error` 的 `code`+`trace_id` 均原样到达前端。中间件保持纯 ASGI（未退 `BaseHTTPMiddleware`），对 SSE 长流不缓冲。
- **非流式收敛**（`chat_once`）：订阅编排流、逐事件解析，读到 `done` 摘 `message_id`+`usage` 返回（与 MySQL `message` 行对齐，token_count 落库有源）；编排 `error` 事件 → 502，`code`+`trace_id` 落响应 body（契约第 2 条）。
- **resume 归属校验**（`api/deps.py::require_thread_access`）：以 thread_id 查 `Conversation` 的归属，无该会话/非当前用户所有/已停用 → 统一 `NotFound(404)`（同 `require_kb_access` 的越权语义，不泄漏存在性）。用显式函数调用而非 FastAPI 依赖，避开与 body 字段冲突。
- 验收口径（三类）已用单测 `tests/test_chat.py` 覆盖（9 项，全部 PASS），下游用 `tests/sse_stub.py` 的契约 SSE stub + `api.upstream.set_client_factory` 注入，全程不依赖真实网络。

---

## 增量 3：langgraph 编排（5.0 人日）

| # | 子任务 | 产出 | 依赖 | 验收 |
|---|---|---|---|---|
| 3.1 | LLM client 层 | 超时（connect/read 双）+ 指数退避 + 熔断 + 多 provider 兜底 | — | 主 provider 挂掉自动切备；熔断后可恢复 |
| 3.2 | Checkpoint 接入 | `build_checkpointer` 实例化 + 会话状态读写 + `thread_config` | 增量 1 | **多实例**读写同一 `thread_id` 状态一致 |
| 3.3 | router 节点 | 意图判定 → `need_retrieval` / `route` 分支 | 3.1 | 需检索/直答两类用例判定正确 |
| 3.4 | retrieve 节点 | 混合检索三段式：并发召回(各 top30) → 融合去重 → Rerank(top5) → 缓存 | 增量 1、3.2 | 缓存命中跳过检索；Rerank 耗时纳入埋点 |
| 3.5 | generate 节点 + SSE | prompt 组装 + 流式输出 + 引用列表 | 3.1、3.4 | 首 token 时延达标；引用可回链 chunk_id |
| 3.6 | HITL 挂起/恢复 | `interrupt` 节点 + `/v1/resume` 续跑 | 3.2 | 挂起后进程重启仍可恢复（checkpoint 生效） |
| 3.7 | 多实例一致性验证 | 双副本并发压测报告 | 3.2–3.6 | 无状态丢失；断点恢复成功率 100% |

**关键决策已锁定**：内部契约 HTTP+SSE（不做 gRPC）、Checkpoint 只接 RedisSaver——本增量不再重开。

**风险**：3.4 的 Rerank 是延迟大头，需在 3.7 压测中拿到 P99 数据反推 topK 调参。

---

## 增量 4：ingest 摄入（3.0 人日，可与增量 3 并行）

| # | 子任务 | 产出 | 依赖 | 验收 |
|---|---|---|---|---|
| 4.1 | 文档解析器 | pdf / docx / md / txt 分发；≤200MB 校验 | — | 四格式样例解析成功；超限拒绝 |
| 4.2 | 切分器 | ~250 token / overlap 100；**tokenizer 选型定稿** | 4.1 | 切片数与 token 分布符合预期 |
| 4.3 | Embedding 接入 | 批量向量化，维度对齐 `MILVUS_DIM` | 4.2 | 维度一致；失败可重试 |
| 4.4 | 双写 + 状态机补偿 | Milvus/ES 串行双写；失败置 3、按 doc_id 清理重入 | 4.3 | **注错测试**：任一侧失败后可完整重跑 |
| 4.5 | worker 重试与幂等 | arq 退避重试；`file_hash` 去重；重复投递幂等 | 4.4 | 同文档重传不产生重复切片 |
| 4.6 | 端到端摄入测试 | 上传→检索可见 全链路 | 4.5、增量 3 检索 | 摄入完成后可被检索命中 |

**关键决策已锁定**：不做分布式事务，靠 `document.status` 驱动补偿——4.4 的注错测试是这块的验收核心。

---

## 增量 5：部署与可观测（2.5 人日）

| # | 子任务 | 产出 | 依赖 | 验收 |
|---|---|---|---|---|
| 5.1 | 容器化 | `api` / `langgraph_service` / `ingest` 三份 Dockerfile | 增量 2–4 | 镜像可构建；健康检查通过 |
| 5.2 | K8s 编排 | Deployment / Service / HPA / liveness+readiness 探针 | 5.1 | 滚动更新不中断；网关按 QPS 弹性 |
| 5.3 | OpenTelemetry 埋点 | 接口 / LLM / Milvus / ES / 各节点耗时；`trace_id` 贯穿 | 增量 3 | 一次请求可在链路图中完整还原 |
| 5.4 | 指标与告警 | Prometheus 指标 + Grafana 面板 + 告警规则 | 5.3 | 缓存命中率、检索 P99、错误率可见并告警 |
| 5.5 | 压测与容量基线 | 并发会话压测报告 + 容量建议 | 5.2、5.4 | 给出单副本承载并发数与扩容阈值 |

---

## 依赖关系

```
增量1(✅) ──┬─> 增量2(api) ──> 增量3(langgraph) ──> 增量5(部署可观测)
            │                        ↑
            └─> 增量4(ingest) ───────┘   （4 与 3 可并行；4.6 需 3 的检索能力）
```

**跨块阻塞点**：
- 2.3 依赖增量 3 的 SSE 事件格式 → 需在增量 2 启动时先冻结契约（建议架构侧参与）。
- 4.6 依赖 3.4 的检索能力 → 增量 4 前 5 个子任务可先行。

---

## 排期建议

| 周 | 内容 |
|---|---|
| W1 | 契约冻结（SSE 事件格式 + `/v1/*` 路径）+ 增量 2 全部 |
| W2 | 增量 3（3.1–3.5）+ 增量 4（4.1–4.4）并行 |
| W3 | 增量 3 收尾（3.6–3.7）+ 增量 4 收尾（4.5–4.6） |
| W4 | 增量 5 全部 + 块级架构复核 |

---

## 需要架构侧确认的两点

1. **SSE 事件格式**（增量 2 启动前冻结）：建议 `event: token|interrupt|done|error` + `data: {...}`，`trace_id` 放响应头。若架构侧已有前端约定，请以既有为准。
2. **tokenizer 选型**（增量 4.2 前置）：影响切片边界与 `message.content` 摘要长度，建议与后续 Ragas 评估基准一起定。
