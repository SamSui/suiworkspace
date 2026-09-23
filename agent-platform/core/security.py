"""安全原语：JWT 签发/校验 + api_key 哈希/比对/生成（增量 2 · 2.1）。

职责边界：
- JWT 只做**认证**（你是谁），不做授权；授权在 `api.deps.require_kb_access`（裁决 #4）。
- api_key 列只存哈希不存明文（`user.api_key` 的硬约束），生成时明文只对外展示一次。

方案取舍：
- api_key 用 **HMAC-SHA256**（以服务端密钥为 key）而非裸 SHA-256——即使库被脱走，
  没有密钥也无法离线爆破（HMAC 抗离线字典/彩虹表，裸 sha256 可暴力破解）。
  仍落为 64 位 hex，兼容 `VARCHAR(64)` 列。
- JWT 签发/校验统一走本模块；`api/middlewares/auth.py` 只复用 `verify_access_token`，
  保证"认证逻辑只有一份"。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from core.config import AppSettings
from core.exceptions import AuthenticationError

# 生成 api_key 的前缀与长度：`ap_` + 32 字节 url-safe（≈43 base64 字符）。
_API_KEY_PREFIX = "ap_"
_API_KEY_RANDOM_BYTES = 32
# HMAC 摘要直接用 64 位 hex（sha256），不必再截断
_HASH_BYTES = hashlib.sha256().digest_size


def _api_key_hmac_key(settings: AppSettings) -> bytes:
    """api_key 检校验用的 HMAC 密钥（服务端 secret UTF-8 化）。"""
    return settings.api_key_secret.encode("utf-8")


# ---------------------------------------------------------------------------
# JWT 签发 / 校验
# ---------------------------------------------------------------------------

def create_access_token(
    user_id: int,
    settings: AppSettings,
    *,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """签发访问令牌。

    - `sub` = str(user_id)，供中间件写进 scope.state，作为当前用户身份；
    - `exp`/`iat` 由 `jwt_secret`/`jwt_algorithm`/`jwt_expire_minutes` 决定。
    """
    now = datetime.now(timezone.utc)
    claims: dict[str, Any] = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
        "type": "access",
    }
    if extra_claims:
        claims.update(extra_claims)
    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def verify_access_token(token: str, settings: AppSettings) -> dict[str, Any]:
    """校验并返回 claims。

    未认证 / 过期 / 被篡改统一抛 `AuthenticationError`（中间件映射为 401），
    不向调用方暴露是"无效"还是"过期"，避免细节泄漏。
    """
    try:
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.PyJWTError as exc:  # 含 ExpiredSignatureError / InvalidTokenError
        raise AuthenticationError("token 无效或已过期") from exc


# ---------------------------------------------------------------------------
# api_key 哈希 / 比对 / 生成 / 轮换
# ---------------------------------------------------------------------------

def hash_api_key(raw_key: str, settings: AppSettings) -> str:
    """对明文 api_key 生成 HMAC-SHA256 摘要（只入库此值，不存明文）。"""
    return hmac.new(
        _api_key_hmac_key(settings), raw_key.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def verify_api_key(raw_key: str, stored_hash: str, settings: AppSettings) -> bool:
    """常量时间比对，防时序探测。"""
    expected = hash_api_key(raw_key, settings)
    return hmac.compare_digest(stored_hash, expected)


def generate_api_key() -> str:
    """生成新的明文 api_key。明文仅在此刻返回给调用方，之后只存 hash。"""
    random_part = secrets.token_urlsafe(_API_KEY_RANDOM_BYTES)
    return f"{_API_KEY_PREFIX}{random_part}"


def rotate_api_key(settings: AppSettings) -> tuple[str, str]:
    """轮换：返回 (新明文, 新哈希)。调用方把哈希写库，明文只示一次。"""
    plain = generate_api_key()
    return plain, hash_api_key(plain, settings)


__all__ = [
    "create_access_token",
    "generate_api_key",
    "hash_api_key",
    "rotate_api_key",
    "verify_api_key",
    "verify_access_token",
]