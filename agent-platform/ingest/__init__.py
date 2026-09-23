"""ingest — 进程组 ②：文档摄入异步链路。

CPU 密集的解析 / 切分 / Embedding 全部在 worker 内执行，绝不进 API 请求路径
（设计文档 §8）。
"""

__version__ = "0.1.0"
