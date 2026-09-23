"""分层配置：pydantic-settings，按组件分节，环境变量前缀隔离。

用法::

    from core.config import get_settings
    settings = get_settings()
    settings.mysql.dsn          # mysql+asyncmy://...
    settings.redis.url

约定：每个分节一个 env 前缀（MYSQL_ / REDIS_ / MILVUS_ / ES_ / APP_ ...），
`.env` 为开发默认，生产以容器环境变量覆盖。
"""

from __future__ import annotations

from functools import lru_cache
from urllib.parse import quote_plus

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = ".env"


class _Section(BaseSettings):
    """分节基类：统一 .env 来源与忽略未声明变量的行为。"""

    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore", case_sensitive=False)


class AppSettings(_Section):
    model_config = SettingsConfigDict(
        env_prefix="APP_", env_file=_ENV_FILE, extra="ignore", case_sensitive=False
    )

    env: str = "dev"
    debug: bool = False
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    langgraph_service_url: str = "http://127.0.0.1:8100"
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 120
    # api_key HMAC-校验密钥：与 jwt_secret 分离，避免一把密钥既签 JWT 又签 api_key
    api_key_secret: str = "change-me-in-production-api-key"

    @property
    def is_prod(self) -> bool:
        return self.env.lower() in {"prod", "production"}


class MySQLSettings(_Section):
    model_config = SettingsConfigDict(
        env_prefix="MYSQL_", env_file=_ENV_FILE, extra="ignore", case_sensitive=False
    )

    host: str = "127.0.0.1"
    port: int = 3306
    user: str = "agent"
    password: str = "agent_pwd"
    database: str = "agent_platform"
    pool_size: int = 10
    max_overflow: int = 20
    pool_recycle: int = 1800
    pool_timeout: int = 30
    echo: bool = False

    @property
    def dsn(self) -> str:
        """异步驱动 DSN（asyncmy）。密码做 URL 编码，避免特殊字符破坏 DSN。"""
        return (
            f"mysql+asyncmy://{quote_plus(self.user)}:{quote_plus(self.password)}"
            f"@{self.host}:{self.port}/{self.database}?charset=utf8mb4"
        )


class RedisSettings(_Section):
    model_config = SettingsConfigDict(
        env_prefix="REDIS_", env_file=_ENV_FILE, extra="ignore", case_sensitive=False
    )

    url: str = "redis://127.0.0.1:6379/0"
    max_connections: int = 50
    socket_timeout: float = 5.0
    socket_connect_timeout: float = 5.0

    # --- key 前缀：同一实例按用途隔离（对应设计文档 §2.6）---
    prefix_session: str = "session:"
    prefix_chat_ctx: str = "chat:ctx:"
    prefix_checkpoint: str = "checkpoint:"
    prefix_retrieval_cache: str = "cache:ret:"
    prefix_rate_limit: str = "rate:"
    prefix_lock: str = "lock:"
    prefix_ingest_queue: str = "queue:ingest:"


class MilvusSettings(_Section):
    model_config = SettingsConfigDict(
        env_prefix="MILVUS_", env_file=_ENV_FILE, extra="ignore", case_sensitive=False
    )

    host: str = "127.0.0.1"
    port: int = 19530
    user: str = ""
    password: str = ""
    collection: str = "knowledge_chunks"
    alias: str = "active_collection"
    dim: int = 768
    timeout: float = 10.0

    @property
    def uri(self) -> str:
        return f"http://{self.host}:{self.port}"


class ESSettings(_Section):
    model_config = SettingsConfigDict(
        env_prefix="ES_", env_file=_ENV_FILE, extra="ignore", case_sensitive=False
    )

    hosts: str = "http://127.0.0.1:9200"
    index: str = "kb_chunks"
    request_timeout: float = 10.0
    max_retries: int = 2

    @property
    def host_list(self) -> list[str]:
        """支持逗号分隔多节点：ES_HOSTS=http://a:9200,http://b:9200"""
        return [h.strip() for h in self.hosts.split(",") if h.strip()]


class IngestSettings(_Section):
    model_config = SettingsConfigDict(
        env_prefix="INGEST_", env_file=_ENV_FILE, extra="ignore", case_sensitive=False
    )

    queue: str = "queue:ingest:doc"
    max_tries: int = 3
    job_timeout: int = 1800
    chunk_tokens: int = 250
    chunk_overlap: int = 100
    max_document_mb: int = 200
    allowed_extensions: str = ".pdf,.docx,.md,.txt"

    @property
    def allowed_ext_set(self) -> set[str]:
        return {e.strip().lower() for e in self.allowed_extensions.split(",") if e.strip()}


class Settings(_Section):
    """聚合根。各分节自持 env 前缀，故此处不设统一前缀。"""

    app: AppSettings = Field(default_factory=AppSettings)
    mysql: MySQLSettings = Field(default_factory=MySQLSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    milvus: MilvusSettings = Field(default_factory=MilvusSettings)
    es: ESSettings = Field(default_factory=ESSettings)
    ingest: IngestSettings = Field(default_factory=IngestSettings)

    @field_validator("app")
    @classmethod
    def _guard_prod_secret(cls, value: AppSettings) -> AppSettings:
        """生产环境不允许沿用示例 JWT/API-Key 密钥——启动即失败，而不是带着弱密钥上线。"""
        if value.is_prod and value.jwt_secret == "change-me-in-production":
            raise ValueError("APP_ENV=prod 时必须显式设置 JWT_SECRET")
        if value.is_prod and value.api_key_secret == "change-me-in-production-api-key":
            raise ValueError("APP_ENV=prod 时必须显式设置 API_KEY_SECRET")
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程内单例。测试可用 get_settings.cache_clear() 重置。"""
    return Settings()
