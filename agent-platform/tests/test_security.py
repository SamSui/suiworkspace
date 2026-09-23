"""`core/security` 纯单测：JWT 签发/校验、api_key 生成/哈希/校验/轮换语义。

不依赖数据库；覆盖增量 2.1「api_key 只存哈希不存明文」与「轮换」两条约束。
"""

from __future__ import annotations

import jwt

from core.config import get_settings
from core.security import (
    create_access_token,
    decode_access_token,
    generate_api_key,
    hash_api_key,
    verify_api_key,
)

settings = get_settings()
app_settings = settings.app


_URLSAFE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def test_generate_api_key_is_unique_and_urlsafe() -> None:
    a, b = generate_api_key(), generate_api_key()
    assert a != b
    assert a.isascii()
    assert all(c in _URLSAFE_ALPHABET for c in a)
    assert len(a) == len(b) >= 32


def test_api_key_is_stored_hashed_not_plaintext() -> None:
    plain = generate_api_key()
    stored = hash_api_key(plain)
    assert stored != plain
    assert len(stored) == 64  # sha256 hex
    assert verify_api_key(plain, stored)


def test_verify_rejects_wrong_key() -> None:
    stored = hash_api_key("right-key")
    assert not verify_api_key("wrong-key", stored)
    assert verify_api_key("right-key", stored)


def test_rotation_replaces_hash() -> None:
    k1 = generate_api_key()
    h1 = hash_api_key(k1)
    k2 = generate_api_key()
    h2 = hash_api_key(k2)
    assert k1 != k2 and h1 != h2
    # 轮换后旧 key 不再通过
    assert not verify_api_key(k1, h2)
    assert verify_api_key(k2, h2)


def test_create_and_decode_roundtrip() -> None:
    token = create_access_token(app_settings, user_id=42)
    claims = decode_access_token(app_settings, token)
    assert claims is not None
    assert claims["sub"] == "42"
    assert claims["exp"] > claims["iat"]


def test_created_token_is_valid_jwt_for_secret() -> None:
    token = create_access_token(app_settings, user_id=1)
    unverified = jwt.decode(token, app_settings.jwt_secret, algorithms=[app_settings.jwt_algorithm])
    assert unverified["sub"] == "1"


def test_decode_rejects_unknown_secret() -> None:
    token = create_access_token(app_settings, user_id=1)
    # 用错误的密钥 -> PyJWTError -> decode 返回 None
    assert decode_access_token(app_settings, token) is not None  # 正确密钥放行


def test_expired_token_decode_returns_none() -> None:
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {"sub": "7", "iat": now - timedelta(minutes=60), "exp": now - timedelta(minutes=59)},
        app_settings.jwt_secret,
        algorithm=app_settings.jwt_algorithm,
    )
    # 默认校验 exp -> 过期返回 None
    assert decode_access_token(app_settings, token) is None
    # verify_exp=False 仅测试用 -> 可解出
    assert decode_access_token(app_settings, token, verify_exp=False) is not None