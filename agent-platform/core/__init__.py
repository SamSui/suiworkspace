"""core — 两个进程组共用的基础能力：配置、日志、异常、存储层客户端。

刻意**不在包级 eager 导入** `core.config`：那会让任何 `import core.exceptions`
都强制依赖 pydantic-settings。需要配置请显式 `from core.config import get_settings`。
"""
