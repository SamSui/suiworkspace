# 增量 2 门禁 · 运行时冒烟报告

> 执行：随研发 ｜ 日期：2026-09-23 ｜ 对应门禁：架构裁决《SUIG-10 增量1 验收裁决》第四节
> 更新：授权后（Python 3.12 建 venv + 装依赖）门禁 2/3/4 全部实跑通过；**增量 2.1 增补门禁 5（用户与鉴权闭环）亦实跑通过**。

## 0. 最终结论

| 门禁项 | 状态 | 说明 |
|---|---|---|
| 1. compose 六服务全部 healthy | ✅ **通过** | 六服务实测 healthy，且逐项做了功能性验证 |
| 2. 四类存储客户端 `connect` + `health()` 实跑 | ✅ **通过** | `/healthz` 返回 200，四类全 ok |
| 3. FastAPI 实跑启动 + OpenAPI 可访问 | ✅ **通过** | openapi.json 200，12 条路由齐全 |
| 4. `AsyncRedisSaver` 构造 + `asetup()` 实跑 | ✅ **通过** | `asetup() OK` + redis roundtrip |
| 5.（增量 2.1）用户与鉴权闭环 | ✅ **通过** | 注册→换 JWT→放行；无/过期 token → 401 |

**冒烟共抓到 6 处运行期缺陷**（"编译通过 ≠ 跑得起来"的直接证据），全部已修；增量 2.1 新增门禁 5 后 **OVERALL: PASS**。

## 增量 2.1 增补：用户与鉴权闭环（门禁 5）

| 步骤 | 实测 |
|---|---|
| `POST /v1/users` 注册 | 201，`api_key` 明文仅回显一次 |
| `POST /v1/auth/token` 换 JWT | 200，`token_type=bearer` |
| 无 token 访问 `/v1/users/me` | 401 ✅ |
| 过期 token 访问 `/v1/users/me` | 401 ✅ |
| 合法 token 访问 `/v1/users/me` | 200 ✅ |

**说明**：鉴权不引入新第三方契约——JWT 用主依赖 `pyjwt`，api_key 哈希用标准库 `sha256`；`api_key` 只存哈希不存明文，轮换即整行替换。

## 补充(授权后第二轮)：Python 依赖相关修复

冒烟在其正跑通前暴露了 6 处仅运行期可见的问题，已全部修复并复验通过：

| # | 缺陷 | 修复 |
|---|---|---|
| 3.1 | MinIO 旧 pin 已下架 | pin 到实测 `RELEASE.2024-01-05T22-17-24Z` |
| 3.2 | MinIO healthcheck 用不存在 curl | 改用内置 `mc ready local`；并修废弃 env |
| 3.3 | `python-multipart` 缺失 | 移入 pyproject 主依赖 |
| 3.4 | FastAPI 拒绝 `_` 前缀参数 | 5 个占位路由修名 |
| 3.5 | ES SDK 9.x 不兼容服务端 8.13 | pin `<9`（8.x SDK 发 compatible-with=8） |
| 3.6 | RedisSaver 需 RediSearch | compose 改 `redis/redis-stack-server` |

---

## 1. 环境前提

架构裁决第四节判断"Docker daemon 未运行"——复核后实际是**可用的**：Docker Desktop 已在运行（Server 24.0.6 / Compose v2.22.0），本机已有其它工作负载容器（`bidmaster-postgres`、`nexus3`）。

因此环境阻塞**部分自解**：Docker 侧不再阻塞，Python 依赖侧仍需授权。

- 端口：3306 / 6379 / 9200 / 19530 / 9091 / 9000 / 9001 冒烟前均为空闲，与既有容器无冲突。
- 资源：磁盘剩余 280GB；内存 31.7GB 总 / 8.9GB 空闲。
- **镜像拉取**：本机配置的两个 registry mirror（163 / USTC）均已失效（不可达），Docker Hub 直连亦不稳定（间歇 EOF）。逐个重试后 5 个镜像拉取成功；`minio/minio:RELEASE.2023-03-20T20-16-18Z` 始终失败——原因见 3.1。

---

## 2. 门禁项 1：六服务 healthy（通过）

