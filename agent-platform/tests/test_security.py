"""core.security 单测（增量 2 · 2.1）。

不回 DB——纯内存验证 JWT 签发/校验与 api_key 哈希/比对/轮换。
"""

from __future__ import annotations

import jwt
import pytest

from core.config import Settings
from core.exceptions import AuthenticationError
from core.security import (
    create_access_token,
    generate_api_key,
    hash_api_key,
    rotate_api_key,
    verify_access_token,
    verify_api_key,
)
from tests._constants import TEST_API_KEY_SECRET, TEST_JWT_SECRET


@pytest.fixture
def app_settings() -> Settings:
    s = Settings()
    s.app.jwt_secret = TEST_JWT_SECRET
    s.app.api_key_secret = TEST_API_KEY_SECRET
    return s.app


# ---------------------------------------------------------------------------
# JWT 签发 / 校验
# ---------------------------------------------------------------------------

def test_create_and_verify_roundtrip(app_settings) -> None:
    token = create_access_token(7, app_settings)
    claims = verify_access_token(token, app_settings)
    assert claims["sub"] == "7"
    assert claims["type"] == "access"
    assert "exp" in claims and "iat" in claims


def test_expired_token_raises(app_settings) -> None:
    # 手工构造一个已过期的 token，模拟真实时间流逝
    import datetime as dt

    now = dt.datetime.now(dt.timezone.utc)
    token = jwt.encode(
        {
            "sub": "7",
            "iat": now - dt.timedelta(hours=2),
            "exp": now - dt.timedelta(hours=1),
            "type": "access",
        },
        TEST_JWT_SECRET,
        algorithm="HS256",
    )
    with pytest.raises(AuthenticationError):
        verify_access_token(token, app_settings)


def test_tampered_token_raises(app_settings) -> None:
    token = create_access_token(7, app_settings)
    # 随便改动 signature 尾字符，破坏签名
    tampered = token[:-4] + "AAAA"
    with pytest.raises(AuthenticationError):
        verify_access_token(tampered, app_settings)


def test_wrong_secret_raises(app_settings) -> None:
    token = jwt.encode({"sub": "7"}, "different-secret-different-secret-zz", algorithm="HS256")
    with pytest.raises(AuthenticationError):
        verify_access_token(token, app_settings)


# ---------------------------------------------------------------------------
# api_key 哈希 / 比对 / 生成 / 轮换
# ---------------------------------------------------------------------------

def test_hash_is_deterministic_and_not_plaintext(app_settings) -> None:
    raw = "ap_secret-key-123"
    h = hash_api_key(raw, app_settings)
    assert h != raw  # 绝不存明文
    assert len(h) == 64  # sha256 hex
    assert h == hash_api_key(raw, app_settings)  # 确定性


def test_verify_api_key_true_and_false(app_settings) -> None:
    raw = "ap_round-trip-key"
    h = hash_api_key(raw, app_settings)
    assert verify_api_key(raw, h, app_settings) is True
    assert verify_api_key("ap_wrong-key", h, app_settings) is False


def test_generate_api_key_format() -> None:
    k1 = generate_api_key()
    k2 = generate_api_key()
    assert k1.startswith("ap_")
    assert k1 != k2  # 随机性
    assert len(k1) > 8


def test_rotate_produces_new_usable_pair(app_settings) -> None:
    p1, h1 = rotate_api_key(app_settings)
    p2, h2 = rotate_api_key(app_settings)
    assert p1 != p2
    assert h1 != h2
    assert verify_api_key(p1, h1, app_settings) is True
    assert verify_api_key(p2, h2, app_settings) is True