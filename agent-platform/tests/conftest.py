"""pytest 公共配置：把项目根加入 sys.path，便于 `core` / `api` 导入。"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# 测试统一用一个 ≥32 字节的强密钥，避免 pyjwt 的 InsecureKeyLength 告警；
# 覆盖默认的 `change-me-in-production`（生产仍由 APP_JWT_SECRET 显式注入）。
os.environ.setdefault("APP_JWT_SECRET", "unit-test-secret-0123456789abcdef-0123456789")
os.environ.setdefault("APP_ENV", "dev")
