"""统一异常层次。

目的：让中间件能把任意异常映射成稳定的 HTTP 语义，且不泄漏内部细节。
"""

from __future__ import annotations


class AppError(Exception):
    """业务异常基类。`code` 是稳定的机器可读标识，`status_code` 决定 HTTP 语义。"""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str = "", *, detail: object | None = None) -> None:
        super().__init__(message or self.__class__.__doc__ or self.code)
        self.message = message or self.code
        self.detail = detail


class ConfigError(AppError):
    """配置缺失或非法。"""

    status_code = 500
    code = "config_error"


class StorageUnavailable(AppError):
    """依赖存储不可用（连接失败 / 探活失败）。"""

    status_code = 503
    code = "storage_unavailable"


class AuthenticationError(AppError):
    """未认证或凭证无效。"""

    status_code = 401
    code = "unauthenticated"


class PermissionDenied(AppError):
    """已认证但无权限——查询路径强制校验失败时抛出。"""

    status_code = 403
    code = "permission_denied"


class NotFound(AppError):
    """资源不存在。"""

    status_code = 404
    code = "not_found"


class Conflict(AppError):
    """资源冲突（如用户名重复等唯一性约束）。"""

    status_code = 409
    code = "conflict"


class RateLimited(AppError):
    """触发限流。"""

    status_code = 429
    code = "rate_limited"


class ValidationError(AppError):
    """请求参数不合法。"""

    status_code = 422
    code = "invalid_argument"


class UpstreamError(AppError):
    """下游服务（编排服务 / LLM / Embedding）失败。"""

    status_code = 502
    code = "upstream_error"


class NotImplementedYet(AppError):
    """所属增量尚未实现——占位路由显式返回，绝不伪装成成功。"""

    status_code = 501
    code = "not_implemented"

    def __init__(self, increment: str, message: str = "") -> None:
        super().__init__(message or f"该能力计划在 {increment} 交付")
        self.increment = increment
        self.detail = {"increment": increment}
