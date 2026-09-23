# agent-platform — 智能体中台（研发落地骨架）

> SUIG-10 首个研发增量。设计依据：`TechnicalDesign.md` v1.0 + 架构裁决 `adjudication.md`。
> 状态：**增量 1（骨架 + 存储层 + 开发栈）已实现**；**增量 2.1（用户与鉴权体系）、2.2（知识库 CRUD + 权限）已实现**；langgraph 编排 / ingest / 部署可观测为后续子任务（见 `docs/increment-1-breakdown.md`）。

## 1. 目录结构

```
agent-platform/
├─ api/                    # 进程组 ①：FastAPI 网关注入服务（对外 HTTP/WS/SSE）
│  ├─ main.py              # 应用工厂 + lifespan + 中间件装配
│  ├─ deps.py              # 依赖注入（当前用户、存储容器）
│  ├─ routers/             # health / auth / users / knowledge（2.1/2.2 实现）+ chat / document / agent / task（占位）
│  ├─ middlewares/         # 鉴权、限流、trace 埋点、全局异常
│  └─ schemas/             # Pydantic v2 请求/响应模型
├─ langgraph_service/      # 进程组 ②：LangGraph 独立编排服务（内网 HTTP+SSE）
│  ├─ main.py              # 内网入口 /v1/stream、/v1/resume
│  ├─ checkpointer.py      # RedisSaver 装配（唯一 Checkpoint 实现）
│  ├─ graph/               # rag_graph / router / state
│  └─ nodes/               # retrieve / generate 节点
├─ ingest/                 # 进程组 ②：文档摄入 worker（arq + Redis 队列）
│  ├─ worker.py            # 队列消费 + document.status 状态机
│  ├─ chunker.py           # ~250 token / overlap 100
│  ├─ embedding.py         # Embedding 服务封装
│  ├─ storage.py           # 双写 Milvus + ES + 回写 MySQL
│  └─ parser/              # pdf / docx / md / txt 解析
├─ core/                   # 进程组共用：配置、日志、存储层客户端
│  ├─ config.py            # pydantic-settings 分层配置
│  ├─ logging.py           # 结构化日志 + trace_id
│  ├─ exceptions.py        # 统一异常
│  └─ storage/             # MySQL / Milvus / ES / Redis 客户端 + 健康探活
├─ db/                     # 数据模型与索引定义
│  ├─ base.py              # SQLAlchemy 2.x DeclarativeBase
│  ├─ models/entities.py   # 6 张业务表
│  ├─ schemas_milvus.py    # Milvus Collection schema + index
│  ├─ mappings_es.py       # ES index mapping
│  └─ sql/01_schema.sql    # 开发栈初始化 DDL
├─ deploy/
│  └─ docker-compose.yml   # 开发栈：mysql / redis / milvus(etcd+minio) / es
├─ docs/
│  └─ increment-1-breakdown.md   # 剩余四块子任务拆解与排期
├─ pyproject.toml
└─ .env.example
```

**两组进程**（与设计一致，避免长会话阻塞网关）：

| 进程组 | 包含 | 说明 |
|---|---|---|
| ① 网关进程 | `api` | 无状态、对外，只做鉴权/限流/校验/SSE 透传 |
| ② 编排与摄入进程 | `langgraph_service`、`ingest` | 有状态编排 + CPU 密集摄入，独立扩缩容 |

## 2. 快速开始（开发栈）

```bash
cd agent-platform
cp .env.example .env
docker compose -f deploy/docker-compose.yml up -d      # mysql / redis / milvus / es
docker compose -f deploy/docker-compose.yml ps         # 等 healthcheck 全部 healthy

python -m venv .venv && . .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

uvicorn api.main:app --reload --port 8000              # 网关
uvicorn langgraph_service.main:app --reload --port 8100 # 编排（内网）
arq ingest.worker.WorkerSettings                        # 摄入 worker
```

健康探活（覆盖全部四类存储）：

```bash
curl http://127.0.0.1:8000/healthz     # 聚合，任一存储不健康 → 503
curl http://127.0.0.1:8000/healthz/live
```

## 3. 已落地的架构约束（按裁决执行）

1. **内部契约 HTTP+SSE** — `api` 通过内部 HTTP+SSE 调用 `langgraph_service`；gRPC 未引入。
2. **Checkpoint 只接 RedisSaver** — `langgraph_service/checkpointer.py` 是唯一实现，不提供 PostgresSaver 分支。
3. **双写一致性靠 `document.status` 状态机补偿** — 单 worker 串行双写；失败置 `status=3`，重跑前按 `doc_id` 清理两侧残留（可重入）。
4. **权限校验在查询路径强制** — `kb_id` 分区/过滤仅作检索优化，**不作安全边界**；`api/deps.py` 的 `require_kb_access` 是唯一准入点。

## 4. 增量边界

| 模块 | 增量 1 状态 |
|---|---|
| `core/`（配置、日志、四类存储客户端、健康探活） | ✅ 已实现 |
| `db/`（6 张表模型、Milvus/ES schema、初始化 DDL） | ✅ 已实现 |
| `deploy/docker-compose.yml`（四组件开发栈） | ✅ 已实现 |
| `api/` 应用工厂 + 中间件装配 + `/healthz` | ✅ 已实现 |
| `api/` 用户与鉴权（`/v1/auth`、`/v1/users`，JWT + api_key 哈希轮换） | ✅ 已实现（增量 2.1） |
| `api/` 知识库 CRUD + 权限（`/v1/kb`，`require_kb_access` 全接入） | ✅ 已实现（增量 2.2） |
| `api/` 业务路由（chat/document/agent/task） | ⏳ 骨架占位，增量 2 |
| `langgraph_service/` 编排图与节点 | ⏳ Checkpointer 已实现，图/节点增量 3 |
| `ingest/` 摄入链路 | ⏳ 队列与状态机骨架，增量 4 |
| 部署与可观测（K8s、OTel、Prometheus） | ⏳ 增量 5 |

> 占位路由返回 `501 Not Implemented` 并在响应体标注所属增量——不会伪装成已实现。

## 5. Python 版本

设计基线 3.10~3.11；`pyproject.toml` 声明 `>=3.10`。本地校验环境为 3.13（语法/导入检查通过），运行环境请以 3.11 为准。
