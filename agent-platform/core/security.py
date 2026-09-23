"""安全原语：JWT 签发/校验 + api_key 生成/哈希/校验。

约束（架构放行 2.1 重述）：
- 不引入未验证的新第三方契约——`pyjwt` 已是主依赖，api_key 哈希走标准库
  `hashlib` / `secrets`。下方 `api_key` 哈希是确定性 SHA-256：入参是 secrets 生成的
  高熵随机 key，无字典爆破风险，无需慢哈希；轮换即整行替换（设计文档 §5.2 iron rule）。
- api_key 只存哈希、不存明文；明文仅在创建/轮换成功时回显一次给调用方。
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from core.config import AppSettings

# api_key 是前端/客户端持有的永久凭证，统一 40 字符 URL-safe base64（256 bit 熵）。
API_KEY_BYTES = 32
API_KEY_LENGTH = 40


def generate_api_key() -> str:
    """生成新 api_key（明文）。调用方负责只回显一次、并只存 `hash_api_key` 的产物。"""
    return secrets.token_urlsafe(API_KEY_BYTES)[:API_KEY_LENGTH]


def hash_api_key(plain: str) -> str:
    """对明文 api_key 做确定性 SHA-256 哈希（入库值）。"""
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def verify_api_key(plain: str, stored_hash: str) -> bool:
    """恒时比对：明文哈希后与库中存储值比对，避免时序侧信道。"""
    return secrets.compare_digest(hash_api_key(plain), stored_hash)


def create_access_token(
    settings: AppSettings, user_id: int, *, extra_claims: dict[str, Any] | None = None
) -> str:
    """签发短命 JWT（登录凭证）。

    `sub` 固定为用户 id；`exp` 滚动刷新于签发时刻 + `jwt_expire_minutes`。
    """
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(
    settings: AppSettings, token: str, *, verify_exp: bool = True
) -> dict[str, Any] | None:
    """校验并解码 JWT。

    - 过期 / 签名不合法 / 结构非法 → 返回 None（中间件据此回 401）。
    - `verify_exp=False` 供测试构造各类负面用例（过期 token）使用，生产恒为 True。
    """
    try:
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"verify_exp": verify_exp},
        )
    except jwt.PyJWTError:
        return None


__all__ = [
    "create_access_token",
    "decode_access_token",
    "generate_api_key",
    "hash_api_key",
    "verify_api_key",
]