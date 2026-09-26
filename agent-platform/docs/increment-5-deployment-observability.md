# 增量 5 · 部署与可观测（5.1–5.5）设计与接口记录

> 编制：随研发 ｜ SUIG-21 ｜ 依据：`increment-1-breakdown.md`（增量 5）+ 架构裁决（不重开契约）
> 作用域：容器化、K8s 编排、OpenTelemetry 埋点、Prometheus/Grafana 指标告警、并发压测容量基线。

---

## 1. 达成口径（验收对照）

| # | 子任务 | 落地文件 | 验收证据 |
|---|---|---|---|
| 5.1 | 容器化 | `deploy/Dockerfile.{api,langgraph,ingest}` + `.dockerignore` | 三份 Dockerfile 结构完整、healthcheck 对齐各服务健康探针；**构建**因宿主无 docker daemon 登记未跑（见 §7）。 |
| 5.2 | K8s 编排 | `deploy/k8s/{namespace,api-configmap,api-hpa,langgraph,ingest}.yaml` | 8 份 K8s 文档经结构校验通过；滚动更新策略明确（网关 maxUnavailable=0/maxSurge=1）；`kubectl` apply 需集群，登记未跑。 |
| 5.3 | OpenTelemetry 埋点 | `observability/{otel,prom}.py` + `api/middlewares/observability.py` + 节点/存储/LLM 打点 | 单测 `tests/test_observability.py`（链路 trace_id 贯穿 + 各环节 span）全部 PASS；`trace_id` 沿用现有 `x-request-id` 贯穿。 |
| 5.4 | 指标与告警 | `observability/prom.py`（`/metrics`）、`deploy/prometheus/*`、`deploy/grafana/*` | 单测覆盖缓存命中率/检索 P99/错误率/依赖耗时 + `/metrics` 文本导出；Grafana 面板与 5 条告警规则给出。 |
| 5.5 | 压测与容量 | `scripts/load_test.py` + 本文档 §6 | 离线跑通取得基线（逻辑下限）；真实栈并发压测依赖六服务，未跑登记。 |

## 2. 不重建契约

沿用已冻结架构裁决与内部契约；本增量**不**引入新的业务契约、不改变：
- 内部 HTTP+SSE（裁决 #1）、Checkpoint 只接 RedisSaver（#2）、双写状态机（#3）、查询路径权限（#4）。
- `trace_id`：仍以 `core/logging.py` 的 `x-request-id`（`TRACE_ID_HEADER`）为唯一贯穿键，
  本增量把它桥接进 OTel，不另起第二套 trace 语义。
- tokenizer/embedding 决策（增量 4 冻结）；镜像内配置与 `core/config.py` 各分节 env 前缀一致。

## 3. OpenTelemetry 埋点设计（5.3）

### 3.1 分层
- **入口**：新增 `ObservabilityMiddleware`（放在 `TraceMiddleware` 内侧、`Auth/RateLimit` 外层）；
  每请求开一个根 span `http.request`，并记请求量/在途数/错误率。
- **编排节点**：`retrieve_node` 包 `node.retrieve` span，并对缓存命中/未命中与检索总时延打点；
  Milvus/ES 检索包 `dependency.milvus.search` / `dependency.es`、重排包 `rerank`，
  三者经 `rt_span.child(...)` **归为 `node.retrieve` 的子 span**（SUIG-28 ③ 修正：不再平级游离），
  链路以 `node.retrieve → {dependency.milvus.search | dependency.es | rerank}` 呈现。
- **LLM**：`llm/client.py` 完成一次流后记依赖耗时（`target=llm`）。
- **存储**：`core/storage/{es,milvus}.py` 在 search/insert/bulk_index 统计耗时。

### 3.2 `trace_id` 贯穿
`ObservabilityMiddleware` 根 span 在 **`TraceMiddleware` 已 set trace_id 之后**再取
`get_trace_id()`，保证「日志 trace_id == OTel trace_id == 响应头 x-request-id」三者一致；
同一请求在链路图可完整还原 `网关 → 编排 → 存储/LLM`（`format_chain` 提供缩进布局）。

### 3.3 可插拔导出
- 无 `OTEL_EXPORTER_OTLP_ENDPOINT` → 进程内 `InMemorySpanSink`（本地可验证、单测断言）；
- 配了 OTLP endpoint → 走 OTLP 导出（生产接 collector）。
- SDK 未安装时全部 no-op，不阻断业务——满足「未验证第三方契约不引入」。

## 4. 指标与告警（5.4）

Prometheus 指标（前缀 `kb_`，由 `observability/prom.Metrics` 统一打点，进程内镜像计数
可复位供测试/报告）：

| 指标 | 语义 | 验收对位 |
|---|---|---|
| `kb_cache_hits_total` / `kb_cache_misses_total` | 缓存命中/未命中 | 缓存命中率 |
| `kb_retrieval_seconds`（直方图） | 检索总耗时 | 检索 P99 |
| `kb_errors_total{phase}` | 网关/编排/存储层错误 | 错误率 |
| `kb_dependency_seconds{target,name}` | LLM/Milvus/ES/网关耗时 | 依赖可视化 |
| `kb_requests_total{route}` / `kb_requests_inflight` | 吞吐 / 在途 | 流量 + HPA 参考 |