```
NAME        STATUS
ap-es       Up 3 minutes (healthy)
ap-etcd     Up 3 minutes (healthy)
ap-milvus   Up 45 seconds (healthy)
ap-minio    Up About a minute (healthy)
ap-mysql    Up 3 minutes (healthy)
ap-redis    Up 3 minutes (healthy)
```

### 2.1 功能性验证（不止于 healthy 灯）

healthy 只说明探针通过，故逐项做了实际读写/查询验证：

**MySQL —— `db/sql/01_schema.sql` 真的执行了**（这是增量 1 从未验证过的 DDL）：

```
Tables_in_agent_platform: agent_config, conversation, document, knowledge_base, message, user
document.status      -> tinyint   （与 ORM 的 SmallInteger 一致 ✅）
document.file_hash   -> char(64)
外键数量             -> 6
```

六张表全部建出，列类型与 ORM 定义吻合，外键齐全——**DDL 无语法/语义错误**。

**Redis**：`PING` → `PONG`
**Elasticsearch**：`_cluster/health` → `status: green`，`number_of_nodes: 1`
**Milvus**：`GET /healthz` → `OK`

---

## 3. 冒烟抓到的两个真实缺陷（已修）

### 3.1 MinIO 镜像 pin 的 tag 已从 Docker Hub 下架

`minio/minio:RELEASE.2023-03-20T20-16-18Z` 拉取始终失败。MinIO 会持续下架旧 release tag，2023-03 的 tag 已不在。

**修法**：pin 到本机实测可用的 `RELEASE.2024-01-05T22-17-24Z`（由镜像内 `release` label 核实，非 `latest`）。

> 这条正是"编译通过 ≠ 跑得起来"的典型：compose `config` 解析完全通过，只有真拉镜像才暴露。

### 3.2 MinIO healthcheck 用了镜像里不存在的 `curl`

原 healthcheck 为 `curl -f http://localhost:9000/minio/health/live`。实测该镜像内 **既无 curl 也无 wget**，只有 `mc`：

```
$ command -v curl || echo NO_CURL
NO_CURL
$ command -v mc
/usr/bin/mc
```

后果：MinIO 进程其实**完全正常**（S3 API 9000 / Console 9001 均在监听），却被探针判为 unhealthy，导致 `docker compose up` 直接 `dependency failed to start`——Milvus 因此起不来。

**修法**：改用镜像内置 alias 的 `["CMD", "mc", "ready", "local"]`，实测返回 `The cluster is ready`（exit 0）。

**顺带修正**：`MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` 已废弃，启动日志要求改用 `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`，一并改正。

---

## 4. 阻塞项：门禁 2/3/4 需要 Python 依赖

三项都需要第三方库（`sqlalchemy` / `asyncmy` / `pymilvus` / `elasticsearch` / `redis` / `pydantic-settings` / `fastapi` / `langgraph-checkpoint-redis`），本机无任何可用环境：

- 系统 Python 3.13 / 3.12 均未安装上述依赖；
- 本机其它 venv（`BidMaster-Pro`、`html-to-docx` 等）属别的项目，不应挪用；
- 无本地 python 镜像可离线复用。

按操作规则，`pip install` 属需授权操作，故**未自行安装**。

**需要的授权（二选一）**：

```bash
# 方案 A：项目本地 venv（推荐，完全隔离、可整体删除）
cd agent-platform
python -m venv .venv && .venv\Scripts\activate
pip install -e ".[dev]"
```

```
# 方案 B：全程容器内执行，宿主零改动
# 用一次性 python:3.11 容器挂载代码跑冒烟，跑完即删
```

授权后可在数分钟内补完门禁 2/3/4。

---

## 5. 当前环境状态

六服务**仍在运行**（便于门禁 2/3/4 授权后立即续跑，也便于架构侧复核）。

资源占用约 3–4GB 内存。如暂不需要，一条命令即可停：

```bash
docker compose -f deploy/docker-compose.yml down        # 保留数据卷
docker compose -f deploy/docker-compose.yml down -v     # 连数据卷一起清
```

> 已确认与既有容器（`bidmaster-postgres`、`nexus3`）无端口冲突，未触碰它们。
