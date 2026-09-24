"""可观测性模块（增量 5.3 / 5.4）。

在本仓库既有 `langgraph_service.metrics.py`（进程内计数/直方图）之上，叠加两层
标准化可观测：

- `observability.otel`  —— OpenTelemetry spans + 现有的 `trace_id` request/response 头
  打通，一次请求在链路图可完整、带起止还原 `网关 → 编排 → 存储/LLM`；
- `observability.prom` —— Prometheus 计数器 / 直方图（缓存命中率、检索 P99、错误率、
  依赖耗时），并提供一个 `prometheus_client` 的 registry 与取样/导出入口。

约束（对齐增量五验收口径与“未验证契约不引入”）：
- 仅依赖 `pyproject.toml` 的 `observability` extra 已声明版本；未安装该 extra 时
  本模块的**核心对象**（幂等的 no-op）仍可安全导入，调用方不因此报错。
- OTel 默认用**内存 Span 收集器**（`InMemorySpanSink`）与 console 导出，无需外部
  OTLP collector 即可本地验证；配置了 `OTEL_EXPORTER_OTLP_ENDPOINT` 时才真正上传。
- Prometheus 默认用进程内 registry 直出；部署时由 `deploy/prometheus/prometheus.yml`
  scrape 各进程 `/metrics`。
"""

from __future__ import annotations

__version__ = "0.1.0"