导出：`GET /metrics`（`api` 与 `langgraph_service` 均挂载；`/metrics` 加入 `PUBLIC_PATHS` 免鉴权）。
**ingest worker 可观测口径（SUIG-28 ②）**：arq 常驻进程无 HTTP `/metrics`、无 K8s Service
（liveness 走 exec `ps`），故不声明 `agent-ingest` job
避免 Prometheus 对空 service 抓取而静默为零；其流量/可见性由网关聚合 QPS 与
`document.status` 状态机在查询侧兜底呈现。
桥接：既有 `langgraph_service.metrics` 直方图经 `kb_inproc_metric_seconds` 并入 Prometheus，
避免新埋点可见、旧埋点不可见。
Grafana 面板 `deploy/grafana/agent-platform-dashboard.json`：缓存命中率、检索 P50/P99
（P50、P99 两条 series，SUIG-28 ①）、错误率、依赖耗时四面板。
告警 `deploy/prometheus/alerts.yml` 5 条（缓存命中率<30%、检索 P99>1s、网关错误率>5%、
编排错误率、LLM 耗时>5s）。

## 5. K8s 编排要点（5.2）

- 网关无状态可弹性：`api-hpa.yaml` 提供 CPU-based HPA（70% 均用率触发）+ QPS external
  指标（需 Prometheus Adapter）注释版，min=2 / max=8；滚动更新 `maxUnavailable=0 + maxSurge=1`。
- `langgraph` 有状态多实例（Checkpoint 在 Redis）滚动更新不中断；liveness/readiness 均探 `/healthz`。
- `ingest` 单 worker 串行双写（裁决 #3），副本固定 1，`Recreate` 更新避免双写并发。
- 探针：`api` liveness `/healthz/live`（探进程）、readiness `/healthz/ready`（探依赖聚合）。

## 6. 压测与容量基线（5.5）

**方法**：`scripts/load_test.py`。离线模式复用检索桩跑并发协程，得 Python 侧吞吐与分位；
以 Little's Law（N=λ·W，P99 预算 1s）反推单副本并发。

**离线实测（本工作区，零依赖，2.5.5 增量内执行）**：

```
mode: local   total: 200   concurrency: 20
throughput_rps: 641.0
p50_s: 0.031   p95_s: 0.032   p99_s: 0.032
estimated_single_replica_concurrency: ~641
```

> 解读：此为**纯逻辑下限**（不含 Milvus/ES/LLM-RPC 与网络）。真实栈下需把平均耗时
> 换成实测值再代入。**注意**：离线合成时延把 P50/P99 压得很扁平（≈合成 20ms 附近），
> 真实检索因 Milvus-RRPC + Rerank 会显著更宽，须以在线实测覆盖；下表给出待补项。

**建议（初步，待真实栈复跑）**：
- 单副本承载并发：以 P99≤1s 为 SLA，逻辑下限约 600+ QPS / 单副本；折合实时对话
  场景受编排/LLM 支配需降权。
- 扩容阈值：网关 CPU 均用率 ≥70%（HPA 70%）→ 触发扩一件；P99>1s 持续 5 分钟 →
  触发告警并人工确认扩容。

## 7. 未跑项登记（如实）

| 项 | 原因 | 本地近似验证 |
|---|---|---|
| 容器镜像 `docker build` | 宿主 docker daemon 未运行 | Dockerfile 结构 / healthcheck 路径 / `.dockerignore` 静态核对；`api`/`langgraph`/`ingest` healthcheck 均对应当前 `/healthz`、`/healthz/live` 路由 |
| `kubectl apply --dry-run` | 无 K8s 集群（kubectl client 有，server 连接拒绝） | 8 份 manifest 经 `测试` YAML 结构校验（kind/api/name/selector/probe/strategy 全过）；`deploy/k8s/*` |
| 真实 K8s 滚动更新 | 无集群 | 策略字段（maxUnavailable/surge）按最佳实践给出并通过结构校验 |
| 在线六存储压测 | 无 docker 六服务栈 | 离线逻辑基线已跑通（§6）；`--svc-url` 路径在 `ingest`/`langgraph` 服务可跑后复跑 |
| OTLP collector 真实上报 | 无外部 collector | 内存 sink 单测覆盖；`OTEL_EXPORTER_OTLP_ENDPOINT` 配置生效即接 |

## 8. 运行说明

```bash
cd agent-platform
# 单元测试（含 12 项可观测单测，全离线）
.venv/Scripts/python.exe -m pytest -q -k "not integration"

# 启动后探活 & 指标
curl http://127.0.0.1:8000/healthz/live      # liveness
curl http://127.0.0.1:8000/metrics           # Prometheus 文本指标（公开）

# 容量基线（离线）
python scripts/load_test.py --concurrency 20 --total 200
# 在线（六服务栈就绪后）
python scripts/load_test.py --svc-url http://127.0.0.1:8100
```

## 9. 编码自测清单

- [ ] 网关 /metrics 返回文本含 kb_ 指标（`tests/test_observability.py::test_api_metrics_and_spans_end_to_end`）
- [ ] 检索缓存命中/未命中计数与命中率可观测
- [ ] 检索 P99 可从直方图取（单测断言）
- [ ] 链路 span 父子 / trace_id 一致（`test_span_child_chain_reconstruct`）
- [ ] `/metrics` 加入 PUBLIC_PATHS 免鉴权
- [ ] 既有 79 项单测 + 11 项可观测单测全绿