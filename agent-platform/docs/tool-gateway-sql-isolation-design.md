# Tool 网关 · SQL / 代码执行隔离设计（P1.3 · SUIG-33）

> 状态：设计定稿，`tool_gateway/executors.py::SqlToolExecutor` 已按此落地（默认禁 +
> 只读白名单 + 最小权限）；代码沙箱（第二类）本设计给出可行方案，落地依赖资源授权，
> 见「未落点」。
> 依赖：统一准入链在 `tool_gateway/gateway.py::ToolGateway.call`（授权→校验→限流→
> 执行→审计）。

## 1. 目标与非目标

**目标**
- SQL / 任意代码执行的**默认拒绝（default-deny）**，绝不在未知白名单/隔离缺失时放行。
- 放行路径上执行必须具备**最小权限** + **隔离环境**。
- 每一次放行/拒绝都可审计（`audit_log`：`tool.call` / `tool.denied`）。

**非目标**
- 本设计不承诺对恶意攻击的全防御；它把「任意可写/任意主机」这条最宽广的攻击面收窄
  为「只读白名单 + 受控账号/沙箱」，把爆炸半径压到最小。

## 2. 信任模型与默认拒绝

- 所有工具在 `ToolRegistry` 里**默认无权**（`allow` 白名单为空 ⇒ 一律拒）。
- 授权 = 显式 `approve(actor, tool)`（未来由 P1.1 `tool_permission` 表驱动）。
- SQL/代码类工具在授权之上还有两次硬门：
  1. `ToolSpec.isolated` 必须为 `True`（契约铁律，非 `isolated` 一律拒）；
  2. 执行器无受限只读 DSN / 无沙箱配置 → 直接拒。

## 3. SQL 隔离（已落地：`SqlToolExecutor`）

### 3.1 最小权限（least privilege）
- 只用 **每工具专属的受限只读账号**（`sql_dsns: {tool_name: dsn}`），绝不使用 root /
  公共写连接。
- 账号在 DB 侧仅授 `SELECT`（对单库/白名单库），该账号自身只能做只读。
- 执行一律在 **只读事务**（`SET TRANSACTION READ ONLY`）内进行，DB 引擎层兜底写操作
  失败。

### 3.2 语句静态白名单
入库 SQL 逐个字段静态扫描，**默认放行仅限只读动词**：
```python
ALLOWED_VERBS = {"select", "with", "show", "explain"}
FORBIDDEN_SUBSTR = {insert, update, delete, drop, alter, create,
                    truncate, grant, revoke, load, call, exec,
                    procedure, trigger, "--", "/*", "*/"}
```
- 首动词不在 `ALLOWED_VERBS` → 拒；
- 出现任一禁词 → 拒；
- 通过后才进入受控只读连接执行。

### 3.3 运行时流
`SqlToolExecutor.execute()`：
1. `dsn = dsns[tool]`，缺 → **返回「默认禁」失败**；
2. `spec.isolated or` → 拒；
3. `sql` 缺失/非字符串 → 拒；
4. `_static_check` 不通过 → **返回「静态白名单拒绝」**；
5. 只读事务执行，取前 N 行，异常 → 执行失败（可审计）。

## 3.4 最小权限落库的配套（运维，待 DB 侧落实）
```sql
-- 每只读账号仅授所需库(或全部库)的 SELECT
CREATE USER 'tool_report_ro'@'%' IDENTIFIED BY '<pw>';
GRANT SELECT ON `report_db`.* TO 'tool_report_ro'@'%';
GRANT SET_USER_ID /* 不需要 */ ;
FLUSH PRIVILEGES;
```
> 该 DSN 由运行环境注入（`TOOL_SQL_DSN_<tool>`），不硬编码进代码/镜像。

## 4. 代码执行（任意代码）——隔离环境设计（默认拒 + 可选沙箱）

> 本轮**未接入真实代码执行**；设计为下一层(安全(用户执行)或生产授权)提供明确治理。

### 4.1 默认拒绝
- 代码执行工具默认不注册 / 未授权；且要求 `ToolSpec.isolated=True` 与 `runtime` sandbox
  名字匹配。
- 无已配置的沙箱后端 → 一律拒。

### 4.2 两层可选的隔离执行器
| 层 | 后端 | 隔离手段 | 成本/延迟 |
|---|---|---|---|
| L1 | 进程内受限解释器（只读环境，CPU/内存配额） | 解释器级 + asyncio 限额 | 低 |
| L2 | OCI 容器（`--read-only --cap-drop all --network=none`，超时强杀） | 内核隔离 | 中 |
| L3 | 独立沙箱 API（gVisor） | 更强内核隔离 | 高 |

### 4.3 代码沙箱的强制约束（无论哪层）
- 无出网（对绝大多数代码执行请求，`network=none`）；
- 只读文件系统（不许落盘到宿主）；
- CPU / 内存 / 时钟（超时）配额；
- 超时即强杀并记为失败；
- 输出仅回传 stdout 截断（小容量）+ 退出码，不泄露环境/密钥；
- 每次执行的依赖都冻结在镜像/锁文件，杜绝运行时拉取任意依赖。

## 5. 安全基线核对（对应验收口径）

| 口径 | 落点 |
|---|---|
| SQL 默认禁 | 无 DSN / `isolated=False` → 拒；未授权 → 拒 |
| 白名单内最小权限 | per-tool 只读账号 + 只读事务 + 动词白名单 |
| 隔离执行 | 只读事务隔离；代码沙箱（L2+）为本设计治理 |

## 6. 审计
- 每次放行/拒绝都写 `audit_log`：动作 `tool.call`/`tool.denied`/`tool.ratelimited`，
  actor/凭验 (actor_id) 记为调用方，diff_context 内含 tool / 拒绝原因 / 执行 ok/error。

## 7. 未落地 / 需授权决策
- 下一步 NODE（代码执行）：选择 L1/L2 作为首生产后端（资源、隔离等级需 owner 决策，
  且涉及资源成本）；
- per-tool/库的受限只读 DB 账号与 DDL 需要 DBA 环节（本轮给 `tool_report_ro` 样例，
  未实际建库）